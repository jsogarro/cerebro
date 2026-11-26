"""Process-level hardening against same-uid reads of this process's memory."""

import ctypes
import sys

_PR_SET_DUMPABLE = 4


def make_non_dumpable() -> None:
    """Stop same-uid processes reading ``/proc/<pid>/environ`` and friends.

    A non-dumpable process fails the kernel's ptrace-read check for every
    non-root peer, which is what guards the environment secrets from a
    compromised child. Linux only: other platforms have no ``/proc`` to protect.
    """
    if sys.platform != "linux":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, "prctl(PR_SET_DUMPABLE) failed")
