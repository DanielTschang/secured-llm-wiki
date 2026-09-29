"""Infrastructure leak tests: the local cluster must enforce what the app assumes.

Require the local kind cluster (`make kind-up`). Skipped when absent unless
KC_REQUIRE_INFRA=1, in which case absence is a failure.

Every "blocked" assertion has a positive control: the same probe must succeed from a
namespace without policies. Otherwise an offline machine or a broken probe would pass.
"""

import json
import os
import re
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.infra

REPO = Path(__file__).parents[3]
GUARD = REPO / "scripts/require-local-context.sh"
CONTEXT = os.environ.get("KC_KUBE_CONTEXT", "kind-kc")
NAMESPACE = os.environ.get("KC_NAMESPACE", "kc")
CONTROL_NAMESPACE = "kc-leak-control"
PROBE_IMAGE = "busybox:1.36"
REQUIRE = os.environ.get("KC_REQUIRE_INFRA") == "1"


def _unavailable(reason: str) -> None:
    if REQUIRE:
        pytest.fail(reason)
    pytest.skip(reason)


def kubectl(ctx: str, *args: str, stdin: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", "--context", ctx, *args],
        input=stdin, capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout + result.stderr


@pytest.fixture(scope="module")
def cluster() -> str:
    guard = subprocess.run([str(GUARD), CONTEXT], capture_output=True, text=True, check=False)
    if guard.returncode != 0:
        _unavailable(f"local cluster {CONTEXT} not available (run make kind-up)")
    return CONTEXT


@pytest.fixture(scope="module")
def control_ns(cluster: str) -> Iterator[str]:
    """A namespace with no NetworkPolicies: the positive control."""
    manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": CONTROL_NAMESPACE,
            "labels": {"pod-security.kubernetes.io/enforce": "restricted"},
        },
    }
    kubectl(cluster, "apply", "-f", "-", stdin=json.dumps(manifest))
    yield CONTROL_NAMESPACE
    kubectl(cluster, "delete", "namespace", CONTROL_NAMESPACE, "--wait=false", check=False)


def probe(ctx: str, namespace: str, script: str, labels: dict[str, str] | None = None) -> str:
    """Run a one-shot restricted-PSA busybox pod and return its logs."""
    name = f"leak-probe-{uuid.uuid4().hex[:8]}"
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": {"kc.io/leak-probe": "true", **(labels or {})}},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": PROBE_IMAGE,
                    "command": ["sh", "-c", script],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    kubectl(ctx, "-n", namespace, "apply", "-f", "-", stdin=json.dumps(pod))
    try:
        kubectl(
            ctx, "-n", namespace, "wait", f"pod/{name}", "--timeout=180s",
            "--for=jsonpath={.status.phase}=Succeeded",
        )  # fmt: skip
        return kubectl(ctx, "-n", namespace, "logs", name)
    finally:
        kubectl(ctx, "-n", namespace, "delete", "pod", name, "--wait=false", check=False)


def node_ip(ctx: str) -> str:
    return kubectl(
        ctx, "get", "nodes", "-o",
        'jsonpath={.items[0].status.addresses[?(@.type=="InternalIP")].address}',
    ).strip()  # fmt: skip


def egress_checks(node: str) -> dict[str, str]:
    return {
        "dns_internal": "nslookup kubernetes.default.svc.cluster.local",
        "dns_external": "nslookup example.com",
        "tcp_internet": "nc -w 5 1.1.1.1 443 </dev/null",
        "tcp_apiserver": "nc -w 5 kubernetes.default.svc.cluster.local 443 </dev/null",
        "tcp_other_namespace": "nc -w 5 kube-dns.kube-system.svc.cluster.local 9153 </dev/null",
        "tcp_kubelet": f"nc -w 5 {node} 10250 </dev/null",
    }


def egress_script(node: str) -> str:
    """Prints `<check> OPEN|BLOCKED` for each egress path, at the connection level."""
    checks = egress_checks(node)
    lines = ["echo PROBE_RAN"]
    for name, cmd in checks.items():
        lines.append(
            f"if {cmd} >/dev/null 2>&1; then echo '{name} OPEN'; else echo '{name} BLOCKED'; fi"
        )
    return "; ".join(lines)


def parse(out: str, node: str) -> dict[str, str]:
    assert "PROBE_RAN" in out, out
    results = dict(re.findall(r"^(\w+) (OPEN|BLOCKED)$", out, flags=re.M))
    assert set(results) == set(egress_checks(node)), out  # no check may go missing
    return results


@pytest.fixture(scope="module")
def control_results(cluster: str, control_ns: str) -> dict[str, str]:
    node = node_ip(cluster)
    return parse(probe(cluster, control_ns, egress_script(node)), node)


