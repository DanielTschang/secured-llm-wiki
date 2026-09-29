"""Runs tests/leak/infra/in_cluster inside the cluster as the ingest-worker ServiceAccount.

The Job gets exactly what the ingest broker gets: the kc-ingest-worker ServiceAccount's
Vault role and the ingest-worker network policy (via the dev-only test-runner label).
"""

import json
import os
import time
import uuid

import pytest

from tests.support.cluster import NAMESPACE, kubectl, local_cluster_ok

pytestmark = pytest.mark.infra

IMAGE = os.environ.get("KC_TEST_IMAGE", "kc/test-runner:dev")


def job_manifest(name: str) -> dict[str, object]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 600,
            "template": {
                "metadata": {"labels": {"kc.io/component": "test-runner"}},
                "spec": {
                    "serviceAccountName": "kc-ingest-worker",
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "tests",
                            "image": IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "python", "-m", "pytest", "tests/leak/infra/in_cluster",
                                "-m", "in_cluster", "-q", "-rA",
                            ],
                            "env": [
                                {"name": "KC_IN_CLUSTER", "value": "1"},
                                {"name": "KC_MONGO_HOST", "value": "kc-mongodb:27017"},
                                {"name": "HOME", "value": "/tmp"},
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {
                                    "name": "vault-token",
                                    "mountPath": "/var/run/secrets/kc",
                                    "readOnly": True,
                                },
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "vault-token",
                            "projected": {
                                "sources": [
                                    {
                                        "serviceAccountToken": {
                                            "audience": "vault",
                                            "expirationSeconds": 3600,
                                            "path": "vault-token",
                                        }
                                    }
                                ]
                            },
                        },
                        {"name": "tmp", "emptyDir": {}},
                    ],
                },
            },
        },
    }  # fmt: skip


def test_task_credentials_are_single_space_in_cluster() -> None:
    if not local_cluster_ok():
        if os.environ.get("KC_REQUIRE_INFRA") == "1":
            pytest.fail("local cluster not available")
        pytest.skip("local cluster not available (make kind-up)")
    name = f"leak-isolation-{uuid.uuid4().hex[:8]}"
    kubectl("-n", NAMESPACE, "apply", "-f", "-", stdin=json.dumps(job_manifest(name)))
    try:
        deadline = time.monotonic() + 600
        while True:
            status = kubectl(
                "-n", NAMESPACE, "get", f"job/{name}",
                "-o", "jsonpath={.status.succeeded}/{.status.failed}",
            ).strip()  # fmt: skip
            if status != "/":
                break
            if time.monotonic() > deadline:
                raise AssertionError("isolation job did not finish")
            time.sleep(3)
        logs = kubectl("-n", NAMESPACE, "logs", f"job/{name}", check=False)
        assert " passed" in logs, logs
        assert status == "1/", logs  # succeeded/failed
        assert " failed" not in logs and " skipped" not in logs and " error" not in logs, logs
    finally:
        kubectl("-n", NAMESPACE, "delete", "job", name, "--wait=false", check=False)


def test_space_runner_is_isolated_from_broker_and_other_spaces() -> None:
    """ADR-011: runner-sp_cd has no ServiceAccount token, no view of the broker or of any
    other space's runner, and no access to another space's socket directory."""
    if not local_cluster_ok():
        pytest.skip("local cluster not available")
    check = (
        "import os,pathlib;"
        "print('SA_TOKEN', os.path.exists('/var/run/secrets/kc/vault-token'));"
        "sa='/var/run/secrets/kubernetes.io/serviceaccount/token';"
        "print('SA_DEFAULT', os.path.exists(sa));"
        "print('OWN_SOCKET', os.path.exists('/run/kc/sp_cd/runner.sock'));"
        "print('OPC_DIR', os.path.exists('/run/kc/sp_opc'));"
        "procs=pathlib.Path('/proc').glob('[0-9]*');"
        "cmds=[pathlib.Path(p,'cmdline').read_bytes() for p in procs];"
        # Needles built at run time so this probe's own command line cannot match.
        "m=b'ingest_worker'+b'.main';r=b'ingest_worker'+b'.runner';"
        "print('SEES_BROKER', any(m in c for c in cmds));"
        "print('RUNNERS', sum(r in c for c in cmds))"
    )
    out = kubectl(
        "-n", NAMESPACE, "exec", "deploy/kc-ingest-worker", "-c", "runner-sp-cd", "--",
        "python", "-c", check,
    )  # fmt: skip
    assert "OWN_SOCKET True" in out, out  # positive control: its own space works
    assert "RUNNERS 1" in out, out  # sees exactly itself
    assert "SA_TOKEN False" in out, out
    assert "SA_DEFAULT False" in out, out
    assert "OPC_DIR False" in out, out
    assert "SEES_BROKER False" in out, out
