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


def local_cluster_ok(ctx: str = CONTEXT) -> bool:
    return subprocess.run([str(GUARD), ctx], capture_output=True, check=False).returncode == 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def port_forward(service: str, remote: int, ctx: str = CONTEXT) -> Iterator[int]:
    if not local_cluster_ok(ctx):
        raise RuntimeError("refusing: not a local cluster context")
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
