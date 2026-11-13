from pathlib import Path

from meeting_test_runtime.conf_audit import audit_summary, scan_conf_protocol


ROOT = Path(__file__).resolve().parents[1]


def test_conf_protocol_audit_preserves_duplicate_versions():
    definitions = scan_conf_protocol(ROOT / "ConfProtocol.xml")
    summary = audit_summary(definitions)
    assert summary == {
        "definitions": 127,
        "unique_cmdids": 126,
        "duplicate_cmdids": [6601],
    }
    assert len([item for item in definitions if item.cmdid == 6601]) == 2
