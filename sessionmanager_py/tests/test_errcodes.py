from fm_client.errcodes import EXCEPTION, FmError, code_name


def test_known_error_code_has_symbolic_name():
    assert code_name(EXCEPTION) == "EXCEPTION"
    assert str(FmError(EXCEPTION)) == "0x1002 EXCEPTION"


def test_unknown_error_code_keeps_hex_fallback():
    assert code_name(0x7FFF) == "0x7FFF"