@pytest.mark.parametrize(
    "labels",
    [
        None,
        {"kc.io/platform-client": "acl"},
        {"kc.io/platform-client": "pages"},
        {"kc.io/component": "ingest-worker"},
        {"kc.io/component": "sync", "kc.io/platform-client": "pages"},
        {"kc.io/component": "test-runner"},
    ],
    ids=["plain", "acl-client", "pages-client", "ingest-worker", "sync", "test-runner"],
)
def test_no_egress_beyond_cluster_dns(
    cluster: str, control_results: dict[str, str], labels: dict[str, str] | None
) -> None:
    node = node_ip(cluster)
    results = parse(probe(cluster, NAMESPACE, egress_script(node), labels), node)
    assert results["dns_internal"] == "OPEN", results  # pod networking and cluster DNS work
    unverifiable: list[str] = []
    for check, state in results.items():
        if check == "dns_internal":
            continue
        assert state == "BLOCKED", f"{check} reachable from {NAMESPACE}: {results}"
        if control_results[check] != "OPEN":
            unverifiable.append(check)
    if unverifiable:
        _unavailable(f"positive control did not connect for {unverifiable}; block unverified")


# --- mock-platform L7 policy -------------------------------------------------

HTTP_SCRIPT = """echo PROBE_RAN
req() {
  out=$(wget -q -T 5 -O /dev/null "$@" 2>&1); rc=$?
  code=$(echo "$out" | grep -oE 'HTTP/1\\.[01] [0-9]{3}' | tail -1 | cut -d' ' -f2)
  echo "RESULT rc=$rc code=${code:-none}"
}
"""


def http(
    cluster: str, client: str | None, method: str, path: str, namespace: str = NAMESPACE
) -> str:
    """The HTTP status seen by the probe, or 'none' when the connection was dropped."""
    args = f"http://mock-platform.{NAMESPACE}.svc.cluster.local{path}"
    if method == "POST":
        body = '{"user_id":"svc_sync"}'
        args = f"--post-data='{body}' --header='Content-Type: application/json' {args}"
    labels = {"kc.io/platform-client": client} if client else None
    out = probe(cluster, namespace, HTTP_SCRIPT + f"req {args}", labels)
    assert "PROBE_RAN" in out, out
    m = re.search(r"RESULT rc=(\d+) code=(\w+)", out)
    assert m, out
    return "200" if m.group(1) == "0" else m.group(2)


# Reaching the app without a token yields 401/404 from the app itself (the positive
# control that the path is open); Cilium's L7 deny is 403; an L3/L4 drop has no status.
@pytest.mark.parametrize(
    ("client", "method", "path", "expect"),
    [
        (None, "GET", "/.well-known/jwks.json", "none"),
        ("acl", "GET", "/.well-known/jwks.json", "200"),
        ("acl", "GET", "/api/me/spaces", "401"),
        ("acl", "GET", "/api/pages/opc_o1", "403"),
        ("acl", "GET", "/api/spaces/sp_opc/pages", "403"),
        ("pages", "GET", "/api/pages/opc_o1", "404"),
        ("pages", "GET", "/api/me/spaces", "403"),
        ("pages", "GET", "/api/spaces/sp_opc/pages", "404"),
        ("pages", "GET", "/api/pages/opc_o1/attachments/o1_residual.png", "404"),
        ("acl", "GET", "/api/me/spaces?x=1", "403"),
        # Sync must never get the rendered form (it may contain other spaces' includes).
        ("pages", "GET", "/api/pages/opc_o2?format=rendered", "403"),
        ("acl", "POST", "/dev/token", "403"),
        ("pages", "POST", "/dev/token", "403"),
    ],
)
def test_mock_platform_l7_allow_list(
    cluster: str, client: str | None, method: str, path: str, expect: str
) -> None:
    assert http(cluster, client, method, path) == expect


@pytest.mark.parametrize("client", [None, "acl"], ids=["plain", "acl-label"])
def test_mock_platform_ingress_rejects_other_namespaces(
    cluster: str, control_ns: str, client: str | None
) -> None:
    """From a namespace with no egress limits, only mock-platform's own ingress policy can
    block; a copied client label from another namespace must not help."""
    assert http(cluster, client, "GET", "/.well-known/jwks.json", namespace=control_ns) == "none"


def test_dev_endpoints_disabled_in_cluster(cluster: str) -> None:
    out = kubectl(
        cluster, "-n", NAMESPACE, "get", "deploy", "mock-platform",
        "-o", "jsonpath={.spec.template.spec.containers[0].env}",
    )  # fmt: skip
    env = {e["name"]: e.get("value") for e in json.loads(out)}
    assert env.get("KC_MOCK_DEV_ENDPOINTS") != "1"


