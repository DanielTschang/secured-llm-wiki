"""The queue carries IDs only and each user can do only its job."""

import asyncio
import os
from collections.abc import Iterator
from contextlib import ExitStack

import httpx
import nats
import pytest
from nats.js.errors import APIError

from tests.support.cluster import local_cluster_ok, port_forward

pytestmark = pytest.mark.infra
ROOT_TOKEN = "root"  # Vault dev mode


@pytest.fixture(scope="module")
def env() -> Iterator[dict[str, str]]:
    if not local_cluster_ok():
        if os.environ.get("KC_REQUIRE_INFRA") == "1":
            pytest.fail("local cluster not available")
        pytest.skip("local cluster not available (make kind-up)")
    with ExitStack() as stack:
        vp = stack.enter_context(port_forward("kc-vault", 8200))
        np = stack.enter_context(port_forward("kc-nats", 4222))

        def kv(path: str) -> str:
            r = httpx.get(
                f"http://127.0.0.1:{vp}/v1/kv/data/{path}", headers={"X-Vault-Token": ROOT_TOKEN}
            )
            return r.json()["data"]["data"]["password"]

        yield {
            "url": f"nats://127.0.0.1:{np}",
            **{u: kv(f"nats/{u}") for u in ("sync", "ingest", "admin")},
        }


async def _publish(url: str, user: str, password: str, subject: str, data: bytes) -> None:
    nc = await nats.connect(url, user=user, password=password, allow_reconnect=False)
    try:
        await nc.jetstream().publish(subject, data, timeout=5)
    finally:
        await nc.close()


def publish(env: dict[str, str], user: str, subject: str, data: bytes) -> None:
    asyncio.run(_publish(env["url"], user, env[user], subject, data))


def test_stream_caps_message_size(env: dict[str, str]) -> None:
    async def info() -> int:
        nc = await nats.connect(env["url"], user="admin", password=env["admin"])
        try:
            return (await nc.jetstream().stream_info("KC_PAGES")).config.max_msg_size or 0
        finally:
            await nc.close()

    assert asyncio.run(info()) == 512


def test_oversized_message_refused(env: dict[str, str]) -> None:
    # A subject no consumer reads (no space has this ID): nothing reaches ingest even if broken.
    with pytest.raises(APIError) as exc:
        publish(env, "sync", "kc.page.sp_zz_probe", b"x" * 600)
    assert "size" in str(exc.value).lower()


def test_ingest_user_cannot_publish(env: dict[str, str]) -> None:
    errors: list[str] = []

    async def attempt() -> None:
        async def on_error(e: Exception) -> None:
            errors.append(str(e))

        nc = await nats.connect(
            env["url"], user="ingest", password=env["ingest"], error_cb=on_error,
            allow_reconnect=False,
        )  # fmt: skip
        try:
            await nc.publish("kc.page.sp_zz_probe", b"{}")
            await nc.flush(timeout=5)
            await asyncio.sleep(0.5)
        finally:
            await nc.close()

    asyncio.run(attempt())
    assert any("permissions violation" in e.lower() for e in errors), errors


def test_sync_user_can_publish_small_message(env: dict[str, str]) -> None:
    """Positive control for the refusals above. The probe subject has no consumer; the
    message is purged again with the admin user."""
    publish(env, "sync", "kc.page.sp_zz_probe", b"{}")

    async def purge() -> None:
        nc = await nats.connect(env["url"], user="admin", password=env["admin"])
        try:
            await nc.jetstream().purge_stream("KC_PAGES", subject="kc.page.sp_zz_probe")
        finally:
            await nc.close()

    asyncio.run(purge())
