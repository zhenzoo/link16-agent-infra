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
import json
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


def registry_path():
    """跨机 agent 目录（谁是谁 / 在哪台机 / 分管哪个仓 / open_id）的**唯一路径解析入口**。

    为什么要有这个函数（2026-08-17 · PLAN-926 §S1.1）：本仓准备设为 public，而
    `agent-registry.json` 里是全舰队 51 条真实 open_id + 主机名 —— 公开等于把内网拓扑发出去。
    解法照抄本文件上面 `bots_config_path` 已经验证过的「local 覆盖 committed」双层套路，
    但**路径解析原先散在三处各拼各的**（`registry.py` / 本文件 `_may_send_as` / `register_feishu_app.py`），
    三处不一致就会「读的和写的不是同一个文件」→ 登记完查不到。收到这里统一。

    解析顺序（先命中先用）：
      ① 环境变量 `LINK16_AGENT_REGISTRY` —— 显式 override（测试 / 特殊部署）
      ② `feishu/agent-registry.local.json` —— **本机真数据**（gitignore · 跨机靠 envsync 同步，不靠 git）
      ③ `feishu/agent-registry.json` —— committed 本（迁移期仍在；PLAN-926 §S1.1 阶段③ 会摘出 git）
      ④ `feishu/agent-registry.example.json` —— 脱敏样例（让**陌生人 clone 完**不至于直接崩，
         并且看得到 schema 长什么样；他自己 `cp` 一份成 local 就能用）

    ⚠️ **语义是「整盘接管」不是合并** —— 与 `bots_config_path` 一致。不合并的理由：阶段③ 之后
    committed 那本是**假数据样例**，一合并就会把样例里的假 bot 混进真舰队。
    ⚠️ 迁移期安全性：另外两台机还没有 local 文件 → 命中 ③ → **行为与改动前完全一致、零风险**。
    """
    override = (os.environ.get("LINK16_AGENT_REGISTRY") or "").strip()
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    for name in ("agent-registry.local.json", "agent-registry.json", "agent-registry.example.json"):
        p = here / name
        if p.exists():
            return p
    return here / "agent-registry.json"   # 都没有 → 回 committed 名，让调用方报「找不到」而不是报个怪路径


def resolve_wmux_rpc(project_root):
    """wmux-rpc.js 路径（连 wmux daemon 的 node 客户端 · 桥/wmux_session 跑 `node <这个> rpc …`）。

    历史坑：它原是「仓库外、没提交」的自建脚本，只活在主力机 home → 新机器 git clone 完没有它 = 桥连不上
    wmux = 拿不到 handler（见 docs/SOP-100-new-machine-setup.md §0）。2026-06-17 把正本提交进仓库 wmux/wmux-rpc.js，
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


# ─────────────────────────────────────────────────────────────────────────────
# 发送者身份闸（防跨-agent bot 冒用 · PLAN-920 · 2026-07-22）
#
# 病根：4 个发送面（send_feishu_{msg,media,file,voice}.py）的 --bot = 「用谁的凭据发」，但从不
# 核对 --bot 是不是调用方本人；凭据又全在共享 .env → 任意 agent 能冒用任意 bot 当发送者。
# 修法：把「以谁身份发」和「发给谁」分开——发送者身份（--bot）必须 == 本人；发给别人走 --to-agent
# （只改路由目标·不改发送者）。桥 spawn 的会话身份钉在 FEISHU_BRIDGE_SESSION（agent_runtime.worker_cmd
# 启动命令即焊·整会话不变），是可信锚点。
# ─────────────────────────────────────────────────────────────────────────────


def _norm_bot(s):
    """bot 名归一化：大小写不敏感 + `_`↔`-` 等价（与 send_feishu_msg._norm 同规则）。"""
    return (s or "").strip().lower().replace("_", "-")


def _may_send_as(me):
    """me 允许【代发】的 bot 名集合 —— agent-registry.json 的 `may_send_as` 字段（默认空=严格）。
    留给将来编排者【合法】代发多 bot 的口子（职责·非冒用）；现状全空 = 严格等值。
    best-effort：名册缺失/格式异常/任何错 → 返回空集（从严·绝不因读名册失败而放宽闸）。"""
    try:
        reg = registry_path()          # 统一解析（local → committed → example）· 见本文件 registry_path()
        data = json.loads(reg.read_text(encoding="utf-8"))
        for a in data.get("agents", []):
            if _norm_bot(a.get("name")) == _norm_bot(me):
                return {_norm_bot(x) for x in (a.get("may_send_as") or [])}
    except Exception:  # noqa: BLE001 — 读名册失败一律从严（空集），不放宽
        pass
    return set()


def assert_sender_identity(bot):
    """发送者身份闸：桥 spawn 的 agent 会话不得用【别的 bot】身份发消息（防冒用·PLAN-920）。

    - me = FEISHU_BRIDGE_SESSION（桥开机焊死·整会话不变）：
        · me 未设 → 纯 terminal / 操作者手动 / cron 守护进程本身 → 无锚可校验 → 放行（可信场景）。
          （注：cron 到点是把任务【注入 bot 的现有会话】，那会话 me 是设着的 → 照样被本闸管住·非后门。）
        · me 已设 & --bot == me → 以自己身份发（含发给别人：--to-agent 只改目标·不改这里）→ 放行。
        · me 已设 & --bot 在 me 的 may_send_as 白名单 → 显式允许代发（默认空）→ 放行。
        · 否则（me 已设 & --bot ≠ me & 不在白名单）→ 冒用 → SystemExit（报错指路 --to-agent）。
    调用点：各发送面 parse_args 之后、取凭据/发送之前第一件事。
    """
    me = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not me:
        return  # 无身份锚 = 可信操作者场景（terminal / 手动 / cron 守护进程）→ 放行
    if _norm_bot(bot) == _norm_bot(me):
        return  # 以自己身份发
    if _norm_bot(bot) in _may_send_as(me):
        return  # 显式白名单代发（编排者场景·默认空=不会命中）
    raise SystemExit(
        f"❌ 身份越界：你是 [{me}]，不能用 [{bot}] 的身份发送（防冒用别的 bot 当发送者·PLAN-920）。\n"
        f"   · 要把消息发给别的 agent → 用 --to-agent {bot}（发送者仍是你自己·只改路由目标）。\n"
        f"   · 若确需代发（编排者场景）→ 在 agent-registry.json 给 [{me}] 加 "
        f"\"may_send_as\": [\"{bot}\"]。"
    )
