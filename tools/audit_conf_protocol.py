"""Print a read-only summary of ConfProtocol.xml."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSIONMANAGER = ROOT / "sessionmanager_py"
for path in (ROOT, SESSIONMANAGER):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from meeting_test_runtime.conf_audit import audit_summary, scan_conf_protocol  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("protocol", nargs="?", type=Path, default=ROOT / "ConfProtocol.xml")
    args = parser.parse_args()
    definitions = scan_conf_protocol(args.protocol)
    summary = audit_summary(definitions)
    print(f"definitions={summary['definitions']}")
    print(f"unique_cmdids={summary['unique_cmdids']}")
    print(f"duplicate_cmdids={summary['duplicate_cmdids']}")


if __name__ == "__main__":
    main()
