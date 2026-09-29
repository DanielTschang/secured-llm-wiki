"""ingest-worker main process: one pull consumer per space, one subprocess per task.

Spaces run concurrently; within a space tasks run one at a time (the consumer's
max_ack_pending=1 enforces this on the server as well).
"""

import asyncio
import os
from pathlib import Path

import httpx
import nats
from nats.js import JetStreamContext

from ingest_worker.broker import Decision, Launcher, decide
from ingest_worker.hardening import harden_process
from ingest_worker.runner import SpaceRouter
from kc_labels import SpaceId
from kc_obs import configure_logging, get_logger
from kc_store.vault import VaultClient, VaultError

log = get_logger("ingest_broker")
STREAM = "KC_PAGES"
SA_TOKEN = Path(os.environ.get("KC_SA_TOKEN_PATH", "/var/run/secrets/kc/vault-token"))
RUNNER_SOCKET_ROOT = Path(os.environ.get("KC_RUNNER_SOCKET_ROOT", "/run/kc"))


class RefreshingBrokerVault:
    """Broker Vault login that re-authenticates when its token expires."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http
        self._vault = self._login()

    def _login(self) -> VaultClient:
        return VaultClient.kubernetes_login(
            self._http, "ingest-worker", SA_TOKEN.read_text().strip()
        )

    def child_token(self, token_role: str) -> str:
        try:
            return self._vault.child_token(token_role)
        except VaultError as e:
            if e.status != 403:
                raise
            self._vault = self._login()
            return self._vault.child_token(token_role)

    def kv(self, path: str) -> dict[str, str]:
        return self._vault.kv(path)


async def consume(js: JetStreamContext, space: SpaceId, launcher: Launcher) -> None:
    durable = f"ingest-{space.replace('_', '-')}"
    sub = await js.pull_subscribe(f"kc.page.{space}", durable=durable, stream=STREAM)  # pyright: ignore[reportUnknownMemberType]
    while True:
        try:
            msgs = await sub.fetch(1, timeout=30)
        except TimeoutError:
            continue
        for msg in msgs:
            decision = await asyncio.to_thread(decide, msg.subject, msg.data, launcher)
            match decision:
                case Decision.ACK:
                    await msg.ack()
                case Decision.NAK:
                    await msg.nak(delay=30)
                case Decision.TERM:
                    await msg.term()


async def amain() -> None:
    harden_process()  # the broker holds the ServiceAccount token and its Vault login
    configure_logging()
    spaces = [SpaceId(s) for s in os.environ["KC_SPACES"].split(",")]
    vault_addr = os.environ.get("KC_VAULT_ADDR", "http://kc-vault:8200")
    broker = RefreshingBrokerVault(httpx.Client(base_url=vault_addr, timeout=30))
    launcher = Launcher(broker, SpaceRouter(RUNNER_SOCKET_ROOT))
    login = broker.kv("nats/ingest")
    nc = await nats.connect(  # pyright: ignore[reportUnknownMemberType]
        os.environ.get("KC_NATS_URL", "nats://kc-nats:4222"),
        user="ingest",
        password=login["password"],
    )
    js: JetStreamContext = nc.jetstream()  # pyright: ignore[reportUnknownMemberType]
    log.info("worker_started", spaces=len(spaces))
    await asyncio.gather(*(consume(js, s, launcher) for s in spaces))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
