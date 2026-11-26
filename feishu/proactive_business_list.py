"""Review business assignments and bind owner-approved source messages to outgoing work."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import registry
from bot_names import labels, normalize
from bridge_env import bots_config_path, registry_path, EXAMPLE_REGISTRY_NAME
from bridge_injection import atomic_write_json, injection_lock


HERE = Path(__file__).resolve().parent
SLUG = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def tenant_for(bot, fleet):
    local = load(bots_config_path(HERE.parent))["bots"]
    if not any(row["name"] == bot for row in local) or not SLUG.fullmatch(bot):
        raise ValueError("请指定本机登记的主号")
    matches = [row for row in fleet["agents"] if normalize(bot) in labels(row)]
    if len(matches) != 1:
        raise ValueError("主号未唯一绑定舰队登记")
    return next(t for t in fleet["tenants"] if t["tenant_key"] == matches[0]["tenant_key"])


def peer_cache(bot):
    return HERE / "_state" / "proactive" / bot / "business-members.json"


def read_members(bot):
    tenant = tenant_for(bot, registry.load_registry())
    group = tenant["group_chat_id"]
    args = [shutil.which("lark-cli") or "lark-cli", "--profile", bot, "im", "+chat-members-list",
            "--as", "bot", "--member-types", "bot", "--chat-id", group, "--page-all"]
    response = json.loads(subprocess.run(args, check=True, capture_output=True).stdout)
    data = response["data"]
    if not response.get("ok") or data["chat_id"] != group:
        raise ValueError("原租户群未回读，已有登记未覆盖")
    members = [{k: row[k] for k in ("name", "app_id", "member_id", "tenant_key")}
               for row in data["bots"] if row.get("tenant_key") == tenant["tenant_key"] and
               row.get("member_id") and row.get("app_id") and row.get("name")]
    if not members:
        raise ValueError("原租户群缺少已核身份的机器人")
    return {"bot": bot, "chat_id": group, "members": members}


def refresh_members(bot):
    directory = read_members(bot)
    atomic_write_json(peer_cache(bot), directory)
    return len(directory["members"])


def verified_member_address(bot, peer, group=None):
    """Use the sender's registered group and its own live membership view."""
    if not isinstance(bot, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", bot):
        raise ValueError("发送方代号无效")
    path = peer_cache(bot)
    if not path.is_file():
        return None
    cached = load(path)
    if cached.get("bot") != bot or (group and group != cached.get("chat_id")):
        return None
    def matching(directory):
        return {(row["app_id"], row["member_id"]) for row in directory.get("members", [])
                if row.get("name", "").casefold() == peer.casefold()
                and re.fullmatch(r"cli_[A-Za-z0-9]+", row.get("app_id") or "")
                and re.fullmatch(r"ou_[A-Za-z0-9]+", row.get("member_id") or "")}
    before = matching(cached)
    if not before:
        return None
    if len(before) != 1:
        raise ValueError("同群有多个同名应用，请先核对收件人")
    current = read_members(bot)
    if (current["chat_id"] != cached["chat_id"] or current["bot"] != bot
            or matching(current) != before):
        raise ValueError("群或收件应用的登记已变化，请重新核实派单对象")
    return current["chat_id"], next(iter(before))[1]


def roster_names(local, federated, members):
    names = {r["name"] for r in local}
    mappings = {}
    def register(row, canonical):
        for label in labels(row):
            mappings.setdefault(label, set()).add(canonical)
    for row in local:
        register(row, row["name"])
    for row in federated:
        matched = set().union(*(mappings.get(label,set()) for label in labels(row)))
        if len(matched) > 1:
            continue
        canonical = next(iter(matched), normalize(row["name"]))
        names.add(canonical)
        register(row,canonical)
    members_by_label = {}
    for row in members:
        if not (row.get("member_id") and row.get("app_id")):
            continue
        label = normalize(row["name"])
        members_by_label.setdefault(label,set()).add(row["app_id"])
    for label, app_ids in members_by_label.items():
        if len(app_ids) == 1 and label not in mappings:
            names.add(label)
    return {n for n in names if SLUG.fullmatch(n) and len(mappings.get(n,())) <= 1
            and len(members_by_label.get(n,())) <= 1}


def registered_names(bot):
    if registry_path().name == EXAMPLE_REGISTRY_NAME:
        raise ValueError("示例名册不能用于真实业务登记")
    fleet = registry.load_registry()
    tenant = tenant_for(bot, fleet)
    peers = []
    cache = peer_cache(bot)
    if cache.is_file():
        data = load(cache)
        if data["bot"] != bot or data["chat_id"] != tenant["group_chat_id"]:
            raise ValueError("跨机成员回执与本机登记的主号或租户群不符")
        peers = [row for row in data["members"] if row["tenant_key"] == tenant["tenant_key"]]
    return roster_names(load(bots_config_path(HERE.parent))["bots"], fleet["agents"], peers)


def field(value, label):
    if (not isinstance(value,str) or not value.strip() or value != value.strip() or
            any(c in value for c in "｜|") or len(value.splitlines()) != 1 or
            any(not c.isprintable() for c in value)):
        raise ValueError(f"{label}必须为一行非空原文，不能含分栏符")
    return value


def original_author(bot, key, item, ownership, followup, verified_links, addresses):
    assignments = ownership["tasks"]
    link = verified_links.get(key)
    task = key
    if link:
        task = link["task"]
        if not link.get("request_mid") or followup.get(task,{}).get("request_mid") != link["request_mid"]:
            raise ValueError("当前续单与已核归属的原始派单回执不符")
    if item.get("author_task",task) != task:
        raise ValueError("当前续单未在独立归属记录中核实")
    bound = assignments.get(f"{bot}/{task}")
    if bound:
        return ownership["items"][bound]["owner"]
    previous = followup.get(task,{})
    if previous.get("peer"):
        peer = previous["peer"]
        if peer.startswith("ou_"):
            if peer not in addresses:
                raise ValueError("原收件地址缺少同一应用或同租户登记的机器人身份，请先核成员")
            return addresses[peer]
        return addresses.get(normalize(peer),peer)
    if previous.get("native_log"):
        return bot
    return None


def dispatch_ledgers(bot):
    from proactive_ownership import ItemOwners
    from proactive_followup import Followup
    cache = peer_cache(bot)
    links = cache.with_name("business-author-links.json")
    fleet = registry.load_registry()
    tenant = tenant_for(bot,fleet)
    local = load(bots_config_path(HERE.parent))["bots"]
    directory = load(cache) if cache.is_file() else {}
    members = directory.get("members",[]) if directory.get("chat_id") == tenant["group_chat_id"] and directory.get("bot")==bot else []
    addresses = {}
    for r in [*fleet["agents"], *members]:
        if r.get("tenant_key") != tenant["tenant_key"]:
            continue
        local_match = next((e for e in local if labels(e) & labels(r)),None)
        stable = local_match["name"] if local_match else normalize(r["name"])
        for address in (*labels(r), r.get("member_id"),r.get("open_id")):
            if address:
                addresses.setdefault(address,set()).add(stable)
    return (ItemOwners().read(), Followup(bot).read()["tasks"], load(links) if links.is_file() else {},
            {k:next(iter(vals)) for k,vals in addresses.items() if len(vals)==1})


def assignments(plan, names=None, ledgers=None):
    if plan.get("mode") != "arrange_business":
        raise ValueError("仅在梳理或排业务时启用 arrange_business")
    if names is None:
        names = registered_names(plan["dispatch_bot"])
    if ledgers is None:
        ledgers = dispatch_ledgers(plan["dispatch_bot"])
    from proactive_business_contract import ordered_assignments
    order, owners = ordered_assignments(plan)
    items = {r["id"]: r for r in [*plan["previous"], *plan["items"]]}
    result = []
    used = set()
    for key in order:
        row = items[key]
        name = field(row.get("name"),"业务项")
        agent = field(owners[key], "登记智能体名称")
        expected = field(row.get("expected") or plan.get("expected",{}).get(key), "具体交付物")
        if agent not in names:
            raise ValueError(f"{name}的作者 {agent} 不在实际名册；核原单并修正后重试")
        author = original_author(plan["dispatch_bot"],key,row,*ledgers)
        if author and author != agent:
            raise ValueError(f"{name}的实际原单作者为 {author}，与登记的 {agent} 不符")
        if name in used or expected in {"待定","待确认","交付物","结果","待补","跟进","处理","稍后再定"}:
            raise ValueError(f"{name}重复或缺少具体交付物")
        used.add(name)
        result.append((key,name,agent,expected))
    if not result:
        raise ValueError("本轮没有可梳理的业务")
    return result


def bullets(assigned):
    return ["- " + "｜".join((name,agent,expected)) for _,name,agent,expected in assigned]


def check(text, assigned):
    lines = text.splitlines()
    expected = bullets(assigned)
    if len(lines) != len(expected):
        raise ValueError(f"清单应有 {len(expected)} 个单行业务项，实际 {len(lines)} 行")
    for index,(line,wanted) in enumerate(zip(lines,expected),1):
        if line != wanted:
            raise ValueError(f"第 {index} 行与原业务、登记作者、具体交付或格式不符。应为 {wanted}")


def dispatch_parts(plan, bot):
    """Rebuild the entire owner message before releasing any part of a batch."""
    import bridge_inbound
    from feishu_bridge import STATE_DIR, load_owner_record
    if bot != plan.get("dispatch_bot"):
        raise ValueError("本轮业务登记属于另一主号")
    source_mid = plan.get("source_mid")
    if not isinstance(source_mid, str) or not re.fullmatch(r"om_[A-Za-z0-9_-]+", source_mid):
        raise ValueError("分派缺少本人原消息编号")
    owner = load_owner_record(bot)
    if not owner or not owner.get("open_id") or owner.get("provisional"):
        raise ValueError("主号尚未核实本人身份")
    record = next((r for r in reversed(bridge_inbound.read_records(STATE_DIR, bot, strict=True))
                   if r.get("message_id") == source_mid and r.get("source") == bridge_inbound.NATIVE_SOURCE
                   and r.get("bot") == bot and r.get("chat_type") in {"p2p", "private"}
                   and (r.get("sender") or {}).get("open_id") == owner["open_id"]
                   and r["sender"].get("is_bot") is False), None)
    if not record or not record.get("raw_text"):
        raise ValueError("未找到主号收到的本人原文")
    incoming = {r["id"] for r in plan["items"]}
    owners = {key: agent for key, _, agent, _ in assignments(plan) if key in incoming}
    quotes = {key: [] for key in owners}
    segments = plan.get("source_parts")
    if not isinstance(segments, list) or not segments:
        raise ValueError("缺少原文分段与事项对应表")
    original = []
    for section in segments:
        if not isinstance(section, dict):
            raise ValueError("每段须提供原文和实际事项")
        body, items = section.get("text"), section.get("items")
        if (not isinstance(body, str) or not body.strip() or not isinstance(items, list) or not items
                or any(not isinstance(key, str) or key not in quotes for key in items)
                or len(set(items)) != len(items)):
            raise ValueError("每段原文须指定本批的实际事项")
        body = body.replace("\r\n", "\n")
        original.append(body)
        for key in items:
            quotes[key].append(body)
    if "".join(original) != record["raw_text"].replace("\r\n", "\n") or not all(quotes.values()):
        raise ValueError("分段未按原顺序覆盖完整来信，或事项没有原文")
    return {key: (owners[key], parts) for key, parts in quotes.items()}


def check_recipient(jobs, item, peer, text):
    if item not in jobs or normalize(jobs[item][0]) != normalize(peer):
        raise ValueError("收件人与该段原文的登记执行者不符")
    body = text.replace("\r\n", "\n")
    remaining = body
    for part in jobs[item][1]:
        _, found, remaining = remaining.partition(part)
        if not found:
            raise ValueError("派单正文未按序保留分给此执行者的原文")
    shared = {part for owner, parts in jobs.values() if normalize(owner) == normalize(peer) for part in parts}
    for _, parts in jobs.values():
        for part in parts:
            if part in body and not any(part in allowed for allowed in shared):
                raise ValueError("派单包含单独分给其他执行者的原文")


def review_outgoing(path, bot, item, peer, text):
    from bridge_history import STATE_DIR
    plan = load(path)
    index = round_index(STATE_DIR, bot)
    binding = load(index).get(item) if index.is_file() else None
    if binding and binding["mid"] == plan.get("source_mid") and binding.get("digest") != round_digest(plan):
        raise ValueError("登记后的分段或清单已修改，请重新核对并登记批次")
    jobs = dispatch_parts(plan, bot)
    check_recipient(jobs, item, peer, text)
    return plan["source_mid"]


def round_index(state, bot):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bot):
        raise ValueError("主号代号无效")
    return Path(state) / "proactive" / bot / "dispatch-rounds.json"


