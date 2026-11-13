import pytest

from framecore_session import SecurityError, create_security, is_security_available
from framecore_session.constants import (
    WSESSION_SECURITY_AES_ECB,
    WSESSION_SECURITY_NONE,
    WSESSION_SECURITY_SM4,
    WSESSION_SECURITY_XOR,
)
from framecore_session.security import _BlockEcbSecurity

SAMPLES = [
    b"",
    b"a",
    b"ab",
    b"abc",
    b"abcd",
    b"abcde",
    b"\x00\x01\x02\x03\x04\x05\x06\x07",
    b"The quick brown fox jumps over the lazy dog",
    bytes(range(256)),
]


def test_none_security_is_identity():
    sec = create_security(WSESSION_SECURITY_NONE)
    assert sec.security_type == WSESSION_SECURITY_NONE
    for data in SAMPLES:
        assert sec.encrypt(data) == data
        assert sec.decrypt(data) == data


def test_xor_security_roundtrip_and_remainder():
    sec = create_security(WSESSION_SECURITY_XOR)
    for data in SAMPLES:
        cipher = sec.encrypt(data)
        assert sec.decrypt(cipher) == data
    assert sec.encrypt(b"abcd") != b"abcd"
    assert len(sec.encrypt(b"abcde")) == 5


def test_xor_remainder_bytes_left_unchanged():
    sec = create_security(WSESSION_SECURITY_XOR)
    assert sec.encrypt(b"abcde")[4:] == b"e"


def test_ecb_rejects_invalid_last_block_marker():
    sec = _BlockEcbSecurity(lambda block: block, lambda block: block)
    with pytest.raises(SecurityError):
        sec.decrypt(bytes((16,)) + b"x" * 16)
    with pytest.raises(SecurityError):
        sec.decrypt(bytes((1,)))


@pytest.mark.skipif(not is_security_available(WSESSION_SECURITY_AES_ECB),
                    reason="pycryptodome not installed")
def test_aes_ecb_roundtrip_various_lengths():
    sec = create_security(WSESSION_SECURITY_AES_ECB)
    for data in SAMPLES:
        cipher = sec.encrypt(data)
        assert sec.decrypt(cipher) == data
    assert sec.encrypt(b"") == b"\x00"
    assert len(sec.encrypt(b"a")) == 1 + 16
    assert len(sec.encrypt(b"a" * 16)) == 1 + 16
    assert len(sec.encrypt(b"a" * 17)) == 1 + 32


@pytest.mark.skipif(not is_security_available(WSESSION_SECURITY_SM4), reason="gmssl not installed")
def test_sm4_roundtrip_various_lengths():
    sec = create_security(WSESSION_SECURITY_SM4)
    for data in SAMPLES:
        assert sec.decrypt(sec.encrypt(data)) == data