# --- pod hardening -------------------------------------------------------------


def test_host_network_pods_rejected(cluster: str) -> None:
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "leak-hostnet"},
        "spec": {
            "hostNetwork": True,
            "containers": [{"name": "c", "image": PROBE_IMAGE, "command": ["true"]}],
        },
    }
    out = kubectl(
        cluster, "-n", NAMESPACE, "apply", "--dry-run=server", "-f", "-",
        stdin=json.dumps(pod), check=False,
    )  # fmt: skip
    assert "violates PodSecurity" in out, out


def test_mock_platform_pod_is_hardened(cluster: str) -> None:
    jsonpath = (
        "{.items[0].spec.securityContext.runAsNonRoot} "
        "{.items[0].spec.containers[0].securityContext.readOnlyRootFilesystem} "
        "{.items[0].spec.containers[0].securityContext.allowPrivilegeEscalation} "
        "{.items[0].spec.containers[0].securityContext.capabilities.drop}"
    )
    out = kubectl(
        cluster, "-n", NAMESPACE, "get", "pods",
        "-l", "app.kubernetes.io/name=mock-platform", "-o", f"jsonpath={jsonpath}",
    )  # fmt: skip
    assert out.split(" ", 3) == ["true", "true", "false", '["ALL"]'], out


# --- M1: storage reachability by role --------------------------------------------------

STORAGE = {
    "neo4j_opc": "kc-neo4j-sp-opc 7687",
    "neo4j_cd": "kc-neo4j-sp-cd 7687",
    "vault": "kc-vault 8200",
    "minio": "kc-minio 9000",
    "mongodb": "kc-mongodb 27017",
    "nats": "kc-nats 4222",
    "model_gateway": "kc-model-gateway 8080",
}


def storage_reach(cluster: str, labels: dict[str, str] | None) -> dict[str, str]:
    lines = ["echo PROBE_RAN"]
    for name, target in STORAGE.items():
        lines.append(
            f"if nc -w 5 {target} </dev/null >/dev/null 2>&1; "
            f"then echo '{name} OPEN'; else echo '{name} BLOCKED'; fi"
        )
    out = probe(cluster, NAMESPACE, "; ".join(lines), labels)
    assert "PROBE_RAN" in out, out
    results = dict(re.findall(r"^(\w+) (OPEN|BLOCKED)$", out, flags=re.M))
    assert set(results) == set(STORAGE), out
    return results


def test_storage_reachable_only_by_its_clients(cluster: str) -> None:
    worker = storage_reach(cluster, {"kc.io/component": "ingest-worker"})
    sync = storage_reach(cluster, {"kc.io/component": "sync", "kc.io/platform-client": "pages"})
    plain = storage_reach(cluster, None)
    # Positive control: the ingest worker reaches everything it needs.
    assert set(worker.values()) == {"OPEN"}, worker
    # Sync never touches the graph or the model; everything else it needs is open.
    no_access = {"neo4j_opc", "neo4j_cd", "model_gateway"}
    assert sync == {k: ("BLOCKED" if k in no_access else "OPEN") for k in STORAGE}, sync
    # An unlabelled pod reaches nothing.
    assert set(plain.values()) == {"BLOCKED"}, plain


def test_model_gateway_reaches_only_the_model_server(cluster: str) -> None:
    """The gateway sees space content (prompts), so its only way out is the model server."""
    upstream = kubectl(
        cluster, "-n", NAMESPACE, "get", "deploy", "kc-model-gateway",
        "-o", "jsonpath={.spec.template.spec.containers[0].env[0].value}",
    )  # fmt: skip
    host = upstream.split("//", 1)[1].split(":", 1)[0]
    check = (
        "import socket\n"
        "def probe(h, p):\n"
        "    try:\n"
        "        socket.create_connection((h, p), 5).close(); return 'OPEN'\n"
        "    except OSError:\n"
        "        return 'BLOCKED'\n"
        f"print('UPSTREAM', probe({host!r}, 11434))\n"
        f"print('UPSTREAM_OTHER_PORT', probe({host!r}, 22))\n"
        "print('INTERNET', probe('1.1.1.1', 443))\n"
        "print('MINIO', probe('kc-minio', 9000))\n"
        "print('VAULT', probe('kc-vault', 8200))\n"
    )
    out = kubectl(
        cluster, "-n", NAMESPACE, "exec", "deploy/kc-model-gateway", "--",
        "python", "-c", check,
    )  # fmt: skip
    assert "UPSTREAM OPEN" in out, out  # positive control
    for blocked in ("UPSTREAM_OTHER_PORT", "INTERNET", "MINIO", "VAULT"):
        assert f"{blocked} BLOCKED" in out, out