def round_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def register_round(state, path, plan, jobs):
    from bridge_env import assert_sender_identity
    bot = plan["dispatch_bot"]
    assert_sender_identity(bot)
    index = round_index(state, bot)
    digest = round_digest(plan)
    with injection_lock(state, "dispatch-rounds", bot):
        active = load(index) if index.is_file() else {}
        for key in jobs:
            active[key] = {"path": str(path.resolve()), "mid": plan["source_mid"], "digest": digest}
        atomic_write_json(index, active)


def pending_round(state, bot, item):
    import bridge_outbound
    index = round_index(state, bot)
    entry = load(index).get(item) if index.is_file() else None
    if not entry:
        return None
    if any(r["route"].get("source_mid") == entry["mid"] and r["route"].get("item") == item
           for r in bridge_outbound.read_records(state, bot)):
        return None
    path = Path(entry["path"])
    if load(path).get("source_mid") != entry["mid"]:
        raise ValueError("登记的批次文件已更换，请用原消息重新核对本轮业务")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    for action in ("arrange","check","audit-sends"):
        sub = subs.add_parser(action)
        sub.add_argument("--round-json", type=Path, required=True)
        if action == "check":
            sub.add_argument("--bullets", type=Path, required=True)
    subs.add_parser("refresh-members", help="回读本机主号所属租户群的机器人身份").add_argument("--bot",required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "refresh-members":
            print(f"已核租户群机器人 {refresh_members(args.bot)} 名")
            return
        plan = load(args.round_json)
        if args.action == "audit-sends":
            from bridge_history import STATE_DIR
            import bridge_outbound
            bot = plan["dispatch_bot"]
            jobs = dispatch_parts(plan, bot)
            records = bridge_outbound.read_records(STATE_DIR, bot)
            for item, (peer, parts) in jobs.items():
                mids = set()
                for row in records:
                    route = row.get("route") or {}
                    if (route.get("source_mid") == plan["source_mid"] and route.get("item") == item
                            and normalize(route.get("peer") or "") == normalize(peer) and row.get("message_id")):
                        if route.get("kind") != "a2a" or not re.fullmatch(r"om_[A-Za-z0-9_-]+", row["message_id"]):
                            raise ValueError(f"{item} 已有发送记录，仍缺飞书原生消息编号；请核原投递")
                        check_recipient(jobs, item, peer, row["text"])
                        mids.add(row["message_id"])
                if len(mids) != 1:
                    raise ValueError(f"{item} 应有一条已核派单，实际 {len(mids)} 条")
            print(f"本批 {len(jobs)} 项均已向登记作者投递，各有唯一飞书消息编号。")
            return
        batch = "source_mid" in plan or "source_parts" in plan
        jobs = dispatch_parts(plan, plan["dispatch_bot"]) if batch else {}
        rows = assignments(plan)
        if "outcomes" in plan:
            authors = {key: agent for key, _, agent, _ in rows}
            specs = plan["outcomes"]
            if ({s["parent"] for s in specs} != set(authors)
                    or len({s["id"] for s in specs}) != len(specs)):
                raise ValueError("清单中有未覆盖的业务或重复结果行")
            rows = [(s["id"], field(s["name"], "业务项"), authors[s["parent"]],
                     field(s["expected"], "具体交付物")) for s in specs]
        if args.action == "check":
            check(args.bullets.read_text("utf-8-sig"),rows)
        elif batch:
            from bridge_history import STATE_DIR
            register_round(STATE_DIR, args.round_json, plan, jobs)
        print("\n".join(bullets(rows)))
    except (ValueError, KeyError, StopIteration, TypeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1,f"业务清单未通过：{exc}\n")


if __name__ == "__main__":
    main()
