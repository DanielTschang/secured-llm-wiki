import json
import os
import sys
from pathlib import Path

import pytest

from ingest_worker.broker import Decision, Launcher, TaskResult, decide, task_env
from kc_events import PageEvent

EVENT = PageEvent.of(space_id="sp_opc", page_id="opc_o1", revision=2)
ALLOWED = {
    "KC_VAULT_ADDR",
    "KC_VAULT_TOKEN",
    "KC_SPACE_ID",
    "KC_PAGE_ID",
    "KC_REVISION",
    "KC_MONGO_HOST",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "LANG",
}


class FakeBrokerVault:
    def __init__(self) -> None:
        self.roles: list[str] = []

    def child_token(self, token_role: str) -> str:
        self.roles.append(token_role)
        return f"child-for-{token_role}"


def test_task_env_is_allow_listed() -> None:
    env = task_env(EVENT, vault_addr="http://kc-vault:8200", child_token="child", mongo_host="m:1")
    assert set(env) <= ALLOWED
    assert env["KC_SPACE_ID"] == "sp_opc"
    assert env["KC_PAGE_ID"] == "opc_o1"
    assert env["KC_REVISION"] == "2"
    assert env["KC_VAULT_TOKEN"] == "child"


def test_launcher_mints_single_space_token_and_isolates_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KC_BROKER_VAULT_TOKEN", "broker-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked?")
    out = tmp_path / "env.json"
    vault = FakeBrokerVault()
    cmd = [
        sys.executable,
        "-c",
        f"import os,json;open({str(out)!r},'w').write(json.dumps(dict(os.environ)))",
    ]
    launcher = Launcher(vault, vault_addr="http://v", mongo_host="m:1", command=cmd, timeout=30)
    assert launcher.run(EVENT) is TaskResult.DONE
    assert vault.roles == ["space-sp_opc"]
    child_env = json.loads(out.read_text())
    assert set(child_env) - {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"} <= ALLOWED
    assert child_env["KC_VAULT_TOKEN"] == "child-for-space-sp_opc"
    assert "broker-secret" not in json.dumps(child_env)
    assert "leaked?" not in json.dumps(child_env)
    assert os.environ["KC_BROKER_VAULT_TOKEN"] == "broker-secret"  # parent untouched


@pytest.mark.parametrize(
    ("code", "result"), [(0, TaskResult.DONE), (3, TaskResult.STALE), (1, TaskResult.FAILED)]
)
def test_exit_codes(code: int, result: TaskResult) -> None:
    cmd = [sys.executable, "-c", f"raise SystemExit({code})"]
    launcher = Launcher(FakeBrokerVault(), vault_addr="v", mongo_host="m", command=cmd, timeout=30)
    assert launcher.run(EVENT) is result


def test_timeout_kills_task() -> None:
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    launcher = Launcher(FakeBrokerVault(), vault_addr="v", mongo_host="m", command=cmd, timeout=0.5)
    assert launcher.run(EVENT) is TaskResult.FAILED


def test_token_failure_is_failure_without_spawn() -> None:
    class Denied(FakeBrokerVault):
        def child_token(self, token_role: str) -> str:
            raise PermissionError

    cmd = [sys.executable, "-c", "raise SystemExit(0)"]
    launcher = Launcher(Denied(), vault_addr="v", mongo_host="m", command=cmd, timeout=5)
    assert launcher.run(EVENT) is TaskResult.FAILED


class StubLauncher:
    def __init__(self, result: TaskResult) -> None:
        self.result = result
        self.seen: list[PageEvent] = []

    def run(self, event: PageEvent) -> TaskResult:
        self.seen.append(event)
        return self.result


@pytest.mark.parametrize(
    ("result", "decision"),
    [
        (TaskResult.DONE, Decision.ACK),
        (TaskResult.STALE, Decision.ACK),
        (TaskResult.FAILED, Decision.NAK),
    ],
)
def test_decide(result: TaskResult, decision: Decision) -> None:
    launcher = StubLauncher(result)
    assert decide("kc.page.sp_opc", EVENT.to_bytes(), launcher) is decision
    assert launcher.seen == [EVENT]


@pytest.mark.parametrize(
    ("subject", "data"),
    [
        ("kc.page.sp_cd", EVENT.to_bytes()),  # event claims another space than its subject
        ("kc.page.sp_opc", b'{"space_id":"sp_opc","page_id":"opc_o1","revision":2,"x":"KESTREL"}'),
        ("kc.page.sp_opc", b"not json"),
    ],
)
def test_bad_messages_terminated_without_launch(subject: str, data: bytes) -> None:
    launcher = StubLauncher(TaskResult.DONE)
    assert decide(subject, data, launcher) is Decision.TERM
    assert launcher.seen == []
