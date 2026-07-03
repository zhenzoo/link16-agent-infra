#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a2a_guard.py — a2a 死循环防护（ARCH-140 §2.5）：低信号判定 + 静音集 + 空转计数。

分层（越底层越不靠 agent）：
  ① 结束工具 a2a_end.py 调 mute_add() 落静音标志 → 桥 on_message 注入前 mute_has() → skip。
  ② 桥 on_message 每条 a2a 调 SpinTracker.observe() 计数连续空转 → 满 N 返回 tripped → 桥 mute_add()+DM。

纯逻辑（strip_body / is_low_signal / SpinTracker）零 I/O·可单测；文件 I/O（mute/lastpeer）原子写·绝不抛。
keying 一律用 peer **open_id**（回信不带名字戳·只有它稳定必在·ARCH-140 §2.5）；name 仅供告警显示。
"""
import json
import os
import re
import time

SPIN_N = 3           # 连续「低内容」a2a 消息到此 → 熔断（2026-07-03：4→3·含短消息触发）
RECENT_K = 3         # 判重复：与该 peer 最近 K 条正文比
SHORT_MAX = 50       # 剥壳后正文 ≤ 此长度 = 短消息 = 低内容（抓「客气环」·见下 is_short 说明）

# footer = Stop hook（bridge_stop.py）缀在末卡的过程小结：`\n\n---\n✅ 已完成 · 🔧.. 💭.. · 🪙..`
_FOOTER_RE = re.compile(r"\n-{3,}\n✅.*$", re.S)
# a2a 发信方自盖的戳 [飞书_from_X_to_Y]（send_feishu_msg 追加在文末）——是桥元数据、非正文内容，
# 判「长度/重复」前先剥掉（否则 30 字左右的戳会把短消息撑过阈值、也让整条比较不准·2026-07-03）。
_STAMP_RE = re.compile(r"\[飞书_from_.+?_to_.+?\]")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w", re.UNICODE)   # 词字符（字母/数字/CJK·Unicode-aware）；纯标点/emoji 无词字符
_PUNCT_EDGE_RE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)   # 首尾标点/符号


def strip_body(text):
    """剥掉 footer（过程小结）+ from 戳 → 返回 a2a 正文核心。@mention 桥入站已剥。绝不抛。"""
    t = _FOOTER_RE.sub("", (text or ""))
    return _STAMP_RE.sub("", t).strip()


def _norm(s):
    """归一（判重复用）：小写 + 折叠内部空白 + 去首尾标点 → `(Silent.)`/`Silent`/`silent.` 视同。"""
    s = _WS_RE.sub(" ", (s or "").strip().lower())
    return _PUNCT_EDGE_RE.sub("", s)


def is_low_signal(body, recent_bodies=()):
    """body（已 strip_body）是不是【空转/低信号】——两条【零误伤】判据（不靠长度·长度分不清
    「20/A吧」这种简短实质 vs 空转·2026-07-03 单测实证）：
      · R1 无内容：正文没有任何词字符（纯标点/emoji·如 `.` `。` `…` `👀`）；
      · R3 重复：与该 peer 最近几条正文 norm 后相等（`(Silent.)` 刷屏 / `Standing by.` 连发）。
    简短但【新颖】的真实消息（有词字符 + 不重复）→ 判有营养 → 永不误伤。
    recent_bodies = 该 peer 最近若干条正文（不含本条）。"""
    b = (body or "").strip()
    if not b:
        return True                                  # 空
    if not _WORD_RE.search(b):
        return True                                  # R1 无词字符 = 无内容
    nb = _norm(b)
    return bool(nb) and any(_norm(x) == nb for x in recent_bodies)   # R3 与近 K 条重复


class SpinTracker:
    """桥进程内存态：每 peer 连续空转计数 + 最近 K 条正文（判重复）。跨桥重启丢失=无所谓（重开=新计数）。
    observe(peer, text) → tripped(bool)：True 仅在【首次达到连续 N 空转】那条返回（避免重复 DM）。
    有营养的消息（有词字符 + 不与近 K 条重复）→ 计数清零 = 有效对话永不误伤（含简短对话）。"""

    def __init__(self, n=SPIN_N, recent_k=RECENT_K, short_max=SHORT_MAX):
        self.n = n
        self.recent_k = recent_k
        self.short_max = short_max
        self._count = {}       # peer_open_id -> 连续低内容数
        self._recent = {}      # peer_open_id -> 最近 K 条正文 list（判重复）
        self._tripped = set()  # 已熔断过的 peer（避免重复 DM）

    def observe(self, peer, text):
        body = strip_body(text)
        recent = self._recent.get(peer, [])
        # 「低内容」= 无词字符 / 与近 K 条重复 / 【短】(≤short_max)。第三条抓「客气环」——一堆有内容但没意义
        #   的短往返(🤝对齐/🫡待命/…)，empty/dup 抓不住(带词、不逐字重复)、轮数也抓不住(有效活比它更长)；
        #   唯一干净分界是长度(有效活全是长消息·2026-07-03 群日志实证 12 轮有效 vs 4 轮客气环)。仅 a2a·真人不计。
        low = is_low_signal(body, recent) or len(body) <= self.short_max
        self._recent[peer] = (recent + [body])[-self.recent_k:]   # 滚动保留最近 K 条
        if not low:
            self._count[peer] = 0            # 有营养的【长】消息 → 清零（长对话/有效协作永不误伤）
            return False
        c = self._count.get(peer, 0) + 1
        self._count[peer] = c
        if c >= self.n and peer not in self._tripped:
            self._tripped.add(peer)
            return True
        return False

    def forget(self, peer):
        """peer 被 unmute / 手动复位时清它的计数（下次从头数）。"""
        self._count.pop(peer, None)
        self._recent.pop(peer, None)
        self._tripped.discard(peer)


# ---------- 静音集文件（keyed peer open_id）----------
def mute_path(state_dir, bot):
    return os.path.join(state_dir, f"bridge-a2a-mute-{bot}.json")


def mute_load(state_dir, bot):
    """→ {open_id: {"name":.., "ts":.., "by":"tool|fuse"}}。绝不抛。"""
    try:
        d = json.loads(open(mute_path(state_dir, bot), encoding="utf-8").read())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _atomic_write(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def mute_add(state_dir, bot, open_id, *, name=None, by="tool"):
    """加静音（keyed open_id）。返回 True=新加、False=已在集里。"""
    if not open_id:
        return False
    d = mute_load(state_dir, bot)
    if open_id in d:
        return False
    d[open_id] = {"name": name or "", "ts": int(time.time()), "by": by}
    _atomic_write(mute_path(state_dir, bot), d)
    return True


def mute_has(state_dir, bot, open_id):
    return bool(open_id) and open_id in mute_load(state_dir, bot)


def mute_remove(state_dir, bot, open_id=None):
    """open_id=None → 清空全部；否则移除单个。返回被移除的 open_id 列表。"""
    d = mute_load(state_dir, bot)
    if open_id is None:
        removed = list(d.keys())
        if removed:
            _atomic_write(mute_path(state_dir, bot), {})
        return removed
    if open_id in d:
        d.pop(open_id)
        _atomic_write(mute_path(state_dir, bot), d)
        return [open_id]
    return []


# ---------- 当前 a2a 对手（给零参 a2a_end 用）----------
def lastpeer_path(state_dir, bot):
    return os.path.join(state_dir, f"bridge-a2a-lastpeer-{bot}.json")


def lastpeer_write(state_dir, bot, open_id, name=None):
    if not open_id:
        return
    _atomic_write(lastpeer_path(state_dir, bot),
                  {"open_id": open_id, "name": name or "", "ts": int(time.time())})


def lastpeer_load(state_dir, bot):
    try:
        return json.loads(open(lastpeer_path(state_dir, bot), encoding="utf-8").read())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
