#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context_import.py —— 把 context_scan.py 盘点出来的已有 AI 记忆/配置，勾选式导入到所选 profile home（PLAN-1000 S3）。

默认只预览；`--apply` 才写。每次写都留 receipt（新建了哪些文件、改了哪些文件 + 改前备份），
`--rollback --receipt <file>` 一键撤回。

导入矩阵（目标 = Claude profile home，如 ~/.claude-work；Codex profile 只导 AGENTS.md 与 memories）：
  来源                         → 目标
  ~/.claude/CLAUDE.md          → <home>/CLAUDE.md 末尾追加带 imported-from 标记的独立段（幂等：同源同 hash 跳过，变了就替换）
  ~/.claude/memory/*.md        → <home>/memory/<同名>.md + MEMORY.md 索引补行
  ~/.claude/projects/<slug>/memory/*  → <home>/projects/<slug>/memory/*（同机同路径 → slug 相同，Claude Code 开工自动读）
  ~/.claude/skills/<name>/     → <home>/skills/<name>/（已存在或叫 feishu 的跳过）
  ~/.claude/commands/*.md      → <home>/commands/
  ~/.claude/settings.json      → 只合并白名单偏好键（theme/model/effortLevel/autoUpdatesChannel/tui/permissions），目标已有的键不动
  Claude 桌面版 Cowork memory/CLAUDE.md          → <home>/CLAUDE.md 追加标记段
  Claude 桌面版 Cowork memory/memory/*.md、space memory → <home>/memory/desktop-<name>.md + 索引
  ~/.codex/AGENTS.md           → <home>/CLAUDE.md 追加标记段（Codex 目标则追加到 <home>/AGENTS.md）
  ~/.codex/memories/*.md       → <home>/memory/codex-<name>.md + 索引
  Claude 导出包 memories.json / projects.json    → <home>/memory/export-claude-memories.md、export-claude-projects.md
  Claude / ChatGPT 导出包 conversations.json     → 不整包搬：抽近 90 天纯文本 → 交给一个 `claude -p` 会话蒸馏成 <home>/memory/imported-taste.md
  chatgpt-memories.txt         → <home>/memory/export-chatgpt-memories.md
  永不导入：.claude.json、.credentials.json、auth.json、.env、token、sessions/、projects/*/*.jsonl

用法：
    python feishu/context_import.py --profile claude-work                # 现场盘点 + 预览
    python feishu/context_import.py --profile claude-work --scan r.json  # 用已有报告
    python feishu/context_import.py --profile claude-work --apply [--only claude-code-home,codex-home] [--skip export:x.zip] [--no-distill]
    python feishu/context_import.py --rollback --receipt feishu/_state/context-import-20260910-091500.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import context_scan  # noqa: E402

STATE_DIR = HERE / "_state"
MARK_BEGIN = "<!-- imported-from: {src} · sha256:{digest} · link16 context_import -->"
MARK_END = "<!-- /imported-from: {src} -->"
SETTINGS_WHITELIST = ("theme", "model", "effortLevel", "autoUpdatesChannel", "tui", "permissions")
SECRET_NAMES = context_scan.SECRET_NAMES
DISTILL_DAYS = 90
DISTILL_MAX_CHARS = 260_000
DISTILL_TIMEOUT_SEC = 900


# ---------------------------------------------------------------- 小工具
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _now_tag() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _is_secret(path: Path) -> bool:
    name = path.name
    return name in SECRET_NAMES or name.lower().endswith((".pem", ".key")) or name == ".claude.json" or path.suffix == ".jsonl"


def _first_line(text: str, limit=120) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith("---"):
            return line[:limit]
    return ""


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9一-鿿]+", "-", text).strip("-").lower()
    return s[:60] or "note"


