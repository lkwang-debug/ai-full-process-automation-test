"""4u_fmserver 业务报文的 XML 编解码。

线缆格式（在 XmlMsgIOPack 信封之内）：

    <cmd id="30000" ver="1.2" seq="3">
        <UserName val="guest1"/>
        <UserType val="1"/>
        <Server><SrvAddr val="1.2.3.4:1089"/><SrvName val="ConfSrv"/></Server>
    </cmd>

约定（由服务端 WXmlParser 强制）：
  - 根元素必须是 ``<cmd>``。
  - cmdid 在 ``id`` 属性（十进制字符串）；可选 ``ver`` / ``seq`` 属性。
  - 字段值在子元素的 ``val`` 属性里（不是 ``var``）。
  - 支持嵌套子元素（解析响应里的 <Server>/<User> 等）与同名重复元素（自动聚合成 list）。

本模块只做"XML ↔ (cmdid, 字段树)"转换，不关心信封与会话层。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

# 字段树：tag -> 标量(str) | 嵌套字段树(dict) | 同名重复(list)
Fields = Dict[str, Any]

# 线缆默认编码。注意：4u 服务端历史上有 GB2312 路径，若中文乱码/被拒，把 encoding 改 "gb2312"。
DEFAULT_ENCODING = "utf-8"


def _to_str(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _attach(parent: ET.Element, tag: str, value: Any) -> None:
    """把一个字段挂到 parent 下：标量→<tag val=../>，dict→嵌套，list→重复。

    dict 里以 ``@`` 开头的键作为本元素**属性**而非子元素，如 ``{"@val": 123, "Name": "x"}``
    渲染为 ``<tag val="123"><Name val="x"/></tag>``（用于 <VideoPolling val="id">）。"""
    if isinstance(value, (list, tuple)):
        for item in value:
            _attach(parent, tag, item)
        return
    if isinstance(value, dict):
        el = ET.SubElement(parent, tag)
        for sub_tag, sub_val in value.items():
            if sub_tag.startswith("@"):
                el.set(sub_tag[1:], _to_str(sub_val))
            else:
                _attach(el, sub_tag, sub_val)
        return
    ET.SubElement(parent, tag, {"val": _to_str(value)})


def _parse_children(el: ET.Element) -> Fields:
    result: Fields = {}
    for child in el:
        tag = child.tag
        if len(child):  # 有子元素 → 嵌套
            value: Any = _parse_children(child)
            if child.attrib:  # 元素既有属性又有子元素（如 <VideoPolling val="id">）
                value = {**dict(child.attrib), **value}
        elif "val" in child.attrib:
            value = child.get("val") or ""
        elif child.attrib:
            value = dict(child.attrib)  # 兼容个别直接属性写法
        else:
            value = (child.text or "").strip()
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value
    return result


def _decode(data: bytes, encoding: str) -> str:
    for enc in (encoding, DEFAULT_ENCODING, "gb2312", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode(DEFAULT_ENCODING, errors="replace")


class XmlCommand:
    """一条 4u 业务命令（cmdid + 字段树 + ver/seq）。"""

    def __init__(
        self,
        cmdid: int,
        fields: Optional[Fields] = None,
        *,
        ver: str = "1.0",
        seq: Optional[int] = None,
    ) -> None:
        self.cmdid = cmdid
        self.fields: Fields = dict(fields or {})
        self.ver = ver
        self.seq = seq

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)

    def to_bytes(self, encoding: str = DEFAULT_ENCODING) -> bytes:
        attrs = {"id": str(self.cmdid), "ver": self.ver}
        if self.seq is not None:
            attrs["seq"] = str(self.seq)
        root = ET.Element("cmd", attrs)
        for tag, value in self.fields.items():
            _attach(root, tag, value)
        # encoding="unicode" → 不带 XML 声明，贴近 C++ TiXmlElement 序列化
        return ET.tostring(root, encoding="unicode").encode(encoding)

    @classmethod
    def from_bytes(cls, data: bytes, encoding: str = DEFAULT_ENCODING) -> "XmlCommand":
        # Some fmserver replies include the terminating C-string NUL in the payload.
        data = data.rstrip(b"\x00")
        text = _decode(data, encoding)
        root = ET.fromstring(text)
        if root.tag != "cmd":
            raise ValueError(f"business XML root must be <cmd>, got <{root.tag}>")
        cmdid_attr = root.get("id")
        if cmdid_attr is None:
            raise ValueError("business XML command is missing id")
        cmdid = int(cmdid_attr)
        ver = root.get("ver", "1.0")
        seq_attr = root.get("seq")
        seq = int(seq_attr) if seq_attr and seq_attr.lstrip("-").isdigit() else None
        return cls(cmdid, _parse_children(root), ver=ver, seq=seq)

    def __repr__(self) -> str:
        return f"XmlCommand(id={self.cmdid}, ver={self.ver}, fields={self.fields})"
