"""Tolerant, read-only audit of the non-well-formed ConfProtocol.xml file."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class SignalDefinition:
    ordinal: int
    cmdid: int
    version: Optional[str]
    description: str
    fields: Tuple[str, ...]


_CMD_RE = re.compile(r'<cmd\s+id="(?P<id>\d+)"(?P<attrs>[^>]*)>', re.I)
_COMMENT_RE = re.compile(r'<!--(?P<text>.*?)-->', re.S)
_FIELD_RE = re.compile(r'<([A-Za-z_][\w.-]*)(?:\s|/?>)')


def scan_conf_protocol(path) -> List[SignalDefinition]:
    """Extract command definitions without parsing or changing protocol XML."""
    text = Path(path).read_text(encoding="utf-8")
    matches = list(_CMD_RE.finditer(text))
    definitions = []
    for index, match in enumerate(matches):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():block_end]
        prefix = text[:match.start()]
        comments = list(_COMMENT_RE.finditer(prefix))
        description = ""
        if comments:
            description = " ".join(comments[-1].group("text").split())
        attrs = match.group("attrs")
        version_match = re.search(r'ver\s*=\s*"([^"]+)"', attrs, re.I)
        fields = []
        for field in _FIELD_RE.findall(block):
            if field.lower() not in {"cmd", "msg"} and field not in fields:
                fields.append(field)
        definitions.append(SignalDefinition(
            ordinal=index + 1,
            cmdid=int(match.group("id")),
            version=version_match.group(1) if version_match else None,
            description=description,
            fields=tuple(fields),
        ))
    return definitions


def audit_summary(definitions: List[SignalDefinition]) -> dict:
    unique = {item.cmdid for item in definitions}
    duplicates = sorted({cmdid for cmdid in unique
                         if sum(item.cmdid == cmdid for item in definitions) > 1})
    return {
        "definitions": len(definitions),
        "unique_cmdids": len(unique),
        "duplicate_cmdids": duplicates,
    }

