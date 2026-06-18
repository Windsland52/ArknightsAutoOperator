"""本地敏感信息加解密（Windows DPAPI）。

用于 settings.json 中的 GitHub token 等本地密钥：
- encrypt_text: 明文 → DPAPI 加密 → base64 字符串
- decrypt_text: base64 字符串 → DPAPI 解密 → 明文

DPAPI 加密结果绑定当前 Windows 用户，拷到其他用户/机器通常无法解密。
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("secure_store 仅支持 Windows DPAPI")


def _blob_from_bytes(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buf = ctypes.create_string_buffer(data)
    blob = _DATA_BLOB()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    return blob, buf


def encrypt_text(text: str) -> str:
    """用 Windows DPAPI 加密文本，返回 base64 字符串。"""
    _require_windows()
    data = text.encode("utf-8")
    in_blob, _buf = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]


def decrypt_text(value: str) -> str:
    """解密 encrypt_text 返回的 base64 字符串。"""
    _require_windows()
    data = base64.b64decode(value.encode("ascii"))
    in_blob, _buf = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        plain = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return plain.decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
