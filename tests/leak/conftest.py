import logging
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from mock_platform.app import create_app
from tests.leak.harness import TESTSET, Canary, load_canaries, load_users


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/leak/" in str(item.path) and "/infra/" not in str(item.path):
            item.add_marker(pytest.mark.leak)


@pytest.fixture(scope="session")
def canaries() -> list[Canary]:
    return load_canaries()


@pytest.fixture(scope="session")
def users() -> dict[str, frozenset[str]]:
    return load_users()


@pytest.fixture
def platform() -> Iterator[TestClient]:
    with TestClient(create_app(TESTSET / "manifest.json")) as c:
        yield c


@pytest.fixture
def token_for(platform: TestClient) -> Callable[[str], str]:
    def issue(user_id: str) -> str:
        resp = platform.post("/dev/token", json={"user_id": user_id})
        resp.raise_for_status()
        return resp.json()["access_token"]

    return issue


@pytest.fixture
def captured_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture every log record at DEBUG and above for canary scanning."""
    caplog.set_level(logging.DEBUG)
    return caplog