class Plan:
    """把每个动作先记下来（预览），apply 时逐条执行并写 receipt。"""

    def __init__(self, target_home: Path, runtime: str, profile: str):
        self.home = target_home
        self.runtime = runtime
        self.profile = profile
        self.actions: list[dict] = []
        self.skipped: list[dict] = []
        self.receipt = {"schema": "link16-context-import-receipt-v1", "profile": profile, "home": str(target_home),
                        "at": _now_tag(), "created": [], "modified": [], "backup_dir": ""}

    # ---- 记账
    def add(self, kind, src, dst, detail="", source_id=""):
        self.actions.append({"kind": kind, "src": str(src) if src else "", "dst": str(dst), "detail": detail, "source": source_id})

    def skip(self, why, src="", source_id=""):
        self.skipped.append({"why": why, "src": str(src), "source": source_id})

    # ---- 写入原语（全部经过备份）
    def _record(self, dst: Path, existed: bool):
        key = str(dst)
        if key in self.receipt["created"]:
            return  # 本次导入新建的文件再被改 → 仍算「新建」，回滚时整个删掉
        bucket = self.receipt["modified"] if existed else self.receipt["created"]
        if key not in bucket:
            bucket.append(key)

    def _backup(self, dst: Path):
        if not dst.exists() or str(dst) in self.receipt["created"]:
            return
        root = Path(self.receipt["backup_dir"])
        rel = dst.relative_to(self.home) if str(dst).startswith(str(self.home)) else Path(dst.name)
        target = root / rel
        if target.exists():
            return  # 同一次导入里多次改同一文件：只保留【第一次改之前】的原件，回滚才回得到起点
        target.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir():
            shutil.copytree(dst, target, dirs_exist_ok=True)
        else:
            shutil.copy2(dst, target)

    def write_text(self, dst: Path, text: str):
        existed = dst.exists()
        self._backup(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        self._record(dst, existed)

    def copy_file(self, src: Path, dst: Path):
        if _is_secret(src):
            return
        existed = dst.exists()
        self._backup(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self._record(dst, existed)

    def copy_tree(self, src: Path, dst: Path):
        existed = dst.exists()
        self._backup(dst)
        for dp, dn, fn in os.walk(src):
            dn[:] = [d for d in dn if d not in context_scan.SKIP_DIRS]
            for name in fn:
                s = Path(dp) / name
                if _is_secret(s):
                    continue
                d = dst / s.relative_to(src)
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
        self._record(dst, existed)


# ---------------------------------------------------------------- 各类导入动作
def _instructions_target(plan: Plan) -> Path:
    return plan.home / ("AGENTS.md" if plan.runtime == "codex" else "CLAUDE.md")


def import_instruction_block(plan: Plan, src: Path, source_id: str, apply: bool, label: str):
    """把一份 CLAUDE.md / AGENTS.md 作为带标记的独立段追加到目标指令文件；同源同 hash 跳过，变了就替换。"""
    try:
        if src.resolve() == _instructions_target(plan).resolve():
            plan.skip("来源就是目标自己", src, source_id)
            return
    except OSError:
        pass
    try:
        text = src.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        plan.skip(f"读不了：{exc}", src, source_id)
        return
    if not text:
        plan.skip("空文件", src, source_id)
        return
    digest = _sha(text.encode("utf-8"))
    dst = _instructions_target(plan)
    existing = dst.read_text(encoding="utf-8", errors="replace") if dst.is_file() else ""
    begin = MARK_BEGIN.format(src=src, digest=digest)
    end = MARK_END.format(src=src)
    if begin in existing:
        plan.skip("已导入过且内容没变", src, source_id)
        return
    # 目标本来就包含同样的正文（例如 ~/.claude/CLAUDE.md 是治理渲染出来的副本）→ 不重复塞一份
    norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    if norm(text) and norm(text) in norm(existing):
        plan.skip("目标已包含相同正文", src, source_id)
        return
    block = f"\n\n{begin}\n## 导入的{label}（来源：{src}）\n\n{text}\n\n{end}\n"
    pattern = re.compile(re.escape(MARK_BEGIN.format(src=src, digest="")).replace("sha256:", "sha256:[0-9a-f]*")
                         + r".*?" + re.escape(end) + r"\n?", re.S)
    replaced = bool(pattern.search(existing))
    plan.add("instructions", src, dst, f"{'替换' if replaced else '追加'}标记段（{len(text)} 字）", source_id)
    if apply:
        new_text = pattern.sub("", existing).rstrip() + block if replaced else existing.rstrip() + block
        plan.write_text(dst, new_text.lstrip("\n") if not existing else new_text)


def _fact_text(name: str, description: str, body: str, source: str, kind: str) -> str:
    return (f"---\nname: {name}\ndescription: {description}\nmetadata:\n  node_type: memory\n  type: {kind}\n"
            f"  imported_from: {source}\n  imported_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n---\n\n{body.strip()}\n")


def import_memory_notes(plan: Plan, files: list[Path], prefix: str, source_id: str, apply: bool, kind="project"):
    """一批 markdown 笔记 → <home>/memory/<prefix><name>.md（补 frontmatter）+ MEMORY.md 索引补行。"""
    mem_dir = plan.home / "memory"
    index = mem_dir / "MEMORY.md"
    index_text = index.read_text(encoding="utf-8", errors="replace") if index.is_file() else ""
    new_lines = []
    for src in files:
        if src.name == "MEMORY.md" or _is_secret(src) or src.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            body = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not body.strip():
            continue
        stem = _slug(src.stem)
        name = f"{prefix}{stem}"
        dst = mem_dir / f"{name}.md"
        if dst.exists():
            try:
                if _sha(dst.read_bytes()) == _sha(body.encode("utf-8")) or f"imported_from: {src}" in dst.read_text(encoding="utf-8", errors="replace"):
                    plan.skip("已导入过", src, source_id)
                    continue
            except OSError:
                pass
        has_frontmatter = body.lstrip().startswith("---")
        desc = _first_line(body)
        text = body if has_frontmatter else _fact_text(name, desc, body, str(src), kind)
        plan.add("memory", src, dst, f"记忆笔记（{len(body)} 字）", source_id)
        if f"({name}.md)" not in index_text:
            new_lines.append(f"- [{desc or name}]({name}.md) — 导入自 {src.parent.name}/{src.name}")
        if apply:
            plan.write_text(dst, text)
    if new_lines:
        plan.add("memory-index", "", index, f"索引补 {len(new_lines)} 行", source_id)
        if apply:
            plan.write_text(index, (index_text.rstrip() + "\n" if index_text.strip() else "") + "\n".join(new_lines) + "\n")


def import_project_memory(plan: Plan, src_home: Path, source_id: str, apply: bool):
    """~/.claude/projects/<slug>/memory/* → <home>/projects/<slug>/memory/*（同机同路径，slug 相同）。"""
    for proj in (src_home / "projects").glob("*"):
        mem = proj / "memory"
        if not mem.is_dir():
            continue
        files = [p for p in mem.rglob("*") if p.is_file() and not _is_secret(p)]
        if not files:
            continue
        dst_dir = plan.home / "projects" / proj.name / "memory"
        try:
            if dst_dir.resolve() == mem.resolve():
                plan.skip("来源就是目标自己", mem, source_id)
                continue
        except OSError:
            pass
        todo = []
        for f in files:
            d = dst_dir / f.relative_to(mem)
            if d.exists():
                try:
                    if _sha(d.read_bytes()) == _sha(f.read_bytes()):
                        continue
                except OSError:
                    pass
                if f.name == "MEMORY.md":
                    # 索引冲突：合并行，不覆盖
                    todo.append((f, d, "merge-index"))
                    continue
                continue  # 目标已有不同内容的同名文件 → 保留目标，不覆盖
            todo.append((f, d, "copy"))
        if not todo:
            plan.skip("项目记忆已一致", mem, source_id)
            continue
        plan.add("project-memory", mem, dst_dir, f"{len(todo)} 个文件（项目 {proj.name}）", source_id)
        if apply:
            for f, d, mode in todo:
                if mode == "merge-index":
                    old = d.read_text(encoding="utf-8", errors="replace").splitlines()
                    add = [l for l in f.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip() and l not in old]
                    if add:
                        plan.write_text(d, "\n".join(old + add) + "\n")
                else:
                    plan.copy_file(f, d)


def import_skills_and_commands(plan: Plan, src_home: Path, source_id: str, apply: bool):
    for skill in sorted((src_home / "skills").glob("*/SKILL.md")):
        sdir = skill.parent
        if sdir.name == "feishu":
            plan.skip("feishu skill 由 profile_bootstrap 管理", sdir, source_id)
            continue
        dst = plan.home / "skills" / sdir.name
        try:
            if dst.resolve() == sdir.resolve():
                continue
        except OSError:
            pass
        if dst.exists():
            plan.skip("目标已有同名 skill", sdir, source_id)
            continue
        plan.add("skill", sdir, dst, "整目录复制", source_id)
        if apply:
            plan.copy_tree(sdir, dst)
    for cmd in sorted((src_home / "commands").glob("*.md")):
        dst = plan.home / "commands" / cmd.name
        try:
            if dst.resolve() == cmd.resolve():
                continue
        except OSError:
            pass
        if dst.exists():
            continue
        plan.add("command", cmd, dst, "复制", source_id)
        if apply:
            plan.copy_file(cmd, dst)


def import_settings(plan: Plan, src: Path, source_id: str, apply: bool):
    if plan.runtime != "claude" or not src.is_file():
        return
    dst = plan.home / "settings.json"
    try:
        if dst.resolve() == src.resolve():
            return
    except OSError:
        pass
    try:
        source = json.loads(src.read_text(encoding="utf-8"))
        target = json.loads(dst.read_text(encoding="utf-8")) if dst.is_file() else {}
    except (OSError, ValueError) as exc:
        plan.skip(f"settings.json 解析失败：{exc}", src, source_id)
        return
    if not isinstance(source, dict) or not isinstance(target, dict):
        return
    added = {k: source[k] for k in SETTINGS_WHITELIST if k in source and k not in target}
    if not added:
        plan.skip("settings 白名单键目标都已有", src, source_id)
        return
    plan.add("settings", src, dst, "合并偏好键：" + ", ".join(added), source_id)
    if apply:
        target.update(added)
        plan.write_text(dst, json.dumps(target, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------- 导出包
def _read_zip_member(zip_path: Path, member: str, limit_bytes=800 * 1024 * 1024):
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if Path(info.filename).name == member:
                if info.file_size > limit_bytes:
                    raise ValueError(f"{member} 太大（{info.file_size // 1048576} MB）")
                return zf.read(info).decode("utf-8", errors="replace")
    return None


def _json_to_markdown(obj, depth=0) -> str:
    """把 memories.json / projects.json 这类结构不固定的导出，转成人能读的 markdown（不猜 schema）。"""
    out = []
    if isinstance(obj, dict):
        title = obj.get("name") or obj.get("title") or obj.get("uuid") or ""
        if title and depth <= 2:
            out.append(f"{'#' * min(depth + 2, 5)} {title}")
        for k, v in obj.items():
            if k in {"name", "title"}:
                continue
            if isinstance(v, (dict, list)):
                sub = _json_to_markdown(v, depth + 1)
                if sub.strip():
                    out.append(f"- **{k}**:\n{sub}")
            elif v not in (None, "", [], {}):
                sval = str(v)
                out.append(f"- **{k}**: {sval[:2000]}")
    elif isinstance(obj, list):
        for item in obj[:500]:
            out.append(_json_to_markdown(item, depth + 1) if isinstance(item, (dict, list)) else f"- {str(item)[:2000]}")
    else:
        out.append(str(obj)[:2000])
    return "\n".join(out)


def import_claude_export(plan: Plan, zip_path: Path, source_id: str, apply: bool, distill: bool):
    for member, name, desc in (("memories.json", "export-claude-memories", "Claude.ai 导出的记忆"),
                               ("projects.json", "export-claude-projects", "Claude.ai 导出的 Projects（名称与系统指令）")):
        try:
            raw = _read_zip_member(zip_path, member)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            plan.skip(f"{member}：{exc}", zip_path, source_id)
            continue
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            plan.skip(f"{member} 不是合法 JSON", zip_path, source_id)
            continue
        if member == "projects.json" and isinstance(data, list):
            data = [{k: v for k, v in (p or {}).items() if k in {"name", "description", "prompt_template", "is_private", "created_at"}}
                    for p in data]
        body = _json_to_markdown(data)
        if not body.strip():
            continue
        dst = plan.home / "memory" / f"{name}.md"
        plan.add("export", f"{zip_path}!{member}", dst, f"{desc}（{len(body)} 字）", source_id)
        if apply:
            plan.write_text(dst, _fact_text(name, f"{desc}，导入自 {zip_path.name}", body, f"{zip_path}!{member}", "user"))
            _index_add(plan, name, desc, zip_path.name)
    if distill:
        import_conversations_distilled(plan, zip_path, "claude", source_id, apply)


def import_chatgpt_export(plan: Plan, zip_path: Path, source_id: str, apply: bool, distill: bool):
    if distill:
        import_conversations_distilled(plan, zip_path, "chatgpt", source_id, apply)


def import_chatgpt_memories_txt(plan: Plan, src: Path, source_id: str, apply: bool):
    try:
        body = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        plan.skip(str(exc), src, source_id)
        return
    dst = plan.home / "memory" / "export-chatgpt-memories.md"
    plan.add("export", src, dst, f"ChatGPT 记忆文本（{len(body)} 字）", source_id)
    if apply:
        plan.write_text(dst, _fact_text("export-chatgpt-memories", "ChatGPT「管理记忆」页复制的记忆", body, str(src), "user"))
        _index_add(plan, "export-chatgpt-memories", "ChatGPT 记忆", src.name)


def _index_add(plan: Plan, name: str, desc: str, origin: str):
    index = plan.home / "memory" / "MEMORY.md"
    text = index.read_text(encoding="utf-8", errors="replace") if index.is_file() else ""
    if f"({name}.md)" in text:
        return
    plan.write_text(index, (text.rstrip() + "\n" if text.strip() else "") + f"- [{desc}]({name}.md) — 导入自 {origin}\n")


# ---------------------------------------------------------------- 会话蒸馏
def _claude_export_texts(data, cutoff_ts: float):
    for conv in data if isinstance(data, list) else []:
        created = str(conv.get("created_at") or conv.get("updated_at") or "")
        ts = _parse_ts(created)
        if ts and ts < cutoff_ts:
            continue
        lines = [f"### {conv.get('name') or '(无题)'} · {created[:10]}"]
        for m in conv.get("chat_messages") or []:
            role = m.get("sender") or m.get("role") or "?"
            text = m.get("text") or ""
            if not text and isinstance(m.get("content"), list):
                text = "\n".join(c.get("text", "") for c in m["content"] if isinstance(c, dict) and c.get("type") == "text")
            text = text.strip()
            if text:
                lines.append(f"{role}: {text[:1500]}")
        if len(lines) > 1:
            yield ts or 0.0, "\n".join(lines)


def _chatgpt_export_texts(data, cutoff_ts: float):
    for conv in data if isinstance(data, list) else []:
        ts = float(conv.get("create_time") or conv.get("update_time") or 0)
        if ts and ts < cutoff_ts:
            continue
        lines = [f"### {conv.get('title') or '(无题)'} · {time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else ''}"]
        for node in (conv.get("mapping") or {}).values():
            msg = (node or {}).get("message") or {}
            role = ((msg.get("author") or {}).get("role")) or "?"
            parts = ((msg.get("content") or {}).get("parts")) or []
            text = "\n".join(p for p in parts if isinstance(p, str)).strip()
            if text and role in {"user", "assistant"}:
                lines.append(f"{role}: {text[:1500]}")
        if len(lines) > 1:
            yield ts, "\n".join(lines)


def _parse_ts(value: str) -> float:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        return 0.0
    try:
        return time.mktime(time.strptime(m.group(0), "%Y-%m-%d"))
    except (ValueError, OverflowError):
        return 0.0


DISTILL_PROMPT = """你在为一位刚开始用 Link16 飞书智能体的用户建立初始记忆。下面这个文件是他过去 {days} 天与 {vendor} 的聊天记录纯文本（已去掉工具流水）：
{path}

请只读这个文件，然后输出一份 Markdown（中文，不超过 1500 字，不要寒暄），严格按这个结构：
1. **他是谁**：角色、领域、常用技术栈与工具（只写记录里有证据的）
2. **正在做的项目与目标**：按项目列，每项一句现状 + 一句目标
3. **工作与沟通偏好**：他反复要求或纠正过的做法（例如汇报形态、语言、格式、不要做什么）
4. **常见任务类型**：他最常让 AI 做什么
5. **待办与悬而未决**：记录里提到但没闭环的事
每条后面用括号标一下依据（哪次对话/日期）。不要编造，没证据就写「记录里没有」。"""


def import_conversations_distilled(plan: Plan, zip_path: Path, vendor: str, source_id: str, apply: bool):
    """conversations.json 不整包搬：抽近 90 天纯文本 → `claude -p` 蒸馏成 imported-taste 记忆；失败就保留原料文件。"""
    dst = plan.home / "memory" / f"imported-taste-{vendor}.md"
    raw_dst = STATE_DIR / "context-import" / f"distill-input-{vendor}-{_now_tag()}.md"
    plan.add("distill", f"{zip_path}!conversations.json", dst,
             f"近 {DISTILL_DAYS} 天对话 → claude -p 蒸馏（原料落 {raw_dst.parent}）", source_id)
    if not apply:
        return
    try:
        raw = _read_zip_member(zip_path, "conversations.json")
        data = json.loads(raw) if raw else []
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        plan.skip(f"conversations.json：{exc}", zip_path, source_id)
        return
    cutoff = time.time() - DISTILL_DAYS * 86400
    chunks = sorted((_claude_export_texts if vendor == "claude" else _chatgpt_export_texts)(data, cutoff), key=lambda c: -c[0])
    body, total = [], 0
    for _, text in chunks:
        if total + len(text) > DISTILL_MAX_CHARS:
            break
        body.append(text)
        total += len(text)
    if not body:
        plan.skip(f"近 {DISTILL_DAYS} 天没有对话", zip_path, source_id)
        return
    raw_dst.parent.mkdir(parents=True, exist_ok=True)
    raw_dst.write_text("\n\n".join(body), encoding="utf-8")
    result = run_distiller(plan.home, raw_dst, vendor)
    if result:
        plan.write_text(dst, _fact_text(f"imported-taste-{vendor}", f"从 {vendor} 近 {DISTILL_DAYS} 天对话蒸馏的用户画像与偏好",
                                        result, f"{zip_path}!conversations.json", "user"))
        _index_add(plan, f"imported-taste-{vendor}", f"{vendor} 对话蒸馏的画像与偏好", zip_path.name)
    else:
        plan.skip(f"蒸馏未成功；原料保留在 {raw_dst}，可稍后用 claude 手动读它生成", zip_path, source_id)


def run_distiller(home: Path, input_path: Path, vendor: str) -> str:
    """用目标 profile 自己的 Claude Code 跑一次非交互蒸馏；CLI 不在或失败返回空串。"""
    exe = shutil.which("claude")
    if not exe:
        return ""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(home)
    for key in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDECODE"):
        env.pop(key, None)
    prompt = DISTILL_PROMPT.format(days=DISTILL_DAYS, vendor=vendor, path=input_path)
    try:
        done = subprocess.run([exe, "-p", prompt, "--output-format", "text", "--allowedTools", "Read"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=DISTILL_TIMEOUT_SEC, env=env, cwd=str(input_path.parent),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (done.stdout or "").strip()
    return text if done.returncode == 0 and len(text) > 80 else ""


# ---------------------------------------------------------------- 总装
def build_plan(report: dict, profile: str, only=None, skip=None, apply=False, distill=True) -> Plan:
    import agent_runtime
    spec = agent_runtime.profile_spec(profile)
    home = spec.home_path
    if apply and not home.is_dir():
        raise SystemExit(f"目标 profile {profile} 的 home 不存在：{home}（先 profile_bootstrap --apply）")
    plan = Plan(home, spec.runtime, profile)
    if apply:
        backup = STATE_DIR / "context-import" / f"backup-{plan.receipt['at']}"
        backup.mkdir(parents=True, exist_ok=True)
        plan.receipt["backup_dir"] = str(backup)
    only = set(only or [])
    skip = set(skip or [])
    for src in report.get("sources") or []:
        sid = src["id"]
        if not src.get("found"):
            continue
        selected = (sid in only) if only else src.get("import_default", False)
        if sid in skip:
            selected = False
        if not selected:
            plan.skip("未勾选", src.get("path", ""), sid)
            continue
        kind = src["kind"]
        path = Path(src["path"]) if src.get("path") else None
        if kind == "claude-code":
            import_instruction_block(plan, path / "CLAUDE.md", sid, apply, "全局指令")
            import_memory_notes(plan, sorted((path / "memory").glob("*.md")), "", sid, apply)
            import_project_memory(plan, path, sid, apply)
            import_skills_and_commands(plan, path, sid, apply)
            import_settings(plan, path / "settings.json", sid, apply)
        elif kind == "claude-desktop":
            for mem_dir in src.get("memory_dirs") or []:
                md = Path(mem_dir)
                if (md / "CLAUDE.md").is_file():
                    import_instruction_block(plan, md / "CLAUDE.md", sid, apply, "Claude 桌面版全局指令")
                notes = sorted((md / "memory").glob("*.md")) if (md / "memory").is_dir() else sorted(md.glob("*.md"))
                in_space = md.parent.parent.name == "spaces"   # <user>/spaces/<projectId>/memory
                prefix = f"desktop-{md.parent.name[:12]}-" if in_space else "desktop-"
                import_memory_notes(plan, [n for n in notes if n.name != "CLAUDE.md"], prefix, sid, apply)
        elif kind == "codex":
            if (path / "AGENTS.md").is_file():
                import_instruction_block(plan, path / "AGENTS.md", sid, apply, "Codex 指令")
            import_memory_notes(plan, sorted((path / "memories").rglob("*.md")), "codex-", sid, apply)
        elif kind == "export-claude":
            import_claude_export(plan, path, sid, apply, distill)
        elif kind == "export-chatgpt":
            import_chatgpt_export(plan, path, sid, apply, distill)
        elif kind == "export-chatgpt-memories":
            import_chatgpt_memories_txt(plan, path, sid, apply)
        else:
            plan.skip("这类来源没有可导入的本地内容", src.get("path", ""), sid)
    # 入口提示：让目标 profile 一开工就知道去读导入的记忆
    pointer = plan.home / "memory" / "MEMORY.md"
    if apply and pointer.is_file():
        head = _instructions_target(plan)
        hint = f"\n<!-- link16 context_import pointer -->\n> 已导入你此前的 AI 记忆：开工先读 `{pointer}` 索引与其中的笔记。\n"
        existing = head.read_text(encoding="utf-8", errors="replace") if head.is_file() else ""
        if "link16 context_import pointer" not in existing:
            plan.write_text(head, existing.rstrip() + "\n" + hint)
    if apply:
        rpath = STATE_DIR / f"context-import-{plan.receipt['at']}.json"
        rpath.parent.mkdir(parents=True, exist_ok=True)
        rpath.write_text(json.dumps(plan.receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plan.receipt["receipt_path"] = str(rpath)
    return plan


def rollback(receipt_path: Path) -> list[str]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    home = Path(receipt["home"])
    backup = Path(receipt.get("backup_dir") or "")
    done = []
    for created in receipt.get("created", []):
        p = Path(created)
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            done.append(f"删除 {p}")
    created = set(receipt.get("created", []))
    for modified in receipt.get("modified", []):
        if modified in created:
            continue
        p = Path(modified)
        rel = p.relative_to(home) if str(p).startswith(str(home)) else Path(p.name)
        src = backup / rel
        if src.exists():
            if src.is_dir():
                if p.exists():
                    shutil.rmtree(p)
                shutil.copytree(src, p)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, p)
            done.append(f"恢复 {p}")
    return done


def render(plan: Plan, apply: bool) -> str:
    lines = [f"===== Link16 · 导入 {'【已执行】' if apply else '【预览 · 零写入】'} → profile {plan.profile}（{plan.runtime} · {plan.home}）=====", ""]
    if not plan.actions:
        lines.append("  没有可导入的动作（来源未勾选、或都已导入过）")
    for a in plan.actions:
        lines.append(f"  [{a['kind']:>15}] {a['detail']}")
        if a["src"]:
            lines.append(f"                    {a['src']}")
        lines.append(f"                 →  {a['dst']}")
    if plan.skipped:
        lines.append("")
        lines.append("  跳过：")
        for s in plan.skipped[:40]:
            lines.append(f"    · {s['why']}  {s['src']}")
        if len(plan.skipped) > 40:
            lines.append(f"    · …还有 {len(plan.skipped) - 40} 条")
    lines.append("")
    if apply:
        lines.append(f"  新建 {len(plan.receipt['created'])} 个 · 修改 {len(plan.receipt['modified'])} 个（改前备份在 {plan.receipt['backup_dir']}）")
        lines.append(f"  回滚：python feishu/context_import.py --rollback --receipt {plan.receipt.get('receipt_path')}")
    else:
        lines.append("  确认无误后加 --apply 执行；--only / --skip 按来源 id 勾选；--no-distill 跳过对话蒸馏")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="勾选式导入已有 AI 记忆/配置到所选 profile（PLAN-1000）")
    ap.add_argument("--profile", help="目标 profile 名（agent-profiles.local.json 里的）")
    ap.add_argument("--scan", help="context_scan.py --out 写的报告；不给就现场盘点")
    ap.add_argument("--only", default="", help="只导这些来源 id，逗号分隔")
    ap.add_argument("--skip", default="", help="跳过这些来源 id，逗号分隔")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-distill", action="store_true", help="不做对话蒸馏（默认做）")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--receipt", help="--rollback 用的 receipt 路径")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.rollback:
        if not args.receipt:
            ap.error("--rollback 需要 --receipt")
        for line in rollback(Path(args.receipt)):
            print(line)
        return 0
    if not args.profile:
        targets = context_scan.target_profiles(None)
        args.profile = targets.get("claude") or targets.get("codex")
        if not args.profile:
            ap.error("给 --profile，或先建 profile registry")
    if args.scan:
        report = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    else:
        report = context_scan.scan(profile=args.profile)
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    skip = [s.strip() for s in args.skip.split(",") if s.strip()]
    plan = build_plan(report, args.profile, only, skip, apply=args.apply, distill=not args.no_distill)
    if args.json:
        print(json.dumps({"actions": plan.actions, "skipped": plan.skipped, "receipt": plan.receipt}, ensure_ascii=False, indent=2))
    else:
        print(render(plan, args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
