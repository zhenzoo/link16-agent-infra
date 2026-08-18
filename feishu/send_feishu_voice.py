#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_feishu_voice.py — 把本地【任意音频】作为真·语音气泡（可拖动进度条）发到某 bot 的飞书会话(DM/群)。

和另外三种「发东西」区分清楚：
  · `feishu_bridge.py send --image <图>` → 图片消息（内联显示）
  · `feishu_bridge.py send --doc <md/html>` → 飞书在线云文档链接（在线看/可编辑）
  · `send_feishu_file.py --file <任意文件>` → 文件附件（对方下载打开）
  · **本工具 → 语音消息（msg_type=audio·飞书里是可播放/可拖动进度条的语音气泡）**

🚨 为什么单独成工具（根因·2026-06-18 [飞书-explore] 实证）：
飞书语音消息要正常显示**可拖动的进度条**，上传文件时**必须带 `duration`（毫秒·与实际时长一致）**。
飞书官方 Audio 文档原文：「指定音频时长(duration)，且确保与实际时长一致。否则会导致**播放进度展示不准确**」；
Upload File API：「duration… in milliseconds. **If this field is not specified, no specific duration is displayed.**」
而我们用的 lark_channel SDK 的 `OutboundAudio` 发送路径里，`driver.upload_file()` 只传 file_type/file_name，
**从不传 duration**（`parse_opus_duration` 被导出却从没被调用）→ 飞书拿不到时长 → 点播放直接跳到结尾、
进度条不可拖动。**这不是飞书组件 bug，是 SDK 漏了 duration**。本工具绕开 SDK、直接走 REST 注入 duration 根治。

另：飞书语音**只认 Ogg/Opus 编码**（其他格式必须转码）。本工具默认用 ffmpeg 转成单声道 48k Ogg/Opus，
顺带保证 granule/duration 干净，再用 ffprobe 读真实时长。

用法：
  python orchestrator/send_feishu_voice.py --bot explore --audio clip.mp3
  python orchestrator/send_feishu_voice.py --bot explore --to oc_xxx --audio note.wav --text "语音说明" --json
  python orchestrator/send_feishu_voice.py --bot explore --audio already.opus --no-transcode   # 跳过转码（须已是 Ogg/Opus）
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ORCH = Path(__file__).resolve().parent
PROJECT = ORCH.parent
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(PROJECT / "scripts"))
from bridge_env import resolve_env_path, bots_config_path, assert_sender_identity  # noqa: E402
# 飞书 REST 传输原语（绕代理 OPENER）——和 feishu_docs 同走 orchestrator/feishu_rest（Phase1.2 解耦）
from feishu_rest import api, tenant_token, send_msg  # noqa: E402

ENV_PATH = resolve_env_path()
BASE = "https://open.feishu.cn/open-apis"


def _env(*keys):
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if m and m.group(1) in keys:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def _bot_creds(bot):
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


def _which(name):
    from shutil import which
    return which(name) or which(name + ".exe")


def transcode_to_opus(src: Path) -> Path:
    """任意音频 → 单声道 48k Ogg/Opus（飞书语音唯一认的编码）·返回临时 .opus 路径。"""
    if not _which("ffmpeg"):
        raise SystemExit("❌ 找不到 ffmpeg（飞书语音须 Ogg/Opus·装 ffmpeg 或用 --no-transcode 传已是 opus 的文件）")
    out = Path(tempfile.gettempdir()) / f"feishu_voice_{uuid.uuid4().hex}.opus"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k",
           "-application", "voip", str(out)]
    # 读取侧规矩（PLAN-929）：ffmpeg 在中文 Windows 往 stderr 写 **GBK**；PYTHONUTF8=1 下 text=True
    # 按 UTF-8 解码会当场崩读线程。我们【往外写】强制 UTF-8，【读别人】一律 errors="replace"。
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not out.exists():
        raise SystemExit(f"❌ ffmpeg 转码失败: {(r.stderr or '')[:300]}")
    return out


def _granule_duration_ms(opus: Path) -> int:
    """直接读 Ogg/Opus 末页 granule_position → 毫秒（零依赖·不需 ffprobe）。
    Opus 恒 48kHz 采样 → ms = granule / 48。从文件尾向前找最后一个 `OggS` 页·读 +6 偏移的 int64。
    （等价 lark_channel 的 parse_opus_duration·但这里内联·不依赖它是否被顶层 re-export）"""
    try:
        buf = opus.read_bytes()
    except OSError:
        return 0
    if len(buf) < 27:
        return 0
    i = len(buf) - 27
    while i >= 0:
        if buf[i:i + 4] == b"OggS":
            try:
                granule = struct.unpack_from("<q", buf, i + 6)[0]
            except struct.error:
                return 0
            return int(round(granule / 48)) if granule > 0 else 0
        i -= 1
    return 0


