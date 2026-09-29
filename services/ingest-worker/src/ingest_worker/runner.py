"""Task runner: its own container, no ServiceAccount token (ADR-006).

Receives (space_id, page_id, revision, child token) from the broker over a unix socket
and runs each task in a fresh subprocess. The token goes to the task on stdin, never in
its environment, and tasks make themselves non-dumpable, so a concurrent task of another
space cannot read it from /proc.
"""

import json
import os
import socketserver
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from ingest_worker.broker import EXIT_STALE, TaskResult
from kc_events import PageEvent
from kc_obs import configure_logging, get_logger

__all__ = ["LocalRunner", "Runner", "SocketRunner", "serve", "task_env"]

log = get_logger("ingest_runner")

TASK_COMMAND = [sys.executable, "-m", "ingest_worker.task"]
SOCKET = Path(os.environ.get("KC_RUNNER_SOCKET", "/run/kc/runner.sock"))


class Runner(Protocol):
    def run(self, event: PageEvent, token: str) -> TaskResult: ...


def task_env(event: PageEvent, *, vault_addr: str, mongo_host: str) -> dict[str, str]:
    """The task's entire environment: IDs and endpoints. Nothing inherited, no secrets."""
    return {
        "KC_VAULT_ADDR": vault_addr,
        "KC_SPACE_ID": event.space_id,
        "KC_PAGE_ID": event.page_id,
        "KC_REVISION": str(int(event.revision)),
        "KC_MONGO_HOST": mongo_host,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
    }


class LocalRunner:
    def __init__(
        self,
        *,
        vault_addr: str,
        mongo_host: str,
        command: list[str] | None = None,
        timeout: float = 600,
    ) -> None:
        self._vault_addr = vault_addr
        self._mongo_host = mongo_host
        self._command = command or TASK_COMMAND
        self._timeout = timeout

    def run(self, event: PageEvent, token: str) -> TaskResult:
        env = task_env(event, vault_addr=self._vault_addr, mongo_host=self._mongo_host)
        try:
            # stdout/stderr pass through: the task logs only via kc_obs (IDs and numbers).
            proc = subprocess.run(  # noqa: S603
                self._command,
                env=env,
                input=token + "\n",
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.error("task_timeout", space_id=event.space_id, page_id=event.page_id)
            return TaskResult.FAILED
        match proc.returncode:
            case 0:
                return TaskResult.DONE
            case code if code == EXIT_STALE:
                return TaskResult.STALE
            case code:
                log.error(
                    "task_failed", space_id=event.space_id, page_id=event.page_id, exit_code=code
                )
                return TaskResult.FAILED


class SocketRunner:
    """Broker-side client for the runner container."""

    def __init__(self, path: Path, timeout: float = 900) -> None:
        self._path = path
        self._timeout = timeout

    def run(self, event: PageEvent, token: str) -> TaskResult:
        import socket

        request = {
            "space_id": str(event.space_id),
            "page_id": str(event.page_id),
            "revision": int(event.revision),
            "token": token,
        }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self._timeout)
                s.connect(str(self._path))
                s.sendall(json.dumps(request).encode() + b"\n")
                reply = json.loads(s.makefile().readline())
            return TaskResult(reply["result"])
        except Exception as e:
            log.error("runner_unreachable", space_id=event.space_id, page_id=event.page_id, error=e)
            return TaskResult.FAILED


def _handle(runner: Runner, line: bytes) -> TaskResult:
    try:
        req = json.loads(line)
        token = req.pop("token")
        if not isinstance(token, str) or not token:
            raise ValueError
        event = PageEvent.from_bytes(json.dumps(req).encode())
    except Exception:
        log.warning("runner_request_rejected")
        return TaskResult.FAILED
    return runner.run(event, token)


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(path: Path, runner: Runner) -> socketserver.UnixStreamServer:
    """One request per connection; tasks for different spaces run concurrently."""
    path.unlink(missing_ok=True)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            result = _handle(runner, self.rfile.readline())
            self.wfile.write(json.dumps({"result": result.value}).encode() + b"\n")

    server = _Server(str(path), Handler)
    os.chmod(path, 0o600)
    return server


def main() -> None:
    configure_logging()
    runner = LocalRunner(
        vault_addr=os.environ.get("KC_VAULT_ADDR", "http://kc-vault:8200"),
        mongo_host=os.environ.get("KC_MONGO_HOST", "kc-mongodb:27017"),
    )
    server = serve(SOCKET, runner)
    log.info("runner_started")
    server.serve_forever()


if __name__ == "__main__":
    main()
