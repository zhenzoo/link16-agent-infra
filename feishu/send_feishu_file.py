#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_feishu_file.py — 把本地【任意文件】作为真·文件附件发到某 bot 的飞书会话(DM 或群)。

和 `feishu_bridge.py send` 的另两种「发东西」区分清楚：
  · `send --image <图>`  → 图片消息（飞书里内联显示）
  · `send --doc <md/html>` → 转飞书在线云文档、发一条【链接】（在线看/可编辑·ARCH-101 §2.11）
  · **本工具 → 发文件本体**（对方在飞书里能直接【下载打开】的原始文件·任意类型）

为什么单独成工具：用 SDK 一等公民 `OutboundFile`（不手搓 token/multipart）。**飞书文件消息不支持 caption**
（带 caption 直接 `format_error`），所以 `--text` 作为【单独一条消息】在文件之后发。

目标优先级：`--to`（chat_id/open_id）> 该 bot 会话 chat_id（`_autopilot/bridge-session-<bot>.json`）。
凭据走 `bridge_env`（跨机解析·不写死盘符/用户名），与桥本体同一套 SSOT。

用法：
  python orchestrator/send_feishu_file.py --bot explore --file docs/ARCH-101-feishu-bridge.md
  python orchestrator/send_feishu_file.py --bot explore --to oc_xxx --file path/to/x.pdf --text "这是说明" --json
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ORCH = Path(__file__).resolve().parent
PROJECT = ORCH.parent
sys.path.insert(0, str(ORCH))
from bridge_env import resolve_env_path, bots_config_path, assert_sender_identity  # noqa: E402

# 飞书国内端点直连·绕代理（同 feishu_bridge）
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

ENV_PATH = resolve_env_path()


def _env(*keys):
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if m and m.group(1) in keys:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def _bot_creds(bot):
    """从 bot 名册(本地 overlay 优先) + .env 取 (app_id, app_secret)。找不到 raise。"""
    cfg = bots_config_path(PROJECT)
    specs = json.loads(cfg.read_text(encoding="utf-8")).get("bots", []) if cfg.exists() else []
    spec = next((s for s in specs if s.get("name") == bot), None)
    if not spec:
        raise SystemExit(f"❌ bot 名册里没有 '{bot}'（{cfg}）")
    ide, sce = spec.get("app_id_env"), spec.get("app_secret_env")
    e = _env(ide, sce)
    aid, asec = e.get(ide), e.get(sce)
    if not aid or not asec:
        raise SystemExit(f"❌ bot '{bot}' 缺凭据：.env 没有 {ide}/{sce}")
    return aid, asec


def _session_chat(bot):
    f = PROJECT / "feishu" / "_state" / f"bridge-session-{bot}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("chat_id")
        except (OSError, json.JSONDecodeError):
            return None
    return None


async def _go(app_id, app_secret, target, file_path, text):
    from lark_channel import FeishuChannel, OutboundFile, MediaSource  # noqa: E402
    ch = FeishuChannel(app_id=app_id, app_secret=app_secret)
    p = Path(file_path)
    res = await ch.send(target, OutboundFile(
        source=MediaSource(kind="file", path=str(p.resolve())), file_name=p.name))
    file_ok = bool(getattr(res, "success", False))
    file_err = None
    if not file_ok:
        err = getattr(res, "error", None)
        file_err = f"code={getattr(err, 'code', None)} {getattr(err, 'hint', None)}" if err else "unknown"
    text_ok = None
    if text:                                  # caption 不支持 → 文件后另发一条文字消息
        try:
            r2 = await ch.send(target, {"text": text})
            text_ok = bool(getattr(r2, "success", False))
        except Exception:  # noqa: BLE001
            text_ok = False
    return file_ok, file_err, text_ok


def main():
    ap = argparse.ArgumentParser(description="把本地文件作为真·文件附件发到飞书 DM/群（OutboundFile）")
    ap.add_argument("--bot", required=True, help="哪个 bot（用它的飞书应用凭据发）")
    ap.add_argument("--file", required=True, help="要发的本地文件路径（任意类型）")
    ap.add_argument("--to", default=None, help="目标 chat_id(oc_)/open_id(ou_)；不给=该 bot 会话的 chat_id")
    ap.add_argument("--text", default=None, help="附带说明（飞书文件消息不支持 caption → 作为单独一条消息发）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    a = ap.parse_args()
    assert_sender_identity(a.bot)   # 身份闸：桥会话不得冒用别的 bot 发（PLAN-920）
    p = Path(a.file)
    if not p.is_file():
        raise SystemExit(f"❌ 文件不存在: {a.file}")
    target = a.to or _session_chat(a.bot)
    if not target:
        raise SystemExit(f"❌ 没有可发目标（--to 没给，且 bridge-session-{a.bot}.json 无 chat_id·先在飞书 @ 它一次）")
    app_id, app_secret = _bot_creds(a.bot)
    file_ok, file_err, text_ok = asyncio.run(_go(app_id, app_secret, target, a.file, a.text))
    out = {"file_ok": file_ok, "file_err": file_err, "text_ok": text_ok,
           "bot": a.bot, "to": target, "file": p.name, "size_bytes": p.stat().st_size}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"{'✅' if file_ok else '❌'} 文件{' + 文字' if a.text else ''} → {target}"
              f"（{p.name} · {p.stat().st_size} 字节）" + (f" · 错误={file_err}" if file_err else ""))
    sys.exit(0 if file_ok else 1)


if __name__ == "__main__":
    main()
