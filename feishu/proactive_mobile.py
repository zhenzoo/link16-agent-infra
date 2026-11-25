"""Build an immutable mobile release from a generated Proactive board.

Only selected HTML originals and directly referenced display assets are eligible.
The local receipt and its source paths stay outside the deployable version directory.
"""
from __future__ import annotations

import argparse
import hashlib
import csv
import io
import ipaddress
import json
import os
import posixpath
import re
import tempfile
import wave
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from bs4 import BeautifulSoup, Comment, Doctype

import miaoda_delivery
import bridge_injection


IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO = {".mp4"}
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
    def __init__(self, board, stage, roots, *, archive_paths=False):
        self.board, self.stage, self.roots = board, stage, roots
        self.docs = {}
        self.images = {}
        self.videos = {}
        self.audio = {}
        self.originals = {}
        self.frame_ids = {}
        self.has_document_frame = False
        self.catalogs = set()
        self.archived = {}
        self.current_archive_exports = {}
        self.archive_versions = {}
        self.unavailable = 0
        self.directory = stage / "assets"
        self.archive_paths = archive_paths
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

    def video(self, path):
        if path not in self.videos:
            if path.stat().st_size > 100_000_000:
                raise ValueError("单段视频超过妙搭 100 MB 限制：" + path.name)
            data = path.read_bytes()
            if len(data) < 16 or data[4:8] != b"ftyp":
                raise ValueError("无法验证选中视频的 MP4 类型：" + path.name)
            asset = "assets/video-" + digest(data) + ".mp4"
            if not (self.stage / asset).exists():
                (self.stage / asset).write_bytes(data)
            self.videos[path] = asset
        return self.videos[path]

    def wav(self, path):
        if path not in self.audio:
            if path.stat().st_size > 100_000_000:
                raise ValueError("单个音频超过妙搭 100 MB 限制：" + path.name)
            with wave.open(str(path), "rb") as stream:
                frames = stream.getnframes()
                if not frames or len(stream.readframes(frames)) != frames * stream.getnchannels() * stream.getsampwidth():
                    raise ValueError("WAV 样本数据不完整：" + path.name)
            data = path.read_bytes()
            entry = "assets/audio-" + digest(data) + ".wav"
            (self.stage / entry).write_bytes(data)
            self.audio[path] = entry
        return self.audio[path]

    def build(self):
        source = self.board.read_bytes()
        board = BeautifulSoup(source.decode("utf-8-sig"), "html.parser")
        sections = [n.get("id") for n in board.select("main > section.major")]
        allowed = {"messages", "files", "calendar", "approvals"}
        legacy = sections == ["messages", "files", "reminders", "calendar", "approvals"]
        business = sections == ["business", "approvals", "closed"]
        if business:
            from proactive_business import result
            for status in board.select("#business .result-status"):
                result({"type":"status","text":status.get_text()})
            for metric in board.select("#business .facts span"):
                value,unit = metric.get("data-value"),metric.get("data-unit")
                if value is None or not unit:
                    raise ValueError("业务指标缺少数值或单位")
                result({"type":"metrics","items":[{"label":"指标","value":float(value),"unit":unit}]})
            for medium in board.select("#business .attachments img, #business .attachments video"):
                original = relative_target(self.board,medium["src"])
                if not can_read(original,self.roots,VIDEO if medium.name=="video" else IMAGES) or digest(original.read_bytes()) != medium.get("data-sha256"):
                    raise ValueError("业务配图和登记的源指纹不符")
            if any(len(row.find_all(["td","th"],recursive=False))!=4 for row in board.select("#business tr[data-business-id]")):
                raise ValueError("业务行应为四列")
        if not legacy and not business and (not sections or len(sections) != len(set(sections)) or not set(sections) <= allowed):
            raise ValueError("入口须为本次生成的 Link16 审阅工作台")
        self.docs[self.board] = "index.html"
        self.originals[self.board] = digest(source)
        for anchor in board.select("#closed .archive-links a[data-archive-version]"):
            version = anchor["data-archive-version"]
            sha = anchor["data-source-sha256"]
            path = relative_target(self.board, anchor["href"])
            if (not can_read(path, self.roots, HTML) or not re.fullmatch(r"[a-f0-9]{20}", version)
                    or not re.fullmatch(r"[a-f0-9]{64}", sha)):
                raise ValueError("归档原件路径或验收版本无效")
            if version not in self.archive_versions:
                directory = self.stage.parent.parent / version
                receipt = json.loads(directory.with_suffix(".receipt.json").read_text("utf-8"))
                if receipt["version"] != version or digest(json.dumps(receipt["files"], sort_keys=True).encode())[:20] != version:
                    raise ValueError("旧手机包与归档回执不一致")
                self.archive_versions[version] = (directory, receipt, {r["path"]: r for r in receipt["files"]})
            directory, receipt, inventory = self.archive_versions[version]
            original = next((o for o in receipt["originals"] if o["file"] == path.name and o["sha256"] == sha), None)
            if not original or original["entry"] != "assets/doc-" + sha + ".html":
                raise ValueError("已验手机包中找不到归档原稿：" + path.name)
            export = original["entry"]
            self.copy_archived(directory, inventory, export)
            self.archived[path, sha] = original
            if path not in self.current_archive_exports and digest(path.read_bytes()) == sha:
                self.current_archive_exports[path] = export
        for a in board.select("article a[href], #business tr[data-business-id] a[href], #closed .archive-links a[href]"):
            if a.has_attr("data-archive-version"):
                continue
            dest = relative_target(self.board, a["href"])
            if a.attrs.pop("data-review-catalog", "") == "entries" and can_read(dest, self.roots, HTML):
                self.catalogs.add(dest)
            if dest in self.docs or not can_read(dest, self.roots, HTML):
                continue
            source = dest.read_bytes()
            sha = digest(source)
            self.docs[dest] = "assets/doc-" + sha + ".html"
            self.originals[dest] = sha
        for path, export in self.docs.items():
            if path == self.board:
                from proactive_business import DOCUMENT_TYPES
                soup = board
            else:
                data = path.read_bytes()
                if digest(data) != self.originals[path]:
                    raise ValueError("原件制作期间已更新，请重新生成：" + path.name)
                soup = BeautifulSoup(data.decode("utf-8-sig"), "html.parser")
                if path in self.frame_ids:
                    meta = soup.find("meta", attrs={"name": "review-fragments"})
                    declared = set(meta.get("content", "").split()) if meta else set()
                    for fragment in self.frame_ids[path]:
                        if fragment not in declared or len(soup.find_all(id=fragment)) != 1:
                            raise ValueError(f"原稿缺少声明或唯一可预览锚点：{path.name}#{fragment}")
            # Machine paths are private by default. The owner can opt in to the path
            # of an archived original that is itself selected in the very same row.
            archive_labels = []
            if path == self.board:
                for code in soup.select("#business tr[data-business-id] code[data-local-original]"):
                    selected = Path(code.get_text())
                    summary = code.parent.find("summary")
                    roots = [self.board.parent]
                    if os.environ.get("LINK16_AGENT_INFRA_ROOT"):
                        roots.append(Path(os.environ["LINK16_AGENT_INFRA_ROOT"])/"docs")
                    if (not self.archive_paths or not selected.is_absolute() or not summary
                            or selected.name != summary.get_text()
                            or selected.suffix.lower() not in DOCUMENT_TYPES or not selected.is_file()
                            or not any(selected.resolve().is_relative_to(r.resolve()) for r in roots)):
                        code.parent.decompose()
                        continue
                    code.clear()
                    archive_labels.append((code,selected))
            for code in soup.select("#closed code[data-archive-document], .review-topic code[data-archive-document]") if path == self.board else []:
                ref = code.attrs.pop("data-archive-document")
                selected = relative_target(path, ref)
                row = code.find_parent("tr") or code.find_parent(["article","details"], class_="review-topic")
                code.clear()
                if (self.archive_paths and (selected in self.originals or any(p == selected for p, _ in self.archived)) and selected != self.board and row
                        and any(relative_target(path, a["href"]) == selected for a in row.select(".archive-links a[href], .record-links a[href]"))):
                    archive_labels.append((code, selected))
                else:
                    code.parent.decompose()
            self.format(soup, path, export)
            html = str(soup)
            if LOCAL_ADDRESS.search(html) or PRIVATE.search(html):
                raise ValueError("页面仍含机器路径或私密凭据：" + path.name)
            for label, original in archive_labels:
                label.string = str(original)
            if archive_labels:
                html = str(soup)
            (self.stage / export).write_text(html, encoding="utf-8", newline="\n")

    def copy_archived(self, directory, inventory, entry):
        """Retain only the selected original and its immutable in-package dependencies."""
        if not re.fullmatch(r"assets/[a-zA-Z0-9._-]+", entry) or entry not in inventory:
            raise ValueError("归档文件未列入已验手机包：" + entry)
        dest = self.stage / entry
        expected = inventory[entry]["sha256"]
        if dest.exists():
            if digest(dest.read_bytes()) != expected:
                raise ValueError("归档资源与本包的同名文件不同：" + entry)
            return
        data = (directory / entry).read_bytes()
        if digest(data) != expected:
            raise ValueError("已验手机包的归档资源发生变化：" + entry)
        dest.write_bytes(data)
        if dest.suffix not in HTML:
            return
        doc = BeautifulSoup(data.decode("utf-8"), "html.parser")
        names = {node.get(key) for key in ("src", "href", "poster") for node in doc.select(f"[{key}]")}
        for manifest in doc.select('script[type="application/json"][id$="-sources"]'):
            names.update(json.loads(manifest.get_text()).values())
        for name in names:
            # Generated bundles store documents and dependencies together in assets/.
            parts = urlsplit(name)
            if not parts.scheme and not parts.netloc and parts.path:
                self.copy_archived(directory, inventory, "assets/" + parts.path)

    def prepare_frames(self, soup, path):
        for frame in soup.find_all("iframe"):
            if path == self.board and frame.has_attr("data-review-document"):
                ref = frame["data-review-document"]
                parts = urlsplit(ref)
                dest = relative_target(path, ref)
                card = frame.find_parent("article")
                if (not card or not any(a["href"] == ref for a in card.select("a[href]"))
                        or parts.query or dest == path or dest not in self.docs or not can_read(dest, self.roots, HTML)
                        or frame.get("src") != ref):
                    raise ValueError("全文预览须为同卡已选择的原稿")
                self.has_document_frame = True
                frame.attrs = {**{k: frame[k] for k in ("class","title") if k in frame.attrs},
                               "src": self.docs[dest] + ("#" + quote(unquote(parts.fragment), safe="") if parts.fragment else ""),
                               "sandbox":"allow-scripts","loading":"lazy","referrerpolicy":"no-referrer"}
                continue
            if path != self.board or not frame.has_attr("data-review-fragment"):
                frame.decompose()
                continue
            ref = frame["data-review-fragment"]
            link = urlsplit(ref)
            src = frame.get("src", "")
            embed = urlsplit(src)
            dest = relative_target(path, ref)
            fragment = unquote(link.fragment)
            card = frame.find_parent("article") or frame.find_parent("tr",attrs={"data-business-id":True})
            same_card = card and any(
                relative_target(path, a["href"]) == dest and unquote(urlsplit(a["href"]).fragment) == fragment
                for a in card.select("a[href]"))
            if (not same_card or dest not in self.docs or dest == path or link.query or not fragment or
                    not can_read(dest, self.roots, HTML) or relative_target(path, src) != dest or
                    unquote(embed.fragment) != fragment or parse_qsl(embed.query, keep_blank_values=True) != [("preview", fragment)]):
                raise ValueError("嵌入预览须指向同卡原件、同章锚点与唯一 preview 参数")
            self.frame_ids.setdefault(dest, set()).add(fragment)
            ident = quote(fragment, safe="")
            frame.attrs = {**{k: frame[k] for k in ("class", "title", "height", "width") if k in frame.attrs},
                           "src": self.docs[dest] + "?preview=" + ident + "#" + ident,
                           "loading": "lazy", "sandbox": "allow-scripts", "referrerpolicy": "no-referrer"}

    def copy_accessory_sources(self, soup, path):
        manifests = soup.find_all(id=re.compile(r"^(?:step\d{4}-accessory|link16-(?:asset|accessory))-sources$"))
        if not manifests:
            return False
        manifest = manifests[0]
        if (path == self.board or manifest.name != "script" or manifest.get("type") != "application/json"
                or len(manifests) != 1):
            raise ValueError("资源清单须来自所选原页且唯一")
        sources = json.loads(manifest.get_text())
        prefix = manifest["id"].split("-", 1)[0]
        if not isinstance(sources, dict) or not sources or any(not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", k) for k in sources):
            raise ValueError("资源清单缺少有效的同版模块")
        if prefix != "link16" and set(sources) != {"dock", "bag", "hub", "mount"}:
            raise ValueError("旧版配件清单须保留全部四个模块")
        for key, name in sources.items():
            match = isinstance(name, str) and re.fullmatch(rf"{prefix}-{key}-([0-9a-f]{{16}})\.js", name)
            source = (path.parent / name).resolve() if match else None
            if not match or not can_read(source, [path.parent], {".js"}) or source.parent != path.parent:
                raise ValueError(f"原页缺少同目录源文件：{key}")
            data = source.read_bytes()
            if (not digest(data).startswith(match[1]) or
                    not re.fullmatch(rb'document\.currentScript\.payload="H4sI[A-Za-z0-9+/]*={0,2}";\s*', data)):
                raise ValueError(f"原页文件名指纹或压缩数据不符：{key}")
            target = self.directory / name
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f"同名配件源文件版本冲突：{key}")
            target.write_bytes(data)
        return True

    def format(self, soup, path, export):
        for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
            node.extract()
        for tag in soup.select("base, object, embed, form, link[rel~=preconnect], link[rel~=dns-prefetch], .board-cloud"):
            tag.decompose()
        self.prepare_frames(soup, path)
        for meta in soup.select("meta[http-equiv]"):
            if meta.get("http-equiv", "").lower() in {"refresh", "content-security-policy"}:
                meta.decompose()
        if not soup.head:
            container = soup.html or soup
            offset = 1 if container.contents and isinstance(container.contents[0], Doctype) else 0
            container.insert(offset, soup.new_tag("head"))
        policy = POLICY
        if path in self.catalogs:
            self.format_catalog(soup, path, export)
            policy += "; frame-src 'self'"
        if self.copy_accessory_sources(soup, path):
            policy += "; frame-src 'self'"
        if path == self.board:
            policy = policy.replace("base-uri 'none'", "base-uri 'self'")
            if self.frame_ids or self.has_document_frame:
                policy += "; frame-src 'self'"
        policy = soup.new_tag("meta", attrs={"http-equiv": "Content-Security-Policy", "content": policy})
        soup.head.insert(0, policy)
        if not soup.select_one('meta[name="viewport"]'):
            soup.head.append(soup.new_tag("meta", attrs={"name": "viewport", "content": "width=device-width, initial-scale=1"}))
        # Explicitly named content and same-version navigation only. Do not traverse secondary documents.
        for a in soup.select("a[href]"):
            ref = str(a["href"]).strip()
            message_receipt = a.has_attr("data-message-permalink") and path == self.board
            a.attrs.pop("data-message-permalink", None)
            if ref.startswith("#"):
                continue
            if remote_web(ref):
                parsed = urlsplit(ref)
                params = parse_qsl(parsed.query) + parse_qsl(parsed.fragment)
                # Feishu's copied single-message permalink uses a message selector named
                # token. Preserve only explicitly selected receipts on its canonical route.
                message_receipt = (message_receipt and parsed.netloc == "applink.feishu.cn"
                                   and parsed.path == "/client/message/link/open" and not parsed.fragment
                                   and len(params) == 1 and params[0][0] == "token"
                                   and re.fullmatch(r"[A-Za-z0-9_+/=-]{8,256}", params[0][1]))
                if any(ACCESS_PARAMETER.search(k) for k, _ in params) and not message_receipt:
                    if (path == self.board and soup.select_one("main > section#calendar") and
                            parsed.hostname == "www.feishu.cn" and parsed.path == "/calendar/share"):
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
            archived = self.archived.get((dest, a.attrs.pop("data-source-sha256", ""))) if path == self.board else None
            a.attrs.pop("data-archive-version", None)
            target = archived["entry"] if archived else self.docs.get(dest)
            if not target:
                target = self.current_archive_exports.get(dest)
            if not target and can_read(dest, self.roots, IMAGES):
                target = self.image(dest)
            if not target and can_read(dest, self.roots, VIDEO):
                target = self.video(dest)
            if not target and path == self.board and a.find_parent("article") and can_read(dest, self.roots, {".csv"}):
                data = dest.read_bytes()
                text = data.decode("utf-8-sig")
                rows = csv.reader(io.StringIO(text), strict=True)
                header = next(rows, [])
                if ("\x00" in text or PRIVATE.search(text) or LOCAL_ADDRESS.search(text)
                        or len(header) < 2 or any(len(row) != len(header) for row in rows)):
                    raise ValueError("所选 CSV 缺少一致的列或含有私密字段：" + dest.name)
                target = "assets/table-" + digest(data) + ".csv"
                (self.stage / target).write_bytes(data)
            if not target:
                self.unavailable += 1
                clean_unlinked(a)
                continue
            fragment = urlsplit(ref).fragment
            a["href"] = posixpath.relpath(target, posixpath.dirname(export) or ".") + ("#" + fragment if fragment else "")
            if archived:
                a["title"] = "已验归档原稿 · SHA-256 " + archived["sha256"][:12]
            elif dest in self.originals and dest != self.board:
                a["title"] = "本次随包原稿 · SHA-256 " + self.originals[dest][:12]
        for tag in soup.select("video, [src], [srcset], [poster], link[href]"):
            if tag.name == "iframe":
                continue  # The card, URL, and owner-reviewed document contract are checked together above.
            if tag.name in ("audio", "video"):
                tag.attrs.pop("autoplay", None)
                tag["preload"] = "none"
                tag["controls"] = ""
                if tag.name == "video":
                    tag["playsinline"] = ""
            tag.attrs.pop("srcset", None)
            for attr in ("src", "poster", "href"):
                ref = tag.get(attr)
                if not ref:
                    continue
                if tag.name == "img" and re.fullmatch(r"data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\s]+", ref):
                    continue
                dest = relative_target(path, ref)
                if (tag.name == "img" or (tag.name == "video" and attr == "poster")) and can_read(dest, self.roots, IMAGES):
                    tag[attr] = posixpath.relpath(self.image(dest), posixpath.dirname(export) or ".")
                elif (attr == "src" and (tag.name == "video" or (tag.name == "source" and tag.parent.name == "video"))
                      and can_read(dest, self.roots, VIDEO)):
                    tag[attr] = posixpath.relpath(self.video(dest), posixpath.dirname(export) or ".")
                elif (attr == "src" and (tag.name == "audio" or (tag.name == "source" and tag.parent.name == "audio"))
                      and can_read(dest, self.roots, {".wav"})):
                    tag[attr] = posixpath.relpath(self.wav(dest), posixpath.dirname(export) or ".")
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

    def format_catalog(self, soup, path, export):
        """An explicitly selected dynamic directory may navigate only already selected local originals."""
        candidates = [s for s in soup.find_all("script") if (s.string or "").lstrip().startswith("const DATA=")]
        if len(candidates) != 1:
            raise ValueError("目录中找不到唯一的条目数据：" + path.name)
        script = candidates[0]
        text = script.string
        start = text.index("const DATA=") + len("const DATA=")
        data, end = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError("目录格式与选择的入口不同：" + path.name)
        for entry in data["entries"]:
            online = entry.get("online", "")
            if online:
                parsed = urlsplit(online)
                params = parse_qsl(parsed.query) + parse_qsl(parsed.fragment)
                # An original catalog can carry public HTTP manufacturer sheets as external links.
                public_host = parsed.scheme in ("https", "http") and remote_web(parsed._replace(scheme="https").geturl())
                if not public_host or any(ACCESS_PARAMETER.search(k) for k, _ in params):
                    entry["online"] = ""
            for key in ("local", "preview", "previewURL", "rawURL"):
                ref = entry.get(key)
                if not ref:
                    continue
                dest = relative_target(path, ref)
                target = self.docs.get(dest) or self.current_archive_exports.get(dest)
                fragment = urlsplit(ref).fragment
                entry[key] = (posixpath.relpath(target, posixpath.dirname(export)) +
                              ("#" + fragment if fragment else "")) if target else ""
            entry["missingLocal"] = not bool(entry.get("local"))
            if not entry.get("previewURL"):
                entry["previewMode"] = ""
                entry["previewNote"] = "请从该项在线入口或本机目录查看。"
            else:
                entry["previewNote"] = "本次工作台随包原件"
        data["online"] = ""  # The mirrored directory itself is the current edition.
        script.string = text[:start] + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + text[start + end:]


