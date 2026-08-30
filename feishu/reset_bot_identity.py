#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reset_bot_identity.py — 同名重建一只 bot 时，清掉【上一个飞书应用】留下的身份状态。

**什么时候用**：删掉某只 bot 的飞书应用、又用**同一个 roster 名字**注册一个新应用（换应用、
换租户、旧应用权限批不下来）。本地记录按 **bot 名字** 存，所以 inbox/outbox/receipts 会自然
接上；但另一批文件里存的是**上一个应用的 open_id / chat_id**，留着就是地雷。

**为什么必须清（2026-08-27 实测 · 同一个人在三个应用下的 open_id 各不相同）**：
    tb26-baseball        ou_00000000000000000000000000000065
    tb26-baseball-2      ou_00000000000000000000000000000050
    tb26-baseball-zhen   ou_00000000000000000000000000000030
`open_id` 是 **per-app** 的。`bridge-owner-<bot>.json` 留着老 open_id → 桥以为自己认过主、
拿一个在新应用下不存在的 open_id 去投 DM → 飞书 `230013` → 兜底链降级 → **刷群**。
这就是 ARCH-110 §2.5.3 记的 tb25-phd-taoci-7 事故类型（18 万次 230013 · 群里刷 767 条）。

**清什么 / 留什么**（清单见 PER_APP_STATE / KEEP_ALWAYS，都是精确文件名，不用 glob）：
  清（archive 走，不真删）：owner / session / turn-route / delivery-state / answer-state
                            / pending / watchdog-handoff —— 全是 per-app 的 id 或旧会话指针
  留：inbound / outbox / outbound / receipts（**这就是你要接管的聊天记录**）
      + outbox-hwm（清了会把 1700+ 张老卡重发一遍）
      + stop-cursor（transcript 游标·不是 per-app；清了会重发老 final）

**不做真删除**：命中的文件是 move 进 `feishu/_state/_bot-reset-archive/<bot>-<ts>/`，随时能捞回来。

CLI：
  python feishu/reset_bot_identity.py --bot tb26-baseball                  # 默认 dry-run，零写入
  python feishu/reset_bot_identity.py --bot tb26-baseball --apply          # 真归档
  python feishu/reset_bot_identity.py --bot tb26-baseball --refresh-openid --apply
                                       # 新应用注册好之后跑：把 agent-registry.json 的 open_id 换成新的
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import bots_config_path, registry_path, resolve_env_path  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
STATE_DIR = HERE / "_state"
ARCHIVE_ROOT = STATE_DIR / "_bot-reset-archive"

# 存着【上一个应用】的 open_id / chat_id / 旧会话指针 —— 同名重建时必须清
PER_APP_STATE = [
    ("bridge-owner-{bot}.json", "主人 open_id（per-app）—— 不清会 230013 → 刷群"),
    ("bridge-session-{bot}.json", "会话 chat_id + open_id + pty pin（per-app）"),
    ("bridge-turn-route-{bot}.json", "本轮回信路由便签（存旧 chat_id）"),
    ("bridge-delivery-state-{bot}.json", "待投递队列（可能挂着旧 chat 的文档）"),
    ("bridge-answer-state-{bot}.json", "答案去重台账（按内容 hash·留着会把新回复误判成重复）"),
    ("bridge-pending-{bot}.json", "未消费的入站便签（旧应用的消息）"),
    ("watchdog-handoff-{bot}.json", "看门狗交接（旧 session/pty）"),
]

# 按 bot 名字存的记录 —— 这就是「接管聊天记录」要保住的东西，绝不动
KEEP_ALWAYS = [
    ("bridge-inbound-{bot}.jsonl", "入站记录（你 → bot）"),
    ("bridge-outbox-{bot}.jsonl", "出站卡片完整正文（bot → 你）"),
    ("bridge-outbound-{bot}.jsonl", "主动出站记录"),
    ("bridge-receipts-{bot}.jsonl", "真实送达回执"),
    ("bridge-outbox-hwm-{bot}.json", "出站高水位 —— 清了会把历史卡片全重发一遍"),
    ("bridge-stop-cursor-{bot}.json", "transcript 游标（非 per-app）—— 清了会重发老 final"),
]


def _known_bot(bot):
    path = bots_config_path(PROJECT)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for item in (data.get("bots") or []):
        if item.get("name") == bot:
            return item
    return None


def plan(bot):
    """返回 (要归档的[(path, why)], 保住的[(path, why, exists)])。纯只读。"""
    to_archive, keep = [], []
    for pattern, why in PER_APP_STATE:
        path = STATE_DIR / pattern.format(bot=bot)
        if path.exists():
            to_archive.append((path, why))
    for pattern, why in KEEP_ALWAYS:
        path = STATE_DIR / pattern.format(bot=bot)
        keep.append((path, why, path.exists()))
    return to_archive, keep


def registry_openid(bot):
    reg = registry_path()
    if not reg or not Path(reg).exists():
        return None, None
    data = json.loads(Path(reg).read_text(encoding="utf-8"))
    for item in (data.get("agents") or []):
        if item.get("name") == bot:
            return item.get("open_id"), Path(reg)
    return None, Path(reg)


