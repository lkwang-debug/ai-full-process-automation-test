"""Redact sensitive fields in Allure attachments before HTML generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sessionmanager_py")]

from meeting_test_runtime.allure_reporting import _SECRET_XML
from meeting_test_runtime.redaction import redact


def sanitize(value):
    value = redact(value)
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _SECRET_XML.sub(r"\1***REDACTED***\2", value)
    return value


def sanitize_folder(folder: Path) -> int:
    changed = 0
    for path in folder.glob("*-attachment.json"):
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        cleaned = sanitize(original)
        if cleaned != original:
            path.write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2),
                encoding="utf-8")
            changed += 1
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", nargs="+", type=Path)
    args = parser.parse_args()
    for folder in args.folders:
        print(f"{folder}: sanitized {sanitize_folder(folder)} attachments")


if __name__ == "__main__":
    main()
