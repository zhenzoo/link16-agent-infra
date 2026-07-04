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
