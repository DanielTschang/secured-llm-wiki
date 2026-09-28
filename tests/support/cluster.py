"""Local-cluster access for dev tooling and tests: guard first, then port-forward.

The ambient kube current-context is never used.
"""

import contextlib
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).parents[2]
GUARD = REPO / "scripts/require-local-context.sh"
CONTEXT = os.environ.get("KC_KUBE_CONTEXT", "kind-kc")
NAMESPACE = os.environ.get("KC_NAMESPACE", "kc")


def guard_reason(ctx: str = CONTEXT) -> str | None:
    """None if ctx is the local cluster, else the guard's refusal message."""
    r = subprocess.run([str(GUARD), ctx], capture_output=True, text=True, check=False)
    return None if r.returncode == 0 else (r.stderr.strip() or f"guard exit {r.returncode}")


def local_cluster_ok(ctx: str = CONTEXT) -> bool:
    return guard_reason(ctx) is None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def port_forward(service: str, remote: int, ctx: str = CONTEXT) -> Iterator[int]:
    if (reason := guard_reason(ctx)) is not None:
        raise RuntimeError(f"refusing: {reason}")
    local = _free_port()
    proc = subprocess.Popen(
        ["kubectl", "--context", ctx, "-n", NAMESPACE, "port-forward",
         f"svc/{service}", f"{local}:{remote}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for _ in range(100):
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", local), 0.2):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"port-forward to {service} did not come up")
        yield local
    finally:
        proc.terminate()
        proc.wait()


def kubectl(*args: str, stdin: str | None = None, check: bool = True, ctx: str = CONTEXT) -> str:
    if (reason := guard_reason(ctx)) is not None:
        raise RuntimeError(f"refusing: {reason}")
    result = subprocess.run(
        ["kubectl", "--context", ctx, *args],
        input=stdin, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout + result.stderr