def pack(board, output, extra_roots=(), *, archive_paths=False):
    board = board.resolve(strict=True)
    output = output.resolve()
    roots = [board.parent, *(p.resolve(strict=True) for p in extra_roots)]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=output) as tmp:
        stage = Path(tmp) / "site"
        stage.mkdir()
        bundle = Bundle(board, stage, roots, archive_paths=archive_paths)
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
                   "originals": list({r["entry"]: r for r in [
                       *({"file": p.name, "sha256": bundle.originals[p], "entry": entry} for p, entry in bundle.docs.items()),
                       *bundle.archived.values()]}.values()),
                   "local_references_marked": bundle.unavailable}
        bridge_injection.atomic_write_json(output / (version + ".receipt.json"), receipt)
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True, help="日常生成的原审阅 HTML")
    parser.add_argument("--out-dir", type=Path, required=True, help="私有成包根目录")
    parser.add_argument("--asset-root", type=Path, action="append", default=[], help="原工作台之外允许随包的资料目录")
    parser.add_argument("--archive-paths", action="store_true", help="在本人私有包中显示同一归档行已随包原稿的本机绝对路径")
    args = parser.parse_args()
    result = pack(args.board, args.out_dir, args.asset_root, archive_paths=args.archive_paths)
    print(json.dumps({k: result[k] for k in ("version", "site", "bytes", "local_references_marked")}, ensure_ascii=False))
