from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kc_events import PageEvent
from kc_labels import SpaceId
from kc_platform.pages import PagesClient
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs
from mock_platform.app import create_app

TESTSET = Path(__file__).parents[3] / "tests/fixtures/synthetic_litho_testset"


class SpyPages(PagesClient):
    """Counts content fetches so tests can assert sync did not fetch."""

    def __init__(self, *a: object, **kw: object) -> None:
        super().__init__(*a, **kw)  # type: ignore[arg-type]
        self.fetched: list[str] = []

    def get_page(self, page_id: str):  # type: ignore[override]
        self.fetched.append(page_id)
        return super().get_page(page_id)


class Harness:
    def __init__(self, platform: TestClient) -> None:
        self.platform = platform
        tok = platform.post("/dev/token", json={"user_id": "svc_sync"}).json()["access_token"]
        self.pages = SpyPages(platform, service_token=tok)
        self.keys = FakeKeyService()
        self.docs: dict[str, MemoryDocs] = {}
        self.blobs: dict[str, MemoryBlobs] = {}
        self.events: list[PageEvent] = []
        self.fail_publish = False

    def store(self, space: str) -> SpaceStore:
        return SpaceStore(
            SpaceId(space),
            keys=self.keys,
            docs=self.docs.setdefault(space, MemoryDocs()),
            blobs=self.blobs.setdefault(space, MemoryBlobs()),
        )

    def publish(self, event: PageEvent) -> None:
        if self.fail_publish:
            raise ConnectionError("nats down")
        self.events.append(event)

    def edit(self, **body: object) -> None:
        assert self.platform.post("/dev/page", json=body).status_code == 200


@pytest.fixture
def h() -> Iterator[Harness]:
    with TestClient(create_app(TESTSET / "manifest.json", dev_endpoints=True)) as c:
        yield Harness(c)
