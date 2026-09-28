"""Sync entrypoint (Kubernetes CronJob). Exits non-zero if any space failed."""

import asyncio
import os
import sys
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx
import nats
from nats.aio.client import Client as NatsClient
from nats.js import JetStreamContext

from kc_events import PageEvent
from kc_labels import SpaceId
from kc_obs import configure_logging, get_logger
from kc_platform.pages import ClientCredentials, PagesClient
from kc_store.context import open_store
from kc_store.vault import VaultClient
from sync.core import sync_space

log = get_logger("sync")

SA_TOKEN = Path(os.environ.get("KC_SA_TOKEN_PATH", "/var/run/secrets/kc/vault-token"))


class Publisher:
    """Synchronous facade over JetStream publish, running the NATS client on its own loop."""

    def __init__(self, url: str, user: str, password: str) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._nc: NatsClient = self._run(nats.connect(url, user=user, password=password))  # pyright: ignore[reportUnknownMemberType]
        self._js: JetStreamContext = self._nc.jetstream()  # pyright: ignore[reportUnknownMemberType]

    def _run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=30)

    def __call__(self, event: PageEvent) -> None:
        self._run(self._js.publish(event.subject, event.to_bytes()))

    def close(self) -> None:
        self._run(self._nc.close())
        self._loop.call_soon_threadsafe(self._loop.stop)


def run(
    spaces: list[SpaceId],
    vault: VaultClient,
    pages: PagesClient,
    publish: Callable[[PageEvent], None],
) -> int:
    failed = 0
    for space in spaces:
        try:
            store, mongo = open_store(space, vault)
            try:
                report = sync_space(space, pages, store, publish)
            finally:
                mongo.close()
            log.info(
                "space_synced",
                space_id=space,
                new=report.new,
                changed=report.changed,
                touched=report.touched,
                unchanged=report.unchanged,
                quarantined=report.quarantined,
                skipped=report.skipped,
                republished=report.republished,
                publish_failed=report.publish_failed,
            )
            failed += report.publish_failed > 0
        except Exception as e:
            # Fail this space, keep going; never log the exception message.
            failed += 1
            log.error("space_failed", space_id=space, error=e)
    return failed


def main() -> None:
    configure_logging()
    spaces = [SpaceId(s) for s in os.environ["KC_SPACES"].split(",")]
    vault_http = httpx.Client(
        base_url=os.environ.get("KC_VAULT_ADDR", "http://kc-vault:8200"), timeout=30
    )
    vault = VaultClient.kubernetes_login(vault_http, "sync", SA_TOKEN.read_text().strip())

    platform_http = httpx.Client(
        base_url=os.environ.get("KC_PLATFORM_URL", "http://mock-platform"), timeout=30
    )
    client = vault.kv("platform/svc_sync")
    pages = PagesClient(
        platform_http,
        service_token=ClientCredentials(
            platform_http, client["client_id"], client["client_secret"]
        ),
    )
    nats_login = vault.kv("nats/sync")
    publisher = Publisher(
        os.environ.get("KC_NATS_URL", "nats://kc-nats:4222"), "sync", nats_login["password"]
    )
    try:
        failed = run(spaces, vault, pages, publisher)
    finally:
        publisher.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
