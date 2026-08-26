#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py · 写帖 pipeline 通知器 (飞书自定义机器人 webhook)

设计目标: 最高可靠性的"必达通知"。
  - 纯标准库 (urllib)，无第三方依赖 → 不会因为缺包/装包失败而发不出
  - 强制绕过系统代理 (ProxyHandler({}))  → open.feishu.cn 是国内端点，直连最稳，
    代理(Clash)断了也照样发得出。这是相比 Telegram 的核心可靠性优势。
  - 3 次重试 + 退避 → 抗网络瞬断
  - 关键词: PNN(P120…)天然含飞书关键词 "P";其余文案自带 Post/写帖子;兜底加最短载体
  - 不依赖任何常驻 daemon / MCP server / hook → 谁调它它就发

用法:
  python scripts/notify.py --kind test                # 测试推送
  python scripts/notify.py --kind ready   --post P114 # 帖子 ready
  python scripts/notify.py --kind escape  --post P114 # 需人工 (needs-human)
  python scripts/notify.py --kind brief   --post P114 # brief 待审
  python scripts/notify.py "任意自定义文本"            # 自由文本

退出码: 0 = 送达 (飞书返回 code==0)；非 0 = 全部重试后仍失败。
webhook 地址来源: 环境变量 FEISHU_XHS_WEBHOOK_URL，或向上查找 .env 里同名键。
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ENV_KEY = "FEISHU_XHS_WEBHOOK_URL"
# 飞书机器人「自定义关键词」(已设 P · 可能保留 Post/写帖子)· PNN 消息靠开头的 "P" 命中
KEYWORDS = ("P", "Post", "写帖子")
TIMEOUT = 10             # 单次请求超时(秒)
RETRY_BACKOFF = [0, 2, 5]  # 3 次尝试的等待秒数


def find_webhook_url():
    """优先读环境变量；否则按 bridge_env 的跨机契约解析 .env。"""
    val = os.environ.get(ENV_KEY)
    if val and val.strip() and "PASTE_" not in val:
        return val.strip()

    try:
        from bridge_env import resolve_env_path
        env_file = resolve_env_path(start=Path(__file__))
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == ENV_KEY:
                    value = value.strip()
                    if value and "PASTE_" not in value:
                        return value
    except Exception:
        pass
    return None


def _ensure_keyword(text):
    """飞书自定义关键词安全设置要求消息含关键词。
    PNN 开头消息天然含 "P";其余文案尽量自带 Post/写帖子;都没有时兜底加最短载体 "Post · "。"""
    if any(k in text for k in KEYWORDS):
        return text
    return "Post · " + text


def build_text(kind, post, message):
    """组装消息文本 · PNN 恒在开头 · 不加前缀(关键词靠 PNN 的 'P' 或文案自带 Post/写帖子)。"""
    post = (post or "").strip()
    if kind == "test":
        return _ensure_keyword("Post 通知测试 · 飞书通道已打通 ✅")
    if kind == "ready":
        return _ensure_keyword(f"{post} 已 ready · 待你签字 ✅")
    if kind == "escape":
        return _ensure_keyword(f"{post} 卡住了 · 需人工介入 ⚠️")
    if kind == "published":
        return _ensure_keyword(f"{post} 已发布入库 · published 归档完成 ✅")
    if kind == "brief":
        return _ensure_keyword(f"{post} brief 已就绪 · 待你审批 📝")
    # 自由文本 / info(caller 传完整文案)
    body = message or (f"{post} 状态更新" if post else "状态更新")
    return _ensure_keyword(body)


def send_feishu(url, text):
    """POST 文本消息到飞书 webhook。强制绕过代理 + 重试。返回 (ok, detail)。"""
    payload = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
    # ProxyHandler({}) → 显式禁用所有代理，直连 open.feishu.cn
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    last_detail = "unknown error"
    for attempt, wait in enumerate(RETRY_BACKOFF, start=1):
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
            # 飞书成功: code==0 (新) 或 StatusCode==0 (旧)
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                return True, raw
            last_detail = f"飞书返回非成功 (尝试 {attempt}/3): {raw}"
        except urllib.error.HTTPError as e:
            last_detail = f"HTTP {e.code} (尝试 {attempt}/3): {e.reason}"
        except Exception as e:
            last_detail = f"{type(e).__name__} (尝试 {attempt}/3): {e}"
    return False, last_detail


def fire(kind="info", post=None, text=None):
    """供其他脚本/CLI import 的 fire-and-forget 入口。
    用 Popen 重新拉起本脚本发通知 → 不阻塞调用方(连 HTTP 重试都不等) · 永不抛。
    例: from notify import fire; fire(kind="info", text="数据分析完成")"""
    args = [sys.executable, os.path.abspath(__file__), "--kind", kind]
    if post:
        args += ["--post", post]
    if text:
        args.append(text)
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="飞书写帖通知器")
    ap.add_argument("message", nargs="?", default=None, help="自由文本(可选)")
    ap.add_argument("--kind", choices=["test", "ready", "escape", "published", "brief", "info"],
                    default=None, help="通知类型(套用标准模板)")
    ap.add_argument("--post", default=None, help="帖子号，如 P114")
    args = ap.parse_args()

    url = find_webhook_url()
    if not url:
        print(f"[notify] ✗ 没找到 {ENV_KEY}（检查 .env 是否已填，且不是占位符）", file=sys.stderr)
        sys.exit(2)

    kind = args.kind or ("info" if args.message else "test")
    text = build_text(kind, args.post, args.message)

    ok, detail = send_feishu(url, text)
    if ok:
        print(f"[notify] ✓ 已送达飞书: {text}")
        sys.exit(0)
    print(f"[notify] ✗ 发送失败: {detail}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
