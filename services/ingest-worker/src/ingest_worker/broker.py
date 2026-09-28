"""Main-process side of ingest (ADR-006). Handles IDs only and never reads space content.

For each event it mints a child token restricted to the event's space and runs the task
in a fresh subprocess with an explicit, minimal environment. Subprocesses are never
reused across tasks or spaces.
"""

import subprocess
import sys
from enum import Enum
from typing import Protocol

from kc_events import PageEvent
from kc_obs import get_logger

__all__ = ["Decision", "Launcher", "TaskResult", "decide", "task_env"]

log = get_logger("ingest_broker")

EXIT_DONE = 0
EXIT_STALE = 3
TASK_COMMAND = [sys.executable, "-m", "ingest_worker.task"]


class TaskResult(Enum):
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"


class Decision(Enum):
    ACK = "ack"
    NAK = "nak"
    TERM = "term"


class BrokerVault(Protocol):
    def child_token(self, token_role: str) -> str: ...


def task_env(
    event: PageEvent, *, vault_addr: str, child_token: str, mongo_host: str
) -> dict[str, str]:
    """The subprocess's entire environment. Nothing is inherited from the broker."""
    return {
        "KC_VAULT_ADDR": vault_addr,
        "KC_VAULT_TOKEN": child_token,
        "KC_SPACE_ID": event.space_id,
        "KC_PAGE_ID": event.page_id,
        "KC_REVISION": str(int(event.revision)),
        "KC_MONGO_HOST": mongo_host,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
    }


class Launcher:
    def __init__(
        self,
        vault: BrokerVault,
        *,
        vault_addr: str,
        mongo_host: str,
        command: list[str] | None = None,
        timeout: float = 600,
    ) -> None:
        self._vault = vault
        self._vault_addr = vault_addr
        self._mongo_host = mongo_host
        self._command = command or TASK_COMMAND
        self._timeout = timeout

    def run(self, event: PageEvent) -> TaskResult:
        try:
            token = self._vault.child_token(f"space-{event.space_id}")
        except Exception as e:
            log.error("child_token_failed", space_id=event.space_id, page_id=event.page_id, error=e)
            return TaskResult.FAILED
        env = task_env(
            event, vault_addr=self._vault_addr, child_token=token, mongo_host=self._mongo_host
        )
        try:
            # stdout/stderr pass through: the task logs only via kc_obs (IDs and numbers).
            proc = subprocess.run(self._command, env=env, timeout=self._timeout, check=False)  # noqa: S603
        except subprocess.TimeoutExpired:
            log.error("task_timeout", space_id=event.space_id, page_id=event.page_id)
            return TaskResult.FAILED
        match proc.returncode:
            case 0:
                return TaskResult.DONE
            case 3:
                return TaskResult.STALE
            case code:
                log.error(
                    "task_failed", space_id=event.space_id, page_id=event.page_id, exit_code=code
                )
                return TaskResult.FAILED


class TaskRunner(Protocol):
    def run(self, event: PageEvent) -> TaskResult: ...


def decide(subject: str, data: bytes, launcher: TaskRunner) -> Decision:
    """What to do with one queue message. Malformed or mis-routed messages are terminated,
    never retried and never launched."""
    try:
        event = PageEvent.from_message(subject=subject, data=data)
    except ValueError:
        log.warning("message_rejected")
        return Decision.TERM
    result = launcher.run(event)
    log.info(
        "task_finished",
        space_id=event.space_id,
        page_id=event.page_id,
        revision=int(event.revision),
        result=result,
    )
    return Decision.NAK if result is TaskResult.FAILED else Decision.ACK