def _ffmpeg_probe_ms(src: Path) -> int:
    """回退：ffmpeg -i 把 `Duration: HH:MM:SS.xx` 打到 stderr·解析它（只需 ffmpeg·不需 ffprobe）。"""
    if not _which("ffmpeg"):
        return 0
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # 同上：读 ffmpeg 的 GBK 不许崩
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", r.stderr or "")
    if not m:
        return 0
    h, mn, s, cs = m.groups()
    return int(((int(h) * 3600 + int(mn) * 60 + int(s)) * 1000) + int(cs.ljust(2, "0")[:2]) * 10)


def probe_duration_ms(opus: Path, fallback_src: Path | None = None) -> int:
    """语音进度条的命根子：先读 Ogg/Opus granule（精确），失败再回退 ffmpeg 解析源时长。"""
    ms = _granule_duration_ms(opus)
    if ms <= 0 and fallback_src is not None:
        ms = _ffmpeg_probe_ms(fallback_src)
    return ms


def upload_opus(token: str, opus: Path, duration_ms: int) -> str:
    """im/v1/files 上传 opus → file_key·【关键】带 file_type=opus + duration（手搓 multipart·标准库）。"""
    boundary = f"----{uuid.uuid4().hex}"
    data = opus.read_bytes()
    parts = b""
    for k, v in (("file_type", "opus"), ("file_name", opus.name), ("duration", str(int(duration_ms)))):
        parts += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    parts += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{opus.name}\"\r\n"
        f"Content-Type: audio/opus\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    d = api("POST", f"{BASE}/im/v1/files", token=token,
            raw_body=parts, content_type=f"multipart/form-data; boundary={boundary}")
    if d.get("code") != 0:
        raise SystemExit(f"❌ 语音上传失败 {d.get('code')} {d.get('msg')}")
    return d["data"]["file_key"]


def _id_type(target: str) -> str:
    return "open_id" if target.startswith("ou_") else "chat_id"


def main():
    ap = argparse.ArgumentParser(description="把本地音频作为可拖动进度条的语音气泡发到飞书 DM/群（带 duration 根治）")
    ap.add_argument("--bot", required=True, help="哪个 bot（用它的飞书应用凭据发）")
    ap.add_argument("--audio", required=True, help="要发的本地音频（mp3/wav/m4a/opus/ogg… 任意·默认转码 Ogg/Opus）")
    ap.add_argument("--to", default=None, help="目标 chat_id(oc_)/open_id(ou_)；不给=该 bot 会话 chat_id")
    ap.add_argument("--text", default=None, help="附带文字说明（语音消息不带 caption → 作为单独一条消息发）")
    ap.add_argument("--no-transcode", action="store_true", help="跳过转码（输入须已是 Ogg/Opus）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    a = ap.parse_args()
    assert_sender_identity(a.bot)   # 身份闸：桥会话不得冒用别的 bot 发（PLAN-920）

    src = Path(a.audio)
    if not src.is_file():
        raise SystemExit(f"❌ 音频不存在: {a.audio}")
    target = a.to or _session_chat(a.bot)
    if not target:
        raise SystemExit(f"❌ 没有可发目标（--to 没给，且 bridge-session-{a.bot}.json 无 chat_id·先在飞书 @ 它一次）")
    app_id, app_secret = _bot_creds(a.bot)

    tmp = None
    try:
        if a.no_transcode:
            opus = src
        else:
            opus = tmp = transcode_to_opus(src)
        dur = probe_duration_ms(opus, fallback_src=src)
        token = tenant_token(app_id, app_secret)
        file_key = upload_opus(token, opus, dur)
        rit = _id_type(target)
        send_msg(token, rit, target, "audio", {"file_key": file_key})
        text_ok = None
        if a.text:
            try:
                send_msg(token, rit, target, "text", {"text": a.text})
                text_ok = True
            except SystemExit:
                text_ok = False
    finally:
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    out = {"voice_ok": True, "duration_ms": dur, "text_ok": text_ok,
           "bot": a.bot, "to": target, "file": src.name, "file_key": file_key}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        secs = dur / 1000.0
        print(f"✅ 语音{' + 文字' if a.text else ''} → {target}（{src.name} · {secs:.1f}s · "
              f"进度条已带 duration={dur}ms）")
    sys.exit(0)


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
