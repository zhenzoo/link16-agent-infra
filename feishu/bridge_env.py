#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_env.py — 飞书桥【跨机可移植】单一真相：① .env 路径解析 ② bot 名册路径解析。

为什么有这个模块（2026-06-16 · 方案 A）：桥原来把 `.env` 路径写死成 `E:\\410_VibeCoding\\.env`、
把 bot 名册写死成 committed 的 `bridge-bots.json`。换一台机（盘符/用户名不同）就：
  · 读不到凭据（.env 在 D 盘不是 E 盘）→ 桥根本起不来；
  · committed 名册的 cwd 全是另一台机的绝对路径 → 改它又跟另一台机 git 打架。
本模块把这两处统一成「不写死盘符 + 机器本地优先」的解析，三个入口（feishu_bridge /
register_feishu_app / bridge_feishu_probe / bridge_doctor）都走这里，跨机零冲突。

遵循用户 CLAUDE.md 跨机铁律：外部 VibeCoding 路径走 `VIBECODING_ROOT`，绝不硬编码盘符/用户名。
"""
import os
from pathlib import Path

# legacy 兜底（老机器从没设 VIBECODING_ROOT 时的最后一根稻草 · 绝不破坏另一台机已跑通的行为）
_LEGACY_ENV = Path(r"E:\410_VibeCoding\.env")


def resolve_env_path(start=None):
    """解析 VibeCoding `.env` 全路径（读写凭据共用）。优先级：

      1) 环境变量 XHS_ENV_FILE — 显式指定 .env 全路径（最高优先 · 已是 probe_v5 的既有约定）
      2) 环境变量 VIBECODING_ROOT → <root>/.env（用户每台机一次性设 · 即便文件还没建也返回它 · 供 register 写）
      3) 从本模块/调用方所在仓库逐级上溯找到的第一个 .env
         （兼容 Post/xhs-card-gen 与 Post/tools/xhs-card-gen 两种仓库布局 · 无需任何 env var）
      4) legacy 兜底 E:\\410_VibeCoding\\.env

    返回 Path（不保证 .exists() · 写入场景需要先于文件存在拿到目标路径）。
    """
    explicit = os.environ.get("XHS_ENV_FILE")
    if explicit:
        return Path(explicit)

    vc = os.environ.get("VIBECODING_ROOT")
    if vc:
        return Path(vc) / ".env"   # 权威位置 · 即便还不存在也返回（register 写新 .env 用）

    base = Path(start).resolve() if start else Path(__file__).resolve()
    for p in [base, *base.parents]:
        cand = p / ".env"
        if cand.exists():
            return cand

    return _LEGACY_ENV


def bots_config_path(project_root):
    """bot 名册路径：机器本地 `bridge-bots.local.json` 存在则用它（**整盘覆盖** committed · gitignore ·
    每台机各管各的 bot + cwd · 不碰入了 git 的共享文件 · 跨机零冲突）；否则用 committed `bridge-bots.json`。

    语义「present = 整盘接管」而非合并：本机若起桥，**只跑** local 文件里列的 bot —— 不会去连另一台机的
    飞书应用（同一应用两台机各连一条 WS 会撞），也不会动 committed 文件。另一台机没有 local 文件 → 行为零变化。
    """
    local = Path(project_root) / "feishu" / "bridge-bots.local.json"   # link16: 桥代码在 feishu/(原 orchestrator/)
    if local.exists():
        return local
    return Path(project_root) / "feishu" / "bridge-bots.json"


def resolve_wmux_rpc(project_root):
    """wmux-rpc.js 路径（连 wmux daemon 的 node 客户端 · 桥/wmux_session 跑 `node <这个> rpc …`）。

    历史坑：它原是「仓库外、没提交」的自建脚本，只活在主力机 home → 新机器 git clone 完没有它 = 桥连不上
    wmux = 拿不到 handler（见 SETUP-new-machine.md §0）。2026-06-17 把正本提交进仓库 orchestrator/wmux-rpc.js，
    新机不用再手放。解析顺序：

      1) 环境变量 WMUX_RPC_PATH（显式 override · 热改/调试用）
      2) ~/wmux-rpc.js（老机器/主力机手放并热改的 · 存在则优先 · 保持其原行为不变 · 零回归）
      3) 仓库副本 orchestrator/wmux-rpc.js（版本化正本 · 新机 clone 即有 · 永久解）
    """
    env = os.environ.get("WMUX_RPC_PATH")
    if env:
        return Path(env)
    home = Path.home() / "wmux-rpc.js"
    if home.exists():
        return home
    return Path(project_root) / "wmux" / "wmux-rpc.js"   # link16: 正本在 wmux/(原 orchestrator/)
