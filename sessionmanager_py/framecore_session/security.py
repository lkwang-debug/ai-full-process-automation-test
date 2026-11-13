from __future__ import annotations

import struct
from typing import Callable, Optional, Protocol

from .constants import (
    AES_BLOCK_SIZE,
    AES_ECB_KEY,
    SM4_BLOCK_SIZE,
    WSESSION_SECURITY_AES_ECB,
    WSESSION_SECURITY_NONE,
    WSESSION_SECURITY_SM4,
    WSESSION_SECURITY_XOR,
    XOR_KEY,
)
from .exceptions import SecurityError

_XOR_KEY_BYTES = struct.pack("!I", XOR_KEY)
_BLOCK_SIZE = AES_BLOCK_SIZE


class Security(Protocol):
    security_type: int

    def encrypt(self, data: bytes) -> bytes: ...

    def decrypt(self, data: bytes) -> bytes: ...


class NoneSecurity:
    security_type = WSESSION_SECURITY_NONE

    def encrypt(self, data: bytes) -> bytes:
        return bytes(data)

    def decrypt(self, data: bytes) -> bytes:
        return bytes(data)


class XorSecurity:
    security_type = WSESSION_SECURITY_XOR

    def encrypt(self, data: bytes) -> bytes:
        # 4 字节对齐的前缀整体异或（大整数一次完成）；尾部不足 4 字节保持不变
        # （与 C++ 实现的线缆行为一致）。
        data = bytes(data)
        full = len(data) & ~3
        if not full:
            return data
        value = int.from_bytes(data[:full], "big")
        key = int.from_bytes(_XOR_KEY_BYTES * (full // 4), "big")
        return (value ^ key).to_bytes(full, "big") + data[full:]

    decrypt = encrypt


class _BlockEcbSecurity:
    block_size = _BLOCK_SIZE
    security_type = -1

    def __init__(
        self,
        encrypt_block: Callable[[bytes], bytes],
        decrypt_block: Callable[[bytes], bytes],
        encrypt_bulk: Optional[Callable[[bytes], bytes]] = None,
        decrypt_bulk: Optional[Callable[[bytes], bytes]] = None,
    ) -> None:
        self._encrypt_block = encrypt_block
        self._decrypt_block = decrypt_block
        # 原生批量接口（如 pycryptodome AES）：一次处理整段，比逐块 Python 循环快得多。
        # None 时回退到逐块循环（SM4/gmssl）。
        self._encrypt_bulk = encrypt_bulk
        self._decrypt_bulk = decrypt_bulk

    def encrypt(self, data: bytes) -> bytes:
        length = len(data)
        if length == 0:
            return bytes((0,))
        last_block_size = length % self.block_size
        if last_block_size == 0:
            padded = data
        else:
            padded = data + bytes(self.block_size - last_block_size)
        if self._encrypt_bulk is not None:
            cipher = self._encrypt_bulk(padded)
        else:
            cipher = b"".join(
                self._encrypt_block(padded[i:i + self.block_size])
                for i in range(0, len(padded), self.block_size))
        return bytes((last_block_size,)) + cipher

    def decrypt(self, data: bytes) -> bytes:
        if len(data) < 1:
            raise SecurityError("encrypted payload too short")
        last_block_size = data[0]
        cipher = data[1:]
        if last_block_size >= self.block_size:
            raise SecurityError("invalid encrypted last-block size")
        if len(cipher) % self.block_size != 0:
            raise SecurityError("encrypted payload not block-aligned")
        if not cipher and last_block_size != 0:
            raise SecurityError("encrypted payload has size marker without a block")
        if self._decrypt_bulk is not None:
            plain = self._decrypt_bulk(cipher)
        else:
            plain = b"".join(
                self._decrypt_block(cipher[i:i + self.block_size])
                for i in range(0, len(cipher), self.block_size))
        if last_block_size != 0:
            trim = self.block_size - last_block_size
            plain = plain[:len(plain) - trim]
        return bytes(plain)


def _load_aes_block_cipher():
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise SecurityError(
            "AES-ECB requires pycryptodome (pip install pycryptodome)") from exc

    cipher = AES.new(AES_ECB_KEY, AES.MODE_ECB)

    def encrypt_block(block: bytes) -> bytes:
        return cipher.encrypt(block)

    def decrypt_block(block: bytes) -> bytes:
        return cipher.decrypt(block)

    def encrypt_bulk(buf: bytes) -> bytes:
        return cipher.encrypt(buf)

    def decrypt_bulk(buf: bytes) -> bytes:
        return cipher.decrypt(buf)

    return encrypt_block, decrypt_block, encrypt_bulk, decrypt_bulk


def _load_sm4_block_cipher():
    try:
        from gmssl.sm4 import CryptSM4, SM4_DECRYPT, SM4_ENCRYPT
    except ImportError as exc:
        raise SecurityError(
            "SM4 requires gmssl (pip install gmssl)") from exc

    encryptor = CryptSM4()
    encryptor.set_key(AES_ECB_KEY, SM4_ENCRYPT)
    decryptor = CryptSM4()
    decryptor.set_key(AES_ECB_KEY, SM4_DECRYPT)

    def encrypt_block(block: bytes) -> bytes:
        out = encryptor.crypt_ecb(block)
        if len(out) != SM4_BLOCK_SIZE:
            raise SecurityError("gmssl SM4 provider altered block size; "
                                "raw block provider required")
        return out

    def decrypt_block(block: bytes) -> bytes:
        out = decryptor.crypt_ecb(block)
        if len(out) != SM4_BLOCK_SIZE:
            raise SecurityError("gmssl SM4 provider altered block size; "
                                "raw block provider required")
        return out

    # gmssl 的 crypt_ecb 对非块对齐输入可能改尺寸，保持逐块以沿用现有 size 校验。
    return encrypt_block, decrypt_block, None, None


def create_security(security_type: int) -> Security:
    if security_type == WSESSION_SECURITY_NONE:
        return NoneSecurity()
    if security_type == WSESSION_SECURITY_XOR:
        return XorSecurity()
    if security_type == WSESSION_SECURITY_AES_ECB:
        enc_b, dec_b, enc_bulk, dec_bulk = _load_aes_block_cipher()
        obj = _BlockEcbSecurity(enc_b, dec_b, enc_bulk, dec_bulk)
        obj.security_type = WSESSION_SECURITY_AES_ECB
        return obj  # type: ignore[return-value]
    if security_type == WSESSION_SECURITY_SM4:
        enc_b, dec_b, _enc_bulk, _dec_bulk = _load_sm4_block_cipher()
        obj = _BlockEcbSecurity(enc_b, dec_b)
        obj.block_size = SM4_BLOCK_SIZE
        obj.security_type = WSESSION_SECURITY_SM4
        return obj  # type: ignore[return-value]
    raise SecurityError(f"unsupported security type {security_type}")


_AVAILABILITY_CACHE: dict = {}


def is_security_available(security_type: int) -> bool:
    cached = _AVAILABILITY_CACHE.get(security_type)
    if cached is not None:
        return cached
    try:
        create_security(security_type)
        result = True
    except SecurityError:
        result = False
    _AVAILABILITY_CACHE[security_type] = result
    return result
