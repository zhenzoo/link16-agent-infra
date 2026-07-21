#!/usr/bin/env python3
"""Pure, idempotent link safety rules for Feishu-bound text."""
from __future__ import annotations

import re


_CODE_REGION_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
_MD_LINK_RE = re.compile(
    r"(?<!!)\[([^\]\n]+)\]\((<[^>\n]+>|(?:\\.|[^()\n]|\([^()\n]*\))+)\)"
)
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE_RE = re.compile(r"^/?[A-Za-z]:[\\/]")
_BARE_FILE_URI_RE = re.compile(r"(?<![\w(])file:///[^\s<>\]\)]+", re.IGNORECASE)


def _target_core(raw: str) -> str:
    target = (raw or "").strip()
    if target.startswith("<") and target.endswith(">"):
        return target[1:-1].strip()
    return target.split(maxsplit=1)[0] if target else ""


def _is_external(target: str) -> bool:
    return target.lower().startswith(("http://", "https://"))


def _is_local(target: str) -> bool:
    low = target.lower()
    if not target or target.startswith("#") or _is_external(target):
        return False
    if low.startswith("file:///") or _WINDOWS_DRIVE_RE.match(target):
        return True
    if target.startswith(("\\\\", "/")):
        return True
    # mailto:, tel:, data:, and other explicit schemes are not filesystem paths.
    return not _SCHEME_RE.match(target)


def _local_display(target: str) -> str:
    shown = target.strip()
    if shown.lower().startswith("file:///"):
        shown = shown[8:]
    if re.match(r"^/[A-Za-z]:[\\/]", shown):
        shown = shown[1:]
    return shown


def _inline_code(value: str) -> str:
    return f"`` {value} ``" if "`" in value else f"`{value}`"


def _is_delivery_url(url: str) -> bool:
    m = re.match(r"^https?://([^/?#]+)([^?#]*)", url, re.IGNORECASE)
    if not m:
        return False
    host, path = m.group(1).lower(), m.group(2).lower()
    is_docx = path.startswith("/docx/") and (
        host == "feishu.cn" or host.endswith(".feishu.cn")
        or host == "larksuite.com" or host.endswith(".larksuite.com")
    )
    return is_docx or host.endswith(".pages.dev")


def _stands_alone(segment: str, match: re.Match[str]) -> bool:
    start = segment.rfind("\n", 0, match.start()) + 1
    end = segment.find("\n", match.end())
    if end < 0:
        end = len(segment)
    line = segment[start:end].strip()
    whole = match.group(0)
    if line == whole:
        return True
    return bool(re.fullmatch(r"(?:[-*+]\s+|\d+[.)]\s+)" + re.escape(whole), line))


def _visible_non_code_text(text: str) -> str:
    parts = _CODE_REGION_RE.split(text or "")
    visible = []
    for index in range(0, len(parts), 2):
        visible.append(_MD_LINK_RE.sub(lambda m: m.group(1), parts[index]))
    return "\n".join(visible)


def sanitize_outbound_links(text: str) -> str:
    """Make local targets honest and important external targets identifiable.

    Code regions, anchors, images, and ordinary inline web links are preserved.
    The function is intentionally idempotent because card and fallback paths may
    both apply it.
    """
    if not text:
        return text or ""

    exposed = []
    parts = _CODE_REGION_RE.split(text)
    for index in range(0, len(parts), 2):
        segment = parts[index]

        def replace_link(match: re.Match[str]) -> str:
            label, raw_target = match.group(1), match.group(2)
            target = _target_core(raw_target)
            if _is_local(target):
                return f"{label} — {_inline_code(_local_display(target))}"
            if _is_external(target) and label.strip() != target:
                if _is_delivery_url(target) or _stands_alone(segment, match):
                    if target not in exposed:
                        exposed.append(target)
            return match.group(0)

        segment = _MD_LINK_RE.sub(replace_link, segment)
        segment = _BARE_FILE_URI_RE.sub(
            lambda m: _inline_code(_local_display(m.group(0))), segment
        )
        parts[index] = segment

    result = "".join(parts)
    visible = _visible_non_code_text(result)
    missing = [url for url in exposed if url not in visible]
    if missing:
        result = result.rstrip() + "\n\n原始链接：\n" + "\n".join(missing)
    return result