def _live_openid(bot):
    """从飞书现拉这只 bot 当前应用下的 open_id（bot/v3/info）。"""
    spec = _known_bot(bot)
    if not spec:
        return None, "本机名册无 bot %s" % bot
    import bridge_scope_audit as audit

    token, err = audit._token(spec["app_id_env"], spec["app_secret_env"])  # noqa: SLF001
    if err:
        return None, err
    result = audit._req("GET", audit.BASE + "/bot/v3/info", token)  # noqa: SLF001
    if result.get("code") != 0:
        return None, "bot/v3/info %s: %s" % (result.get("code"), result.get("msg"))
    return ((result.get("bot") or {}).get("open_id")), None


def rewrite_registry_openid(bot, new_open_id, apply=False):
    """把 agent-registry.json 里这只 bot 的 open_id 换掉（就地改一处·不重排文件）。

    为什么需要：register_feishu_app.append_registry_stub 对已存在的名字是【幂等跳过】，
    同名重建时它不会刷新 open_id → a2a 寻址（send_feishu_msg --to-agent）会打到一个死 id。"""
    old, reg = registry_openid(bot)
    if not reg or not reg.exists():
        return {"ok": False, "why": "找不到 agent-registry"}
    if not old:
        return {"ok": False, "why": "agent-registry 里没有 %s 这条" % bot}
    if old == new_open_id:
        return {"ok": True, "changed": False, "old": old, "new": new_open_id, "path": str(reg)}
    text = reg.read_text(encoding="utf-8")
    marker = '"name": "%s"' % bot
    if marker not in text:
        return {"ok": False, "why": "名册里定位不到 %s" % marker}
    if text.count('"%s"' % old) != 1:
        return {"ok": False, "why": "旧 open_id 在文件里出现 %d 次，不敢就地替换"
                                    % text.count('"%s"' % old)}
    if apply:
        reg.write_text(text.replace('"%s"' % old, '"%s"' % new_open_id), encoding="utf-8")
    return {"ok": True, "changed": True, "old": old, "new": new_open_id,
            "path": str(reg), "applied": apply}


def main():
    ap = argparse.ArgumentParser(
        description="同名重建 bot 时清掉上一个飞书应用的身份状态（默认 dry-run）")
    ap.add_argument("--bot", required=True)
    ap.add_argument("--apply", action="store_true", help="真执行；不给 = 只打印计划、零写入")
    ap.add_argument("--refresh-openid", action="store_true",
                    help="新应用注册好之后跑：从 bot/v3/info 现拉 open_id 写回 agent-registry.json")
    args = ap.parse_args()
    bot = args.bot

    if args.refresh_openid:
        live, err = _live_openid(bot)
        if not live:
            print("❌ 拉不到 %s 当前的 open_id：%s" % (bot, err))
            return 1
        result = rewrite_registry_openid(bot, live, apply=args.apply)
        if not result.get("ok"):
            print("❌ %s" % result.get("why"))
            return 1
        if not result.get("changed"):
            print("✅ agent-registry 的 open_id 已是最新（%s），无需改" % live)
            return 0
        head = "已改" if args.apply else "将改（dry-run·加 --apply 才真写）"
        print("%s agent-registry.json 的 %s.open_id" % (head, bot))
        print("   旧 %s" % result["old"])
        print("   新 %s" % result["new"])
        return 0

    to_archive, keep = plan(bot)
    print("=== reset-bot-identity · bot=%s%s" % (bot, "" if args.apply else "（dry-run·零写入）"))
    print("")
    print("【清 · 上一个应用的身份状态】共 %d 个" % len(to_archive))
    if not to_archive:
        print("   —— 一个都没有（这只 bot 没跑过，或已经清过）")
    for path, why in to_archive:
        print("   · %s" % path.name)
        print("       %s" % why)
    print("")
    print("【留 · 你要接管的聊天记录 + 防重发游标】")
    for path, why, exists in keep:
        size = ("%d 行" % sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
                if exists and path.suffix == ".jsonl" else ("在" if exists else "不在"))
        print("   · %-42s %-8s %s" % (path.name, size, why))

    old_oid, _reg = registry_openid(bot)
    print("")
    print("【agent-registry.json】open_id = %s" % (old_oid or "（这条不存在）"))
    print("   ⚠️ register_feishu_app 对已存在的名字是幂等跳过 → 重建后【不会】自动刷新这个 id。")
    print("   新应用注册好、认主完成后跑：")
    print("   python feishu/reset_bot_identity.py --bot %s --refresh-openid --apply" % bot)

    if not args.apply:
        print("")
        print("以上为计划。确认无误后加 --apply 真执行（命中的文件是 move 进 "
              "feishu/_state/_bot-reset-archive/，不是删除，可原样捞回）。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = ARCHIVE_ROOT / ("%s-%s" % (bot, stamp))
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for path, _why in to_archive:
        target = dest / path.name
        shutil.move(str(path), str(target))
        moved.append(path.name)
    print("")
    print("✅ 已归档 %d 个文件 → %s" % (len(moved), dest))
    for name in moved:
        print("   · %s" % name)
    print("")
    print("下一步：删飞书应用 → 同名重新注册 → 认主 → --refresh-openid --apply → 起桥。"
          "完整步骤见 docs/SOP-120 §4.3。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
