import sys

import pytest

from ingest_worker.task import harden_process


@pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux-only; verified in-cluster")
def test_harden_process_makes_task_non_dumpable() -> None:
    import ctypes

    harden_process()
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(3, 0, 0, 0, 0) == 0  # PR_GET_DUMPABLE


def test_harden_process_is_safe_everywhere() -> None:
    harden_process()  # no-op off Linux, must not raise
