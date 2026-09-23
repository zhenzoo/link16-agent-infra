#!/usr/bin/env python3
"""Resolve a Feishu URL to an explicitly mapped local HTML entry, read only."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("需要完整的 http(s) 飞书链接")
    return f"{parts.hostname.lower()}{unquote(parts.path).rstrip('/')}"


def candidate_metadata(root: Path, url: str) -> list[Path]:
    needle = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    rg = shutil.which("rg")
    if rg:
        result = subprocess.run(
            [rg, "-l", "--fixed-strings", "--glob", "meta.json",
             "--glob", "*.meta.json", "--", needle, str(root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr.strip() or "rg 索引失败")
        return [Path(line) for line in result.stdout.splitlines() if line]
    return [path for pattern in ("meta.json", "*.meta.json")
            for path in root.rglob(pattern)
            if needle in path.read_text(encoding="utf-8-sig", errors="replace")]


def local_html(meta: Path, root: Path, entry: str, *, snapshot: bool = False) -> Path | None:
    path = Path(entry)
    bases = (root,) if path.is_absolute() else (meta.parent, *meta.parents)
    for base in bases:
        candidate = (base / path).resolve()
        if not candidate.is_relative_to(root):
            continue
        if candidate.suffix.lower() == ".html" and candidate.is_file():
            return candidate
        if snapshot and candidate.is_dir() and (candidate / "content.html").is_file():
            return candidate / "content.html"
    return None


def matching_entries(node: object, wanted: str, document: dict) -> tuple[list[tuple[str, str]], bool]:
    entries: list[tuple[str, str]] = []
    matched = False
    if isinstance(node, dict):
        online = node.get("online_url")
        if isinstance(online, str) and normalize_url(online) == wanted:
            matched = True
            explicit = node.get("local_entry", node.get("primary_entry"))
            if isinstance(explicit, str):
                entries.append((explicit, "review_page"))
            elif node is document.get("online_delivery") and isinstance(document.get("primary_entry"), str):
                entries.append((document["primary_entry"], "review_page"))
            elif isinstance(node.get("local_path"), str):
                entries.append((node["local_path"], "document_snapshot"))
        for value in node.values():
            nested, nested_match = matching_entries(value, wanted, document)
            entries.extend(nested)
            matched = matched or nested_match
    elif isinstance(node, list):
        for value in node:
            nested, nested_match = matching_entries(value, wanted, document)
            entries.extend(nested)
            matched = matched or nested_match
    return entries, matched


def resolve(url: str, root: Path) -> dict:
    wanted = normalize_url(url)
    found: list[dict] = []
    matched_metadata: list[str] = []
    for meta in candidate_metadata(root, url):
        try:
            document = json.loads(meta.read_text(encoding="utf-8-sig"))
            entries, matched = matching_entries(document, wanted, document)
        except (OSError, ValueError, TypeError):
            continue
        if not matched:
            continue
        matched_metadata.append(str(meta.resolve()))
        for entry, kind in entries:
            html = local_html(meta, root, entry, snapshot=kind == "document_snapshot")
            if html and str(html) not in {item["html"] for item in found}:
                found.append({"html": str(html), "kind": kind, "metadata": str(meta.resolve())})
    return {"url": url, "status": "found" if found else ("metadata_only" if matched_metadata else "not_indexed"),
            "matches": found, "metadata": matched_metadata}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="飞书文档或妙搭页面的完整 URL")
    parser.add_argument("--root", type=Path, default=os.environ.get("VIBECODING_ROOT"),
                        help="本地项目根目录；默认 VIBECODING_ROOT")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.root or not args.root.is_dir():
        parser.error("请设置 VIBECODING_ROOT 或传 --root <项目总目录>")
    try:
        result = resolve(args.url, args.root.resolve())
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["matches"]:
        for item in result["matches"]:
            print(item["html"])
            print("类型：在线文档正文快照" if item["kind"] == "document_snapshot" else "类型：交互评审页面")
            print(f"映射来源：{item['metadata']}")
    else:
        print("找到元数据但没有明确且存在的本地 HTML 映射" if result["metadata"] else "本地元数据没有索引这个链接")
        for meta in result["metadata"]:
            print(f"元数据：{meta}")
    return 0 if result["matches"] else 1


if __name__ == "__main__":
    sys.exit(main())
