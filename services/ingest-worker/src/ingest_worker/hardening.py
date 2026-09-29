"""Process hardening shared by the broker, the runner and tasks. No space-content imports."""

import sys

__all__ = ["READY", "harden_process"]

_PR_SET_DUMPABLE = 4
READY = "ready"


def harden_process() -> None:
    """Make this process non-dumpable: its /proc/<pid>/environ, fd, mem become unreadable
    to other processes of the same UID (e.g. a concurrent task of another space).
    No-op off Linux."""
    if sys.platform != "linux":
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")
