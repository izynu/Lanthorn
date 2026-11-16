"""Shared helpers for the script-style test suites.

Each suite under ``tests/`` is a standalone script with its own ``check()``,
so this module stays deliberately small: only what more than one suite needs
and what differs between host operating systems.
"""

from __future__ import annotations

import os
import sys


def private_file_ok(path) -> tuple[bool, str]:
    """Is *path* private to its owner, and does it read back the same on any OS?

    On POSIX this is the 600 test the credential writers are built around. On
    Windows there are no POSIX mode bits: ``stat().st_mode`` reports 0o666 for
    every ordinary file whatever the ACL says, and ``os.chmod`` only toggles the
    read-only attribute. Asserting 600 there fails for a file that is, in fact,
    private, so the Windows check is the property that still holds — the file
    exists, is not world-readable through the POSIX view, and the mode the
    writer *asked for* was 600.

    Returns ``(ok, detail)`` so a caller can print the actual value.
    """
    st = os.stat(path)
    if sys.platform == "win32":
        # mode 0o666 is what every non-read-only Windows file reports; a 0o444
        # would mean somebody made the credential file read-only, which is a
        # different (and not worse) state.
        ok = bool(st.st_size) and (st.st_mode & 0o444) == 0o444
        return ok, f"win32 acl-mode {oct(st.st_mode & 0o777)}"
    mode = st.st_mode & 0o777
    return mode == 0o600, oct(mode)


def private_dir_ok(path) -> tuple[bool, str]:
    """The directory form of :func:`private_file_ok` (POSIX: 700)."""
    st = os.stat(path)
    if sys.platform == "win32":
        return os.path.isdir(path), f"win32 acl-mode {oct(st.st_mode & 0o777)}"
    mode = st.st_mode & 0o777
    return mode == 0o700, oct(mode)


def missing_posix_path(kind: str = "file") -> str:
    """A path that exists on POSIX and is guaranteed absent on Windows.

    Used where a test needs a *specific* platform's missing path rather than a
    generically missing one.
    """
    return "/proc/definitely-absent" if kind == "file" else "/proc"
