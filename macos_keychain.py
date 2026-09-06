"""Minimal public Security.framework access for myCamino credentials.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import ctypes
from ctypes.util import find_library


ERR_SEC_ITEM_NOT_FOUND = -25300


def _frameworks():
    security = ctypes.CDLL(
        find_library("Security") or "/System/Library/Frameworks/Security.framework/Security"
    )
    core_foundation = ctypes.CDLL(
        find_library("CoreFoundation")
        or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    security.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemModifyAttributesAndData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.SecKeychainItemFreeContent.restype = ctypes.c_int32
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    return security, core_foundation


def read_generic_password(service: str, account: str) -> str:
    security, core_foundation = _frameworks()
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        ctypes.byref(length),
        ctypes.byref(data),
        ctypes.byref(item),
    )
    try:
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return ""
        if status != 0:
            raise OSError(f"Security.framework returned OSStatus {status}")
        return ctypes.string_at(data, length.value).decode("utf-8")
    finally:
        if data:
            security.SecKeychainItemFreeContent(None, data)
        if item:
            core_foundation.CFRelease(item)


def store_generic_password(service: str, account: str, password: str) -> None:
    security, core_foundation = _frameworks()
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    password_bytes = password.encode("utf-8")
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        None,
        None,
        ctypes.byref(item),
    )
    try:
        if status == 0:
            result = security.SecKeychainItemModifyAttributesAndData(
                item, None, len(password_bytes), password_bytes
            )
        elif status == ERR_SEC_ITEM_NOT_FOUND:
            result = security.SecKeychainAddGenericPassword(
                None,
                len(service_bytes),
                service_bytes,
                len(account_bytes),
                account_bytes,
                len(password_bytes),
                password_bytes,
                None,
            )
        else:
            result = status
        if result != 0:
            raise OSError(f"Security.framework returned OSStatus {result}")
    finally:
        if item:
            core_foundation.CFRelease(item)
