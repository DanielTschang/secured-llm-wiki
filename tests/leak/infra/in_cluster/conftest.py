import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # These run only inside the cluster, in the test-runner Job (see test_storage_isolation.py).
    if os.environ.get("KC_IN_CLUSTER") == "1":
        return
    skip = pytest.mark.skip(reason="runs only inside the cluster (test-runner Job)")
    for item in items:
        if "/in_cluster/" in str(item.path):
            item.add_marker(skip)
