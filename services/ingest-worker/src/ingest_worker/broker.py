"""Broker side of ingest (ADR-006). Handles IDs only and never reads space content.

For each event it mints a child token restricted to the event's space and hands it to the
runner container, which runs the task in a fresh subprocess. Only the broker holds the
ServiceAccount token that can mint child tokens.
"""

from enum import Enum
from typing import Protocol

from kc_events import PageEvent
from kc_obs import get_logger

__all__ = ["Decision", "Launcher", "TaskResult", "decide"]

log = get_logger("ingest_broker")

EXIT_DONE = 0
EXIT_STALE = 3


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


class Runner(Protocol):
    def run(self, event: PageEvent, token: str) -> TaskResult: ...


class Launcher:
    """Mints a single-space child token for the event and hands the task to the runner."""

    def __init__(self, vault: BrokerVault, runner: Runner) -> None:
        self._vault = vault
        self._runner = runner

    def run(self, event: PageEvent) -> TaskResult:
        try:
            token = self._vault.child_token(f"space-{event.space_id}")
        except Exception as e:
            log.error("child_token_failed", space_id=event.space_id, page_id=event.page_id, error=e)
            return TaskResult.FAILED
        return self._runner.run(event, token)


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
