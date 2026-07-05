#!/usr/bin/env python3
"""查名册 —— 跨机 agent 目录（SSOT = feishu/agent-registry.json）的唯一查询入口。

回答：所有 agent 有哪些名 / 在哪台机 / 分管哪个仓 / open_id 多少 / 某个仓该通知对面谁拉。
别手 grep JSON、别读 SOP-120 的人读表 —— 一律走这个工具（它才是机器认的）。

CLI:
  python feishu/registry.py                      # 全量表（按机器分组）
  python feishu/registry.py list --shared        # 只看 3 个共享仓
  python feishu/registry.py list --machine tb24  # 只看某台机
  python feishu/registry.py list --repo link16-agent-infra
  python feishu/registry.py whois tb24-link16    # 按名字/open_id 查一条
  python feishu/registry.py whois ou_00000000000000000000000000000028
  python feishu/registry.py peers link16-agent-infra   # 该仓在【别的机】上谁管（repo-sync 路由用）
  python feishu/registry.py resolve ou_7faa...    # open_id -> 名字（桥修戳/脚本用·只打一个词）
  加 --json 任意子命令 => 机器可读输出。

也可 import：name_for_open_id() / peers_for_repo() / load_agents()。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "agent-registry.json"


def load_registry() -> dict:
    with REGISTRY_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_agents() -> list[dict]:
    return load_registry().get("agents", [])


def _match(agent: dict, query: str) -> bool:
    q = (query or "").strip()
    return agent.get("name") == q or agent.get("open_id") == q or agent.get("at_name") == q \
        or agent.get("at_name") == ("@" + q)


def find(query: str) -> dict | None:
    for a in load_agents():
        if _match(a, query):
            return a
    return None


def name_for_open_id(open_id: str, default: str | None = None) -> str | None:
    """桥修戳用：open_id -> 友好名字。查不到回 default（桥应传个非 open_id 的兜底）。"""
    for a in load_agents():
        if a.get("open_id") == open_id:
            return a.get("name")
    return default


def peers_for_repo(repo: str, exclude_machine: str | None = None,
                   verified_only: bool = False) -> list[dict]:
    """某个仓在【别的机】上由谁管 —— repo-sync 通知路由的核心查询。"""
    out = []
    for a in load_agents():
        if a.get("repo") != repo:
            continue
        if exclude_machine and a.get("machine") == exclude_machine:
            continue
        if verified_only and not a.get("verified"):
            continue
        out.append(a)
    return out


def shared_repos() -> list[str]:
    """共享仓白名单（SSOT）—— 只有这几个仓 push 完才需通知对面拉。"""
    return load_registry().get("shared_repos", [])


def is_shared(repo: str) -> bool:
    return repo in shared_repos()


def send_key_for(name: str) -> str | None:
    """友好名(name/at_name/open_id) → send_key（.env slug）。send_feishu_msg 方案B用：
    让 --to-agent 只用【显示名】就喊到 tb24-*（它 .env slug 是旧 xhs 名·≠显示名）。查不到回 None。"""
    a = find(name)
    return (a or {}).get("send_key")


# ---------- 外部通道：群名（committed groups 段）+ 人名（本地缓存）· ARCH-140 §7 ----------

def groups() -> list[dict]:
    return load_registry().get("groups", [])


def group_name(chat_id: str) -> str | None:
    """chat_id → 群名（`via=<群名>` 的源头·来自 groups 段·API 拉的）。查不到回 None。"""
    for g in groups():
        if g.get("chat_id") == chat_id:
            return g.get("name")
    return None


PEOPLE_CACHE = Path(__file__).resolve().parent / "_state" / "people-cache.local.json"


def _load_people() -> dict:
    if PEOPLE_CACHE.exists():
        try:
            return json.loads(PEOPLE_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def person_name(open_id: str) -> str | None:
    """外部真人 open_id → 名字（`from=<人名>` 的源头·本地缓存·桥从群成员 API 查来的）。查不到回 None。"""
    return _load_people().get(open_id)


def cache_person(open_id: str, name: str) -> None:
    """桥从群成员 API 查到外部人名字后回填本地缓存（API 源头·绝非硬编码·ARCH-140 §7.1）。"""
    if not open_id or not name:
        return
    d = _load_people()
    if d.get(open_id) == name:
        return
    d[open_id] = name
    PEOPLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PEOPLE_CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_groups(bot: str) -> list[dict]:
    """从飞书群列表 API 拉某 bot 所在群 → 更新 registry 的 groups 段（群名=API 源头·非手写）。
    保原文件格式（只替换 groups 数组·不整文件 reformat）。新群 external 留 null 待人核。"""
    import send_feishu_msg as S            # 复用 creds + _bot_groups
    creds = S._creds_for(bot)
    if not creds:
        raise SystemExit(f"❌ 找不到 bot '{bot}' 的凭据（.env 里没有）")
    live = S._bot_groups(*creds)           # [{chat_id, name}]
    cur = {g["chat_id"]: dict(g) for g in groups()}
    for g in live:
        cid = g.get("chat_id")
        if not cid:
            continue
        if cid in cur:
            cur[cid]["name"] = g.get("name") or cur[cid].get("name")
        else:
            cur[cid] = {"chat_id": cid, "name": g.get("name"), "external": None,
                        "note": f"sync-groups({bot}) 发现·请核对 external 真假"}
    new_groups = list(cur.values())
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    block = '"groups": [\n' + ",\n".join("    " + json.dumps(g, ensure_ascii=False) for g in new_groups) + "\n  ]"
    text2, n = re.subn(r'"groups":\s*\[.*?\n  \]', block, text, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("❌ agent-registry.json 里没找到 groups 段（先手加一个空 `\"groups\": []`）")
    REGISTRY_PATH.write_text(text2, encoding="utf-8")
    return new_groups


# ---------- CLI 渲染 ----------

def _fmt_rows(agents: list[dict]) -> str:
    if not agents:
        return "（无匹配）"
    headers = ["名字", "发送键", "机", "仓", "shared", "✓", "open_id", "备注"]
    rows = []
    for a in agents:
        rows.append([
            a.get("name", ""),
            a.get("send_key") or "—",
            a.get("machine", ""),
            a.get("repo") or "—",
            "★" if a.get("shared") else "",
            "✓" if a.get("verified") else "…",
            a.get("open_id", ""),
            a.get("role", ""),
        ])
    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]
    line = lambda cells: "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))
    out = [line(headers), line(["-" * w for w in widths])]
    out += [line(r) for r in rows]
    return "\n".join(out)


def _emit(obj, as_json: bool, human_render):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(human_render(obj))


def main() -> int:
    p = argparse.ArgumentParser(description="查名册 —— 跨机 agent 目录")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("list", help="列表（可过滤）")
    pl.add_argument("--machine")
    pl.add_argument("--repo")
    pl.add_argument("--shared", action="store_true")
    pl.add_argument("--unverified", action="store_true", help="只看待核对(verified:false)")

    pw = sub.add_parser("whois", help="按名字/open_id/@名 查一条")
    pw.add_argument("query")

    pp = sub.add_parser("peers", help="某仓在别的机上谁管（路由用）")
    pp.add_argument("repo")
    pp.add_argument("--exclude-machine", help="排除本机（如 tb25）→ 只剩对面")
    pp.add_argument("--verified-only", action="store_true")

    pr = sub.add_parser("resolve", help="open_id -> 名字（只打一个词）")
    pr.add_argument("open_id")

    sub.add_parser("shared-repos", help="列共享仓（push 判据·只这几个才通知对面拉）")
    pis = sub.add_parser("is-shared", help="某仓是否共享仓（exit 0=是/1=否·脚本用）")
    pis.add_argument("repo")

    sub.add_parser("groups", help="列所有群（chat_id/群名/external·ARCH-140 §7）")
    pgn = sub.add_parser("group-name", help="chat_id → 群名（via=<群名> 源头）")
    pgn.add_argument("chat_id")
    prp = sub.add_parser("resolve-person", help="外部人 open_id → 名字（from=<人名>·本地缓存）")
    prp.add_argument("open_id")
    psg = sub.add_parser("sync-groups", help="从飞书群列表 API 拉某 bot 所在群 → 更新 groups 段（群名 API 源头）")
    psg.add_argument("--bot", required=True)

    args = p.parse_args()

    if args.cmd == "whois":
        a = find(args.query)
        if not a:
            print(f"查无此 agent：{args.query}", file=sys.stderr)
            return 1
        _emit(a, args.json, lambda x: _fmt_rows([x]))
        return 0

    if args.cmd == "peers":
        ps = peers_for_repo(args.repo, exclude_machine=args.exclude_machine,
                            verified_only=args.verified_only)
        _emit(ps, args.json, _fmt_rows)
        return 0

    if args.cmd == "resolve":
        name = name_for_open_id(args.open_id)
        if not name:
            print(args.open_id)   # 查不到就原样回 open_id（调用方自行兜底）
            return 1
        print(name)
        return 0

    if args.cmd == "shared-repos":
        repos = shared_repos()
        _emit(repos, args.json, lambda x: "\n".join(x))
        return 0

    if args.cmd == "is-shared":
        ok = is_shared(args.repo)
        print("yes" if ok else "no")
        return 0 if ok else 1

    if args.cmd == "groups":
        gs = groups()
        if args.json:
            print(json.dumps(gs, ensure_ascii=False, indent=2))
        else:
            for g in gs:
                print(f"{g.get('chat_id')}  {'★外部' if g.get('external') else '内部 '}  {g.get('name')}")
        return 0

    if args.cmd == "group-name":
        n = group_name(args.chat_id)
        print(n or args.chat_id)          # 查不到原样回 chat_id
        return 0 if n else 1

    if args.cmd == "resolve-person":
        n = person_name(args.open_id)
        print(n or args.open_id)          # 查不到原样回 open_id
        return 0 if n else 1

    if args.cmd == "sync-groups":
        gs = sync_groups(args.bot)
        print(f"✅ groups 段已更新（{len(gs)} 个群·名字从飞书 API 拉）：")
        for g in gs:
            print(f"  {g.get('chat_id')}  {g.get('name')}  external={g.get('external')}")
        return 0

    # 默认 / list
    agents = load_agents()
    if args.cmd == "list":
        if args.machine:
            agents = [a for a in agents if a.get("machine") == args.machine]
        if args.repo:
            agents = [a for a in agents if a.get("repo") == args.repo]
        if args.shared:
            agents = [a for a in agents if a.get("shared")]
        if args.unverified:
            agents = [a for a in agents if not a.get("verified")]
    _emit(agents, args.json, _fmt_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
