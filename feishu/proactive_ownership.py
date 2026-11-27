"""One product author per physical item or business outcome in the private dispatch registry."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import time

import bridge_history
import bridge_injection

PRODUCT_STYLE = ("本单涉及产品模型、HTML 或 PRD 时，先打开最近经本人确认的原模型和原文档，沿用其版本、章节、"
                 "列序、行序和样式，只改点名的内容。规格行直接写功能、定义、数值和单位；新增一个功能增一行，"
                 "相应视角的 GIF 或截图放回原功能格。保留原图顺序，替换的逐张对应。采购分工和推算过程留在工作记录。")


def product_brief(text, artifacts=()):
    if PRODUCT_STYLE in text:
        return text
    references_product = re.search(r"(?:(?<![A-Za-z0-9])PRD(?![A-Za-z])|\.html?\b|\.fcstd\b|FreeCAD|STEP\s*\d|"
                                   r"规格表|产品规格|原模型|改模型|产品模型|造型|装配|功能表|同版文档|产品文档|"
                                   r"原文档|(?:改|修|更新).{0,4}(?:文档|示意图))",
                                   " ".join([text, *map(str, artifacts)]), re.I)
    return text + "\n\n" + PRODUCT_STYLE if references_product else text


def key(value):
    if not isinstance(value, str) or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{0,119}", value):
        raise ValueError("需使用稳定的物品、执行者及任务标识")
    return value.lower() if not value.startswith("ou_") else value


class ItemOwners:
    def __init__(self, state=None):
        self.root = (state or bridge_history.STATE_DIR) / "proactive" / "ownership"
        self.path = self.root / "items.json"

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"items": {}, "tasks": {}}

    def lock(self, name):
        self.root.mkdir(parents=True, exist_ok=True)
        return bridge_injection.ProcessFileLock(self.root/(name+".lock"), timeout=15)

    def manages(self, sender):
        prefix = key(sender) + "/"
        return any(task.startswith(prefix) for task in self.read()["tasks"])

    @contextmanager
    def author(self, sender, item, peer, task, address=None, *, reserve=True):
        item, peer = key(item), key(peer)
        task_key = key(sender) + "/" + key(task)
        address_key = key(sender) + "/" + key(address) if address else None
        with self.lock("item-"+item):
            with self.lock("registry"):
                data = self.read()
                if data["tasks"].get(task_key, item) != item:
                    raise ValueError("原任务已绑定其他物品，请沿原物品交接")
                record = data["items"].get(item) or {"owner":peer, "addresses":[], "history":[]}
                if record.get("stopping"):
                    raise ValueError("原作者停笔交接中，生产派单暂缓")
                if record["owner"] != peer and (not address_key or address_key not in record["addresses"]):
                    raise ValueError(f"{item} 的唯一责任人是 {record['owner']}；先收原作者停笔交接")
                if record["owner"] != peer and not peer.startswith("ou_"):
                    raise ValueError("收件账号与原作者名称不一致")
                if reserve:
                    if address_key and address_key not in record["addresses"]:
                        record["addresses"].append(address_key)
                    record["touched_at"] = time.time()
                    data["items"][item] = record
                    data["tasks"][task_key] = item
                    bridge_injection.atomic_write_json(self.path, data)
            yield record

    def stop(self, item, old_peer, stop_mid):
        item, old_peer = key(item), key(old_peer)
        if not stop_mid.startswith("om_"):
            raise ValueError("需有真实停笔派单 ID")
        with self.lock("item-"+item), self.lock("registry"):
            data = self.read()
            record = data["items"].get(item)
            if not record or record["owner"] != old_peer:
                raise ValueError("停笔单与原作者不符")
            record["stopping"] = stop_mid
            bridge_injection.atomic_write_json(self.path, data)

    def transfer(self, item, old_peer, next_peer, stop, receipt, preview, reporter):
        from proactive_followup import Followup
        item, old_peer, next_peer = key(item), key(old_peer), key(next_peer)
        if not Path(preview).is_file() or not stop or not receipt or old_peer == next_peer:
            raise ValueError("转交需要原作者停笔回执和可读取的交接原件")
        tracker = Followup(key(reporter), self.root.parent.parent)
        with self.lock("item-"+item):
            before = self.read()["items"].get(item)
            if not before or before["owner"] != old_peer or before.get("stopping") != stop:
                raise ValueError("交接前物品归属已变化")
            eligible = [r for r in tracker.read()["tasks"].values()
                        if stop == (r.get("updates") or [r.get("request_mid")])[-1]
                        and (key(r["peer"]) == old_peer or reporter+"/"+key(r["peer"]) in before["addresses"])]
            if not eligible:
                raise ValueError("停笔原单未绑定此物品的原作者")
            evidence = tracker.check(eligible[0]["id"])
            if evidence.get("request_mid") != stop or receipt not in [evidence.get("reply_mid"), evidence.get("handoff_receipt"), *(evidence.get("reply_mids") or [])]:
                raise ValueError("未收到旧作者在停笔原单上的实际回执")
            with self.lock("registry"):
                data = self.read()
                record = data["items"][item]
                record["history"].append(dict(owner=old_peer, stop_mid=stop, receipt=receipt,
                                              handoff=str(Path(preview).resolve()), at=time.time()))
                record.update(owner=next_peer, addresses=[])
                record.pop("stopping", None)
                bridge_injection.atomic_write_json(self.path, data)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    for name in ("claim", "stop", "transfer"):
        action = sub.add_parser(name)
        for field in (("item", "owner", "bot", "task-id") if name=="claim" else
                      ("item", "owner", "stop-mid") if name=="stop" else
                      ("item", "owner", "next-owner", "bot", "stop-mid", "receipt", "handoff")):
            action.add_argument("--"+field, required=True)
    args = cli.parse_args()
    registry = ItemOwners()
    if args.action == "list":
        print(json.dumps(registry.read(),ensure_ascii=False,indent=2))
    elif args.action == "claim":
        with registry.author(args.bot,args.item,args.owner,args.task_id):
            print(args.item, args.owner)
    elif args.action == "stop":
        registry.stop(args.item,args.owner,args.stop_mid)
        print(args.item, "停笔交接中")
    else:
        registry.transfer(args.item,args.owner,args.next_owner,args.stop_mid,args.receipt,args.handoff,args.bot)
        print(args.item, args.next_owner)


if __name__ == "__main__":
    main()
