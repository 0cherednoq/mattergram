"""Private local store files; Windows DACLs and POSIX owner/mode checks."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def directory(path: Path) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise PermissionError("Provisioning directory needs an existing parent and cannot be a link")
    if os.name == "nt":
        from ._windows_acl import private_directory

        private_directory(path)
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        verify(path, is_directory=True)


def verify(path: Path, *, is_directory: bool = False) -> None:
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PermissionError("Provisioning path cannot be a link or reparse point")
    expected = stat.S_ISDIR if is_directory else stat.S_ISREG
    if not expected(info.st_mode) or (not is_directory and info.st_nlink != 1):
        raise PermissionError("Provisioning path must be a private directory or single-link regular file")
    if os.name == "nt":
        from ._windows_acl import verify_acl

        verify_acl(path)
    elif info.st_uid != getattr(os, "getuid")() or info.st_mode & 0o077:
        raise PermissionError("Provisioning path must be owned by this user without group/other access")


def file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        verify(path)
        descriptor = os.open(path, (flags & ~os.O_CREAT & ~os.O_EXCL))
    try:
        verify(path)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor
