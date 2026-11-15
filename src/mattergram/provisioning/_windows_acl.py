"""Minimal Win32 ACL bridge; never weakens an existing path's permissions."""

from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
from typing import Any


class SecurityAttributes(c.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]


def _apis() -> tuple[Any, Any]:
    adv = getattr(c, "WinDLL")("advapi32", use_last_error=True)
    kernel = getattr(c, "WinDLL")("kernel32", use_last_error=True)
    signatures = {
        "OpenProcessToken": ([w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
        "GetTokenInformation": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
        "ConvertSidToStringSidW": ([c.c_void_p, c.POINTER(w.LPWSTR)], w.BOOL),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
            [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p],
            w.BOOL,
        ),
        "GetNamedSecurityInfoW": (
            [
                w.LPCWSTR,
                c.c_int,
                w.DWORD,
                c.POINTER(c.c_void_p),
                c.c_void_p,
                c.POINTER(c.c_void_p),
                c.c_void_p,
                c.POINTER(c.c_void_p),
            ],
            w.DWORD,
        ),
        "GetAce": ([c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)], w.BOOL),
    }
    for name, (args, returns) in signatures.items():
        fn = getattr(adv, name)
        fn.argtypes, fn.restype = args, returns
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [c.c_void_p], c.c_void_p
    kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, c.POINTER(SecurityAttributes)]
    kernel.CreateDirectoryW.restype = w.BOOL
    return adv, kernel


def _sid(adv: Any, kernel: Any, pointer: Any) -> str:
    text = w.LPWSTR()
    if not adv.ConvertSidToStringSidW(pointer, c.byref(text)):
        raise PermissionError("Cannot inspect provisioning ACL SID")
    try:
        assert text.value is not None
        return str(text.value)
    finally:
        kernel.LocalFree(text)


def _user(adv: Any, kernel: Any) -> str:
    token = w.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise PermissionError("Cannot inspect provisioning process identity")
    try:
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, buffer, size, c.byref(size)):
            raise PermissionError("Cannot inspect provisioning process token")
        return _sid(adv, kernel, c.cast(buffer, c.POINTER(c.c_void_p))[0])
    finally:
        kernel.CloseHandle(token)


def verify_acl(path: Path) -> None:
    adv, kernel = _apis()
    user = _user(adv, kernel)
    owner, acl, descriptor = c.c_void_p(), c.c_void_p(), c.c_void_p()
    result = adv.GetNamedSecurityInfoW(
        str(path), 1, 5, c.byref(owner), None, c.byref(acl), None, c.byref(descriptor)
    )
    if result:
        raise PermissionError("Cannot inspect provisioning path ACL")
    try:
        if not acl.value or _sid(adv, kernel, owner) not in {user, "S-1-5-18", "S-1-5-32-544"}:
            raise PermissionError("Provisioning path owner/DACL is not private")
        # ACL header: revision, reserved, size, ACE count, reserved.
        count = c.c_ushort.from_address(acl.value + 4).value
        own_full = False
        for index in range(count):
            ace = c.c_void_p()
            if not adv.GetAce(acl, index, c.byref(ace)) or not ace.value:
                raise PermissionError("Cannot inspect provisioning ACE")
            kind = c.c_ubyte.from_address(ace.value).value
            flags = c.c_ubyte.from_address(ace.value + 1).value
            mask = c.c_uint32.from_address(ace.value + 4).value
            sid = _sid(adv, kernel, c.c_void_p(ace.value + 8))
            if kind != 0 or sid not in {user, "S-1-5-18"} or flags & ~0x13:
                raise PermissionError("Provisioning ACL permits an unexpected principal or ACE")
            own_full |= sid == user and mask & 0x1F01FF == 0x1F01FF
        if not own_full:
            raise PermissionError("Provisioning user lacks private full control")
    finally:
        kernel.LocalFree(descriptor)


def private_directory(path: Path) -> None:
    if path.exists():
        from ._private_files import verify

        verify(path, is_directory=True)
        return
    adv, kernel = _apis()
    user = _user(adv, kernel)
    descriptor = c.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)", 1, c.byref(descriptor), None
    ):
        raise PermissionError("Cannot create private provisioning security descriptor")
    try:
        attributes = SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
        if (
            not kernel.CreateDirectoryW(str(path), c.byref(attributes))
            and getattr(c, "get_last_error")() != 183
        ):
            raise PermissionError("Cannot create private provisioning directory")
    finally:
        kernel.LocalFree(descriptor)
    from ._private_files import verify

    verify(path, is_directory=True)
