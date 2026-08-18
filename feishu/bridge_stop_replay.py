#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_stop_replay.py — 拿【真实历史 transcript】重放 Stop 装配：改前 vs 改后，判有没有丢正文。

为什么要有它（2026-08-17）：
v0.12.2 修完，两台机都想验「真的好了吗」，但验收计划卡在**没燃料**——TB25 那队占 99% 发作量的
phd-taoci 8 只里 7 只已停、cron 是 OFF，明天只有 1 只会产生数据。**「等一天看它还犯不犯」这种
验收，既要烧一天 token，又只覆盖当天恰好跑过的那点样本，还只能证明「这次没犯」、证明不了
「不会丢东西」。** 换个做法：历史 transcript 里那 618 次发作**全都在磁盘上躺着**，直接拿旧码和
新码各跑一遍、逐字节比——确定性、零等待、零 token、覆盖全部真实形状。

判据（唯一真正要命的那条）：
  **新码有没有【少发】旧码发过的正文？** 少发 = 退化（正文蒸发·主人收不到）。
  少发之外的差异只可能是「旧码把已发过的正文又拼了一遍」= 去重 = 正是本次要修的病，不算退化。
  实现：把旧码那张卡按段拆开，每段（≥40 字）都必须能在【新码这一轮 + 新码此前各轮】发过的
  正文里找到；找不到 = LOST，退出码 1。

用法:
  python feishu/bridge_stop_replay.py --transcript <session.jsonl>
  python feishu/bridge_stop_replay.py --transcript <a.jsonl> --transcript <b.jsonl> --baseline v0.12.1
  （--baseline 是改动【之前】的 git ref，默认 v0.12.1；脚本自己从 git 取那两个文件的历史版本，
    不需要你手工备份旧码。必须在本仓 git 工作区里跑。）
退出码: 0 = 零丢失 · 1 = 有 LOST（退化）· 2 = 取不到 baseline。
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = ("feishu/hooks/bridge_stop.py", "feishu/jsonl_reply_extract.py")
MIN_CHUNK = 40                      # 短于此的段落（「好的」「继续推进。」之类）不参与丢失判定·避免噪声


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _baseline_modules(ref):
    """从 git 取 baseline 版的两个文件 → 落临时目录 → import（不污染工作区·不需手工备份）。"""
    tmp = Path(tempfile.mkdtemp())
    for rel in FILES:
        try:
            blob = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{rel}"],
                                  capture_output=True, check=True).stdout
        except subprocess.CalledProcessError:
            print(f"❌ 取不到 {ref}:{rel} —— baseline ref 不存在或不在 git 工作区", file=sys.stderr)
            sys.exit(2)
        p = tmp / Path(rel).name
        p.write_bytes(blob)
    return _load("base_stop", tmp / "bridge_stop.py"), _load("base_extract", tmp / "jsonl_reply_extract.py")


def _stop_points(records, terminal):
    """Stop hook 会开火的位置 = 带文本的终结态 assistant 记录行号。"""
    out = []
    for i, line in enumerate(records):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") != "assistant" or msg.get("stop_reason") not in terminal:
            continue
        content = msg.get("content")
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip() for b in content):
            out.append(i + 1)
    return out


def replay(tp, base_stop, base_extract, new_stop, new_extract):
    raw = open(tp, encoding="utf-8").read().split("\n")
    stops = _stop_points(raw, base_stop._TERMINAL_STOP)
    tmpdir = Path(tempfile.mkdtemp())
    floor, delivered = 0, []                    # delivered = 新码【此前各轮 + 本轮】发过的全部正文
    same = dedup = lost = 0
    lost_samples = []
    for ln in stops:
        cut = tmpdir / f"c{ln}.jsonl"
        cut.write_text("\n".join(raw[:ln]) + "\n", encoding="utf-8")
        old = base_stop._final_turn_reply(str(cut), base_extract._is_real_user_message,
                                          base_extract._assistant_texts)
        new = new_stop._final_turn_reply(str(cut), new_extract._is_real_user_message,
                                         new_extract._assistant_texts, floor_line=floor)
        corpus = "\n\n".join(delivered + new["cards"])
        if old["cards"] == new["cards"]:
            same += 1
        else:
            missing = []
            for card in old["cards"]:
                for chunk in [c.strip() for c in card.split("\n\n")]:
                    if len(chunk) >= MIN_CHUNK and chunk not in corpus:
                        missing.append(chunk)
            if missing:
                lost += 1
                lost_samples.append((ln, missing[0][:120], len(missing)))
            else:
                dedup += 1
        if new["complete"]:
            floor = new["consumed_line"]
            delivered.extend(new["cards"])
    return {"stops": len(stops), "same": same, "dedup": dedup, "lost": lost, "samples": lost_samples}


def main():
    ap = argparse.ArgumentParser(description="真实 transcript 重放：改前 vs 改后，判有没有少发正文")
    ap.add_argument("--transcript", action="append", required=True, help="可给多次")
    ap.add_argument("--baseline", default="v0.12.1", help="改动【之前】的 git ref（默认 v0.12.1）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    base_stop, base_extract = _baseline_modules(args.baseline)
    new_stop = _load("new_stop", REPO / FILES[0])
    new_extract = _load("new_extract", REPO / FILES[1])
    base_stop._POLL_TRIES = new_stop._POLL_TRIES = 1        # 重放没有并发写·不必等竞态轮询

    rows, total_lost = [], 0
    for tp in args.transcript:
        r = replay(tp, base_stop, base_extract, new_stop, new_extract)
        r["transcript"] = Path(tp).name
        rows.append(r)
        total_lost += r["lost"]
        if not args.json:
            flag = "❌" if r["lost"] else "✅"
            print(f'{flag} {Path(tp).name[:12]}…  落点 {r["stops"]:4d} · 逐字节相同 {r["same"]:4d} · '
                  f'仅去重（旧码把已发过的又拼一遍）{r["dedup"]:4d} · **丢正文 {r["lost"]}**')
            for ln, sample, n in r["samples"][:3]:
                print(f'      ❌ L{ln} 少发 {n} 段，例：{sample!r}')
    if args.json:
        print(json.dumps({"clean": not total_lost, "baseline": args.baseline, "rows": rows},
                         ensure_ascii=False, indent=2))
    else:
        tot = sum(r["stops"] for r in rows)
        print(f'\n合计 {tot} 个落点 · 丢正文 {total_lost} 处' +
              ("  ✅ 零退化" if not total_lost else "  ❌ 有退化·别上线"))
    return 1 if total_lost else 0


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    sys.exit(main())
