"""Infrastructure leak tests: the local cluster must enforce what the app assumes.

Require the local kind cluster (`make kind-up`). Skipped when absent unless
KC_REQUIRE_INFRA=1, in which case absence is a failure.
"""

import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.infra

REPO = Path(__file__).parents[3]
GUARD = REPO / "scripts/require-local-context.sh"
CONTEXT = os.environ.get("KC_KUBE_CONTEXT", "kind-kc")
NAMESPACE = os.environ.get("KC_NAMESPACE", "kc")
PROBE_IMAGE = "busybox:1.36"


def _guard(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(GUARD), *args], capture_output=True, text=True, check=False)


@pytest.mark.parametrize(
    "ctx",
    ["", "danieltschang@mixing", "mixing", "kind", "prod-kind-kc", "kind-kc; rm -rf /", "k3d_x"],
)
def test_guard_refuses_non_local_contexts(ctx: str) -> None:
    assert _guard(ctx, "--allow-missing").returncode != 0


def test_guard_accepts_local_name_shape() -> None:
    assert _guard("kind-kc", "--allow-missing").returncode == 0


@pytest.fixture(scope="module")
def cluster() -> str:
    if _guard(CONTEXT).returncode != 0:
        if os.environ.get("KC_REQUIRE_INFRA") == "1":
            pytest.fail(f"local cluster {CONTEXT} not available")
        pytest.skip(f"local cluster {CONTEXT} not available (run make kind-up)")
    return CONTEXT


def _probe(ctx: str, script: str, *, platform_client: bool) -> str:
    """Run a one-shot busybox pod in the kc namespace and return its output."""
    name = f"leak-probe-{uuid.uuid4().hex[:8]}"
    labels = "kc.io/leak-probe=true"
    if platform_client:
        labels += ",kc.io/platform-client=true"
    result = subprocess.run(
        [
            "kubectl", "--context", ctx, "-n", NAMESPACE, "run", name,
            "--rm", "-i", "--restart=Never", "--quiet",
            f"--image={PROBE_IMAGE}", f"--labels={labels}",
            "--pod-running-timeout=2m",
            "--", "sh", "-c", script,
        ],
        capture_output=True, text=True, timeout=240, check=False,
    )  # fmt: skip
    return result.stdout + result.stderr


# Every probe prints PROBE_RAN first: a pod that never started must not count as "blocked".
_DNS = (
    "nslookup kubernetes.default.svc.cluster.local >/dev/null 2>&1 && echo DNS_OK || echo DNS_FAIL"
)


def _check(target: str, label: str) -> str:
    fetch = f"wget -q -T 5 -O /dev/null {target} >/dev/null 2>&1"
    return f"{fetch} && echo {label}_OPEN || echo {label}_BLOCKED"


def test_no_egress_to_internet(cluster: str) -> None:
    out = _probe(
        cluster,
        f"echo PROBE_RAN; {_DNS}; {_check('http://1.1.1.1', 'IP')}; "
        f"{_check('http://example.com', 'NAME')}",
        platform_client=False,
    )
    assert "PROBE_RAN" in out, out
    assert "DNS_OK" in out, out  # positive control: pod networking works, DNS allowed
    assert "IP_BLOCKED" in out, out
    assert "NAME_BLOCKED" in out, out


def test_mock_platform_only_reachable_by_platform_clients(cluster: str) -> None:
    target = "http://mock-platform/.well-known/jwks.json"
    denied = _probe(cluster, f"echo PROBE_RAN; {_check(target, 'MP')}", platform_client=False)
    allowed = _probe(cluster, f"echo PROBE_RAN; {_check(target, 'MP')}", platform_client=True)
    assert "PROBE_RAN" in denied, denied
    assert "MP_BLOCKED" in denied, denied
    assert "MP_OPEN" in allowed, allowed  # positive control: the block is policy, not breakage


def test_platform_client_still_has_no_internet(cluster: str) -> None:
    out = _probe(cluster, f"echo PROBE_RAN; {_check('http://1.1.1.1', 'IP')}", platform_client=True)
    assert "PROBE_RAN" in out, out
    assert "IP_BLOCKED" in out, out


def test_mock_platform_pod_is_hardened(cluster: str) -> None:
    jsonpath = (
        "{.items[0].spec.securityContext.runAsNonRoot} "
        "{.items[0].spec.containers[0].securityContext.readOnlyRootFilesystem} "
        "{.items[0].spec.containers[0].securityContext.allowPrivilegeEscalation} "
        "{.items[0].spec.containers[0].securityContext.capabilities.drop}"
    )
    out = subprocess.run(
        [
            "kubectl", "--context", cluster, "-n", NAMESPACE, "get", "pods",
            "-l", "app.kubernetes.io/name=mock-platform", "-o", f"jsonpath={jsonpath}",
        ],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert out.split(" ", 3) == ["true", "true", "false", '["ALL"]'], out
