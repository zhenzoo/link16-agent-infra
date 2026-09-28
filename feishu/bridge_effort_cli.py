"""Agent-facing Link16 effort selection for the next Codex turn."""

import argparse
import json
import os
from pathlib import Path

import agent_runtime
import bridge_effort


def main(argv=None):
    parser = argparse.ArgumentParser(description="为当前飞书 bot 的下一个 Codex 回合选择 effort")
    parser.add_argument("effort", help="medium、high、xhigh（也接受 extra high）")
    parser.add_argument("--bot", help="桥内默认取 FEISHU_BRIDGE_SESSION；隔离试验可显式指定")
    parser.add_argument("--profile", help="桥内默认取 LINK16_AGENT_PROFILE；隔离试验可显式指定")
    parser.add_argument("--state-dir", type=Path, help="桥内默认取 FEISHU_BRIDGE_OUTBOX_DIR")
    args = parser.parse_args(argv)
    session_bot = os.environ.get("FEISHU_BRIDGE_SESSION", "").strip()
    session_profile = os.environ.get(agent_runtime.PROFILE_ENV, "").strip()
    if session_bot and args.bot and args.bot != session_bot:
        parser.error("当前桥会话不能替另一只 bot 改 effort")
    if session_profile and args.profile and args.profile != session_profile:
        parser.error("当前桥会话不能替另一个 profile 改 effort")
    session_state_dir = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR", "").strip()
    if session_state_dir and args.state_dir and args.state_dir.resolve() != Path(session_state_dir).resolve():
        parser.error("当前桥会话不能把 effort 写到另一个状态目录")
    bot = args.bot or session_bot
    profile = args.profile or session_profile
    if not bot or not profile:
        parser.error("缺少 bot/profile；请从飞书桥会话调用，或在隔离试验显式指定")
    spec = agent_runtime.profile_spec(profile)
    if spec.runtime != "codex":
        parser.error(f"{profile} 是 {spec.runtime} profile，不能用 Codex effort 入口")
    state_dir = args.state_dir or Path(
        os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or Path(__file__).resolve().parent / "_state")
    level = bridge_effort.normalize(args.effort)
    model = bridge_effort.profile_model(spec.home_path)
    bridge_effort.save(state_dir, bot, profile, level, model)
    print(json.dumps({"bot": bot, "profile": profile, "model": model, "effort": level,
                      "state": "next_turn_selected", "current_turn_changed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
