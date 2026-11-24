"""Build an immutable mobile release from a generated Proactive board.

Only selected HTML originals and directly referenced display assets are eligible.
The local receipt and its source paths stay outside the deployable version directory.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import posixpath
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from bs4 import BeautifulSoup, Comment, Doctype

import miaoda_delivery
import bridge_injection


IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
HTML = {".html", ".htm"}
LOCAL_ADDRESS = re.compile(r"file:/+|(?:[A-Za-z]:[/\\](?:Users|Windows|[0-9]+_VibeCoding)[/\\])", re.I)
PRIVATE = re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----|(?:sk-(?:proj-)?|ghp_)[A-Za-z0-9_-]{28,}|(?:Bearer\s+eyJ)[A-Za-z0-9_.-]{24,}")
ACCESS_PARAMETER = re.compile(r"token|secret|signature|credential|password|ticket|api[_-]?key|access[_-]?key|^code$|^x-amz-|^authorization$", re.I)
EXCLUDED = {".git", ".venv", ".ssh", ".codex", "_state", "node_modules", "transcripts", "__pycache__"}
POLICY = ("default-src 'none'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
          "font-src 'self' data:; script-src 'self' 'unsafe-inline' blob:; worker-src 'self' blob:; "
          "connect-src 'self' data: blob:; media-src 'self' blob: data:; form-action 'none'; base-uri 'none'")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def remote_web(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password or
                parts.hostname in {"localhost", "localhost.localdomain"} or "." not in parts.hostname or
                parts.hostname.endswith((".local", ".localhost", ".internal"))):
            return False
        try:
            return ipaddress.ip_address(parts.hostname).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def relative_target(page, ref):
    try:
        parts = urlsplit(ref)
    except ValueError:
        return None
    if parts.scheme or parts.netloc:
        return None
    name = unquote(parts.path)
    if not name:
        return page
    if any(x in name for x in ("\\", "\0", ":")) or name.startswith("/"):
        return None
    return (page.parent / name).resolve()


def can_read(path, roots, suffixes):
    return bool(path and path.suffix.lower() in suffixes and any(
        path.is_relative_to(root) and all(not name.startswith(".") and name.casefold() not in EXCLUDED
                                         for name in path.relative_to(root).parts) for root in roots) and path.is_file())


def clean_unlinked(tag):
    tag.name = "span"
    tag.attrs = {"title": "本机参考材料，未收入本次手机审阅包"}
    tag.append("（本机参考）")


class Bundle:
    def __init__(self, board, stage, roots):
        self.board, self.stage, self.roots = board, stage, roots
        self.docs = {}
        self.images = {}
        self.originals = {}
        self.unavailable = 0
        self.directory = stage / "assets"
        self.directory.mkdir()

    def image(self, path):
        if path not in self.images:
            data = path.read_bytes()
            # Accept image signatures, never copy renamed credentials, SVG or executable content.
            signatures = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".gif": b"GIF8", ".webp": b"RIFF"}
            kind = next((suffix for suffix, magic in signatures.items() if data.startswith(magic)), "")
            if not kind or (kind == ".webp" and data[8:12] != b"WEBP"):
                raise ValueError("无法验证选中显示资源的图像类型：" + path.name)
            asset = "assets/img-" + digest(data) + kind
            if not (self.stage / asset).exists():
                (self.stage / asset).write_bytes(data)
            self.images[path] = asset
        return self.images[path]

    def build(self):
        source = self.board.read_bytes()
        board = BeautifulSoup(source.decode("utf-8-sig"), "html.parser")
        if [n.get("id") for n in board.select("main > section.major")] != ["messages", "files", "calendar", "approvals"]:
            raise ValueError("入口须为本次生成的四类 Link16 审阅工作台")
        self.docs[self.board] = "index.html"
        self.originals[self.board] = digest(source)
        for a in board.select("article a[href]"):
            dest = relative_target(self.board, a["href"])
            if dest in self.docs or not can_read(dest, self.roots, HTML):
                continue
            source = dest.read_bytes()
            sha = digest(source)
            self.docs[dest] = "assets/doc-" + sha + ".html"
            self.originals[dest] = sha
        for path, export in self.docs.items():
            if path == self.board:
                soup = board
            else:
                data = path.read_bytes()
                if digest(data) != self.originals[path]:
                    raise ValueError("原件制作期间已更新，请重新生成：" + path.name)
                soup = BeautifulSoup(data.decode("utf-8-sig"), "html.parser")
            self.format(soup, path, export)
            html = str(soup)
            if LOCAL_ADDRESS.search(html) or PRIVATE.search(html):
                raise ValueError("页面仍含机器路径或私密凭据：" + path.name)
            (self.stage / export).write_text(html, encoding="utf-8", newline="\n")

    def format(self, soup, path, export):
        for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
            node.extract()
        for tag in soup.select("base, iframe, object, embed, form, link[rel~=preconnect], link[rel~=dns-prefetch], .board-cloud"):
            tag.decompose()
        for meta in soup.select("meta[http-equiv]"):
            if meta.get("http-equiv", "").lower() in {"refresh", "content-security-policy"}:
                meta.decompose()
        if not soup.head:
            container = soup.html or soup
            offset = 1 if container.contents and isinstance(container.contents[0], Doctype) else 0
            container.insert(offset, soup.new_tag("head"))
        policy = soup.new_tag("meta", attrs={"http-equiv": "Content-Security-Policy", "content": POLICY})
        soup.head.insert(0, policy)
        if not soup.select_one('meta[name="viewport"]'):
            soup.head.append(soup.new_tag("meta", attrs={"name": "viewport", "content": "width=device-width, initial-scale=1"}))
        # Explicitly named content and same-version navigation only. Do not traverse secondary documents.
        for a in soup.select("a[href]"):
            ref = str(a["href"]).strip()
            if ref.startswith("#"):
                continue
            if remote_web(ref):
                parsed = urlsplit(ref)
                params = parse_qsl(parsed.query) + parse_qsl(parsed.fragment)
                if any(ACCESS_PARAMETER.search(k) for k, _ in params):
                    if path == self.board and parsed.hostname == "www.feishu.cn" and parsed.path == "/calendar/share":
                        a.attrs = {"href": "#calendar", "title": "从本页日程进入飞书原事件"}
                        a.string = "查看工作台日程"
                    else:
                        a.name = "span"
                        a.attrs = {"title": "源链接携带访问凭据"}
                        a.string = "请从原平台打开"
                    continue
                a["rel"] = "noopener noreferrer"
                continue
            dest = relative_target(path, ref)
            target = self.docs.get(dest)
            if not target and can_read(dest, self.roots, IMAGES):
                target = self.image(dest)
            if not target:
                self.unavailable += 1
                clean_unlinked(a)
                continue
            fragment = urlsplit(ref).fragment
            a["href"] = posixpath.relpath(target, posixpath.dirname(export) or ".") + ("#" + fragment if fragment else "")
            if dest in self.originals and dest != self.board:
                a["title"] = "本次随包原稿 · SHA-256 " + self.originals[dest][:12]
        for tag in soup.select("[src], [srcset], [poster], link[href]"):
            tag.attrs.pop("srcset", None)
            for attr in ("src", "poster", "href"):
                ref = tag.get(attr)
                if not ref:
                    continue
                if tag.name == "img" and re.fullmatch(r"data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\s]+", ref):
                    continue
                dest = relative_target(path, ref)
                if tag.name == "img" and can_read(dest, self.roots, IMAGES):
                    tag[attr] = posixpath.relpath(self.image(dest), posixpath.dirname(export) or ".")
                else:
                    raise ValueError("选中原件有尚未纳入的显示依赖：" + path.name + " / " + tag.name)
        for value in soup.find_all(string=True):
            # Never transform bundled executable models or their vectors as ordinary text.
            if value.parent and value.parent.name not in {"script", "style"} and LOCAL_ADDRESS.search(value):
                value.replace_with("本机参考路径已省略")
        for style in soup.find_all("style"):
            css = style.get_text()
            resources = (url.strip("'\" ") for url in re.findall(r"url\(([^)]*)\)", css, re.I))
            if re.search(r"@import\b", css, re.I) or any(not url.startswith(("data:", "#")) for url in resources):
                raise ValueError("选中原件采用尚未纳入的样式资源：" + path.name)


def pack(board, output, extra_roots=()):
    board = board.resolve(strict=True)
    output = output.resolve()
    roots = [board.parent, *(p.resolve(strict=True) for p in extra_roots)]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=output) as tmp:
        stage = Path(tmp) / "site"
        stage.mkdir()
        bundle = Bundle(board, stage, roots)
        bundle.build()
        files = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
        inventory_path = Path(tmp) / "allowlist.json"
        inventory_path.write_text(json.dumps(files), encoding="utf-8")
        inv = miaoda_delivery.inventory(stage, "index.html", inventory_path)
        if any(row["bytes"] > 100_000_000 for row in inv["files"]):
            raise ValueError("单一资源超过妙搭 100 MB 限制，请选择该原件的同版轻量预览")
        version = digest(json.dumps(inv["files"], sort_keys=True).encode())[:20]
        target = output / version
        with bridge_injection.injection_lock(output, "mobile-version", version):
            if target.exists():
                actual = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
                if actual != files or miaoda_delivery.inventory(target, "index.html", inventory_path)["files"] != inv["files"]:
                    raise ValueError("版本目录已被更改；拒绝覆盖")
            else:
                stage.rename(target)
        receipt = {"version": version, "entry": "index.html", "site": str(target),
                   "source_sha256": bundle.originals[board], "bytes": inv["totalBytes"], "files": inv["files"],
                   "originals": [{"file": p.name, "sha256": bundle.originals[p], "entry": entry} for p, entry in bundle.docs.items()],
                   "local_references_marked": bundle.unavailable}
        bridge_injection.atomic_write_json(output / (version + ".receipt.json"), receipt)
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True, help="日常生成的原审阅 HTML")
    parser.add_argument("--out-dir", type=Path, required=True, help="私有成包根目录")
    parser.add_argument("--asset-root", type=Path, action="append", default=[], help="原工作台之外允许随包的资料目录")
    args = parser.parse_args()
    result = pack(args.board, args.out_dir, args.asset_root)
    print(json.dumps({k: result[k] for k in ("version", "site", "bytes", "local_references_marked")}, ensure_ascii=False))
