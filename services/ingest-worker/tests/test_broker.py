import json
import os
import sys
from pathlib import Path

import pytest

from ingest_worker.broker import Decision, Launcher, TaskResult, decide
from ingest_worker.runner import LocalRunner, SocketRunner, SpaceRouter, serve, task_env
from kc_events import PageEvent

EVENT = PageEvent.of(space_id="sp_opc", page_id="opc_o1", revision=2)
ALLOWED = {
    "KC_VAULT_ADDR",
    "KC_SPACE_ID",
    "KC_PAGE_ID",
    "KC_REVISION",
    "KC_MONGO_HOST",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "LANG",
    "KC_MODEL_BASE_URL",
    "KC_MODEL_NAME",
    "KC_MODEL_ALLOWED_HOSTS",
}


class FakeBrokerVault:
    def __init__(self) -> None:
        self.roles: list[str] = []

    def child_token(self, token_role: str) -> str:
        self.roles.append(token_role)
        return f"child-for-{token_role}"


def test_task_env_is_allow_listed_and_has_no_token() -> None:
    env = task_env(EVENT, vault_addr="http://kc-vault:8200", mongo_host="m:1")
    assert set(env) <= ALLOWED
    assert env["KC_SPACE_ID"] == "sp_opc"
    assert env["KC_PAGE_ID"] == "opc_o1"
    assert env["KC_REVISION"] == "2"
    assert not any("TOKEN" in k for k in env)


def dump_cmd(out: Path) -> list[str]:
    """A task stand-in that reports ready (as a hardened task does), then records its env
    and the token it received on stdin."""
    code = (
        "import os,sys,json;print('ready',flush=True);"
        f"open({str(out)!r},'w').write(json.dumps({{'env':dict(os.environ),'stdin':sys.stdin.read()}}))"
    )
    return [sys.executable, "-c", code]


def local(cmd: list[str], timeout: float = 30) -> LocalRunner:
    return LocalRunner(
        vault_addr="http://v", mongo_host="m:1", command=cmd, timeout=timeout, ready_timeout=3
    )


def task_cmd(body: str) -> list[str]:
    """A task stand-in that reports ready first, as the real task does."""
    return [sys.executable, "-c", f"print('ready', flush=True); {body}"]


def test_launcher_mints_single_space_token_and_passes_it_on_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KC_BROKER_VAULT_TOKEN", "broker-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked?")
    out = tmp_path / "task.json"
    vault = FakeBrokerVault()
    assert Launcher(vault, local(dump_cmd(out))).run(EVENT) is TaskResult.DONE
    assert vault.roles == ["space-sp_opc"]
    seen = json.loads(out.read_text())
    assert set(seen["env"]) - {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"} <= ALLOWED
    assert seen["stdin"].strip() == "child-for-space-sp_opc"
    assert "child-for" not in json.dumps(seen["env"])  # never in the environment
    assert "broker-secret" not in json.dumps(seen) and "leaked?" not in json.dumps(seen)
    assert os.environ["KC_BROKER_VAULT_TOKEN"] == "broker-secret"  # parent untouched


@pytest.mark.parametrize(
    ("code", "result"), [(0, TaskResult.DONE), (3, TaskResult.STALE), (1, TaskResult.FAILED)]
)
def test_exit_codes(code: int, result: TaskResult) -> None:
    runner = local(task_cmd(f"raise SystemExit({code})"))
    assert Launcher(FakeBrokerVault(), runner).run(EVENT) is result


def test_timeout_kills_task() -> None:
    runner = local(task_cmd("import time; time.sleep(30)"), timeout=0.5)
    assert Launcher(FakeBrokerVault(), runner).run(EVENT) is TaskResult.FAILED


def test_token_failure_is_failure_without_spawn(tmp_path: Path) -> None:
    class Denied(FakeBrokerVault):
        def child_token(self, token_role: str) -> str:
            raise PermissionError

    out = tmp_path / "ran"
    runner = local([sys.executable, "-c", f"open({str(out)!r},'w')"])
    assert Launcher(Denied(), runner).run(EVENT) is TaskResult.FAILED
    assert not out.exists()


def test_socket_runner_roundtrip(tmp_path: Path) -> None:
    """Broker and runner live in different containers and talk over a unix socket."""
    import threading

    out = tmp_path / "task.json"
    sock = short_socket_path()
    server = serve(sock, local(dump_cmd(out)), space="sp_opc")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert Launcher(FakeBrokerVault(), SocketRunner(sock)).run(EVENT) is TaskResult.DONE
        assert json.loads(out.read_text())["stdin"].strip() == "child-for-space-sp_opc"
    finally:
        server.shutdown()


def test_runner_rejects_malformed_requests(tmp_path: Path) -> None:
    import socket
    import threading

    ran = tmp_path / "ran"
    sock = short_socket_path()
    server = serve(sock, local([sys.executable, "-c", f"open({str(ran)!r},'w')"]), space="sp_opc")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for bad in (
            b"garbage\n",
            b'{"space_id":"sp_opc"}\n',
            b'{"space_id":"OPC","page_id":"x","revision":1,"token":"t"}\n',
        ):
            with socket.socket(socket.AF_UNIX) as c:
                c.connect(str(sock))
                c.sendall(bad)
                assert json.loads(c.makefile().readline())["result"] == "failed"
        assert not ran.exists()
    finally:
        server.shutdown()


def short_socket_path() -> Path:
    # AF_UNIX paths are limited to ~104 bytes on macOS; pytest's tmp_path is longer.
    import uuid

    return Path("/tmp") / f"kc-{uuid.uuid4().hex[:8]}.sock"


def test_token_withheld_until_task_reports_ready(tmp_path: Path) -> None:
    """The token is written only after the task has hardened itself and said so; a task
    that never reports ready never receives it."""
    out = tmp_path / "stdin"
    code = f"import sys;open({str(out)!r},'w').write(sys.stdin.read())"  # no ready line
    result = Launcher(FakeBrokerVault(), local([sys.executable, "-c", code], timeout=5)).run(EVENT)
    assert result is TaskResult.FAILED
    assert out.read_text() == ""


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


def test_runner_refuses_other_spaces(tmp_path: Path) -> None:
    """ADR-011: a space's runner only ever runs (and receives tokens for) its own space."""
    import threading

    ran = tmp_path / "ran"
    sock = short_socket_path()
    cmd = task_cmd(f"open({str(ran)!r},'w')")
    server = serve(sock, local(cmd), space="sp_cd")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert Launcher(FakeBrokerVault(), SocketRunner(sock)).run(EVENT) is TaskResult.FAILED
        assert not ran.exists()
    finally:
        server.shutdown()


def test_router_sends_each_space_to_its_own_socket() -> None:
    seen: list[tuple[str, str]] = []

    class Recorder:
        def __init__(self, path: Path) -> None:
            self.path = path

        def run(self, event: PageEvent, token: str) -> TaskResult:
            seen.append((str(self.path), event.space_id))
            return TaskResult.DONE

    router = SpaceRouter(Path("/run/kc"), factory=Recorder)
    for space in ("sp_opc", "sp_cd"):
        router.run(PageEvent.of(space_id=space, page_id="p1", revision=1), "t")
    assert seen == [
        ("/run/kc/sp_opc/runner.sock", "sp_opc"),
        ("/run/kc/sp_cd/runner.sock", "sp_cd"),
    ]
