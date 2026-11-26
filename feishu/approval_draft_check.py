"""Validate two private observations of an approval draft. No Feishu write operations."""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


class CheckError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise CheckError(message)


def strings(values):
    require(isinstance(values, list) and all(isinstance(v, str) and v.strip() for v in values),
            "缺少有效的标识列表")
    return values


def indexed(items, key):
    require(isinstance(items, list), "字段定义或明细清单缺失")
    result = {}
    for item in items:
        name = item[key]
        require(isinstance(name, str) and name.strip() and name not in result, "缺少唯一字段或行标识")
        result[name] = item
    return result


def amount(value):
    require(isinstance(value, (str, int)) and not isinstance(value, bool), "金额需为精确十进制文本或整数")
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise CheckError("金额无法解析") from None
    require(n.is_finite(), "金额未读到有限数值")
    return n


def applies(condition, selection):
    """Feishu display_condition groups contain expressions and/or nested groups."""
    if condition is None or condition == {}:
        return True
    require(isinstance(condition, dict), "条件定义无效")
    op = condition["conditional"]
    require(op in {"AND", "OR"}, "遇到未支持的条件组合")
    results = []
    for expr in condition.get("expressions") or []:
        field = expr["source_widget"]["id"]
        require(expr["compare_type"] == "is" and isinstance(expr["standard_value"], str),
                "遇到未支持的条件比较，需要核实当前定义")
        results.append(selection(field) == expr["standard_value"])
    for nested in condition.get("conditions") or []:
        results.append(applies(nested, selection))
    require(results, "条件组为空")
    return all(results) if op == "AND" else any(results)


def check_fields(widgets, ui, outer_selection=None):
    """Use native stars for requiredness. Use schema hints to detect missing observations."""
    present, choices = ui.get("fields"), ui.get("choices")
    require(isinstance(ui.get("stars"), list), "当前页面或明细行的红星未读")
    stars = set(strings(ui["stars"]))
    hidden = set(strings(ui.get("hidden", [])))
    require(isinstance(present, dict) and isinstance(choices, dict) and
            all(type(v) is bool for v in present.values()), "控件读回必须是脱敏的有值/空值判断")
    require(present.keys() <= widgets.keys() and hidden <= widgets.keys() and
            hidden.isdisjoint(present) and choices.keys() <= present.keys(), "页面字段、隐藏项或选项映射有误")
    require(stars <= present.keys(), "红星控件的值未读或尚未映射")
    require(present or hidden, "当前页面或明细行没有控件读数")
    for k, choice in choices.items():
        require(isinstance(choice, str) and bool(choice.strip()) == present[k], "选择器读数与字段状态不符")

    def check_unobserved(k, path):
        if k in hidden:
            return
        require(k not in path, "未读控件的定位条件存在循环，需要核页面")
        wd = widgets[k]
        require(type(wd.get("visible")) is bool, "控件未读且缺少定位信息")
        require(not (wd["visible"] and applies(wd.get("display_condition"),
                                               lambda source: selected(source, path | {k}))),
                "当前分支的控件未读，请核页面及星标")

    def selected(k, path=frozenset()):
        if k not in widgets:
            require(outer_selection is not None, "条件引用了未知控件")
            return outer_selection(k)
        if k not in present:
            check_unobserved(k, path)
            return None
        require(k in choices, "条件驱动项未经读取，无法判定分支")
        return choices[k]

    for pos, k in enumerate(widgets, 1):
        if k in stars:
            require(present[k], f"第 {pos} 个控件有红星、必填，实读为空")
        elif k not in present:
            check_unobserved(k, frozenset())
    return selected, stars


def check_snapshot(expected, ui, schema):
    require(isinstance(ui, dict) and ui, "当前编辑页未读")
    for k in ("draft", "owner", "definition"):
        require(isinstance(expected[k], str) and expected[k].strip() and ui[k] == expected[k],
                "草稿、申请人或表单定义不一致")
    selected, required = check_fields(schema, ui)
    historical = set(strings(expected["historical"]))
    matches = ui["historical"]
    filled_historical = {k for k in historical if ui["fields"].get(k)}
    require(historical <= schema.keys() and isinstance(matches, dict) and matches.keys() <= historical
            and all(type(v) is bool for v in matches.values())
            and {k for k, matched in matches.items() if matched} == filled_historical,
            "本人历史字段未全部匹配")
    require(Counter(strings(ui["attachments"])) == Counter(strings(expected["attachments"])),
            "附件与原始材料清单不符")
    tables = expected["tables"]
    require(isinstance(tables, dict) and ui["tables"].keys() == tables.keys(), "明细表清单不符")
    active_tables = {k for k in ui["fields"] if schema[k].get("children")}
    require(active_tables == tables.keys(), "当前条件下应核验的明细表缺失或多出")
    required_count = len(required)
    line_count = 0
    line_amounts = []
    for table_id, expected_rows in tables.items():
        columns = indexed(schema[table_id]["children"], "id")
        rows = indexed(ui["tables"][table_id], "key")
        sources = indexed(expected_rows, "key")
        require(rows.keys() == sources.keys() and (rows or table_id not in required), "明细缺行、重复或多行")
        for row_number, (key, src) in enumerate(sources.items(), 1):
            row = rows[key]
            try:
                _, row_required = check_fields(columns, row, selected)
            except CheckError as ex:
                raise CheckError(f"明细第 {row_number} 行：{ex}") from None
            required_count += len(row_required)
            line_count += 1
            require(Counter(strings(row["links"])) == Counter(strings(src["links"])), "明细关联了不同的原单")
            for attr in ("purpose", "category"):
                if attr in src:
                    require(attr in row and row[attr] == src[attr], "明细用途或分类未读或不符")
            if "amount" in src:
                n = amount(row["amount"])
                require(n == amount(src["amount"]), "明细金额与凭证不符")
                line_amounts.append(n)
    if "total" in expected:
        n = amount(expected["total"])
        require(amount(ui["total"]) == n and sum(line_amounts, Decimal(0)) == n,
                "表单总额、凭证明细加总不一致")
    return required_count, line_count


def check(report):
    exp, before, after = report["expected"], report.get("before"), report.get("reopened")
    require(isinstance(before, dict) and before, "保存前编辑页未读")
    require(isinstance(after, dict) and after, "重开后编辑页未读")
    schema = indexed(exp["schema"], "id")
    require(schema, "缺少原表单定义")
    events = report["events"]
    require(isinstance(events, list) and [x.get("action") for x in events] ==
            ["save_draft", "leave", "reopen"], "只存草稿的操作边界或退出重开证据不符")
    require(events[0]["draft"] == exp["draft"] and events[1]["draft"] != exp["draft"] and
            events[2]["draft"] == exp["draft"] and events[0]["persisted"] is True,
            "缺少原生保存回执、离开原页或同一草稿重开的证据")
    count = report["new_submissions"]
    require(type(count) is int and count == 0, "提交状态未核实或出现新提交")
    t1, t2 = (datetime.fromisoformat(stage["at"]) for stage in (before, after))
    require(t1.utcoffset() is not None and t2.utcoffset() is not None and t2 > t1,
            "保存前与重开后的读取时间不成立")
    require(isinstance(before["stable"], dict) and isinstance(after["stable"], dict),
            "缺少需跨保存比较的附加字段映射")
    try:
        pre = check_snapshot(exp, before, schema)
    except CheckError as ex:
        raise CheckError(f"保存前：{ex}") from None
    try:
        post = check_snapshot(exp, after, schema)
    except CheckError as ex:
        raise CheckError(f"重开后：{ex}") from None
    for k in ("choices", "fields", "stars", "stable"):
        a, b = before[k], after[k]
        require((set(a) == set(b) if k == "stars" else a == b), "保存前后字段、条件或比较结果变化")
    for k in before["tables"]:
        pre_rows = indexed(before["tables"][k], "key")
        post_rows = indexed(after["tables"][k], "key")
        for row_id, original in pre_rows.items():
            fresh = post_rows[row_id]
            require(original["fields"] == fresh["fields"] and original["choices"] == fresh["choices"] and
                    set(original["stars"]) == set(fresh["stars"]), "重开后明细字段或条件变化")
    require(pre == post, "保存前后验收范围不同")
    return {"ok": True, "required_controls": post[0], "rows": post[1],
            "attachments": len(exp["attachments"])}


def definition_projection(widgets):
    """Discard option labels, defaults and values; compare the rules used by the checker."""
    fields = []
    for w in widgets:
        fields.append({
            "id": w["id"], "required": w["required"], "visible": w["visible"],
            "display_condition": w.get("display_condition"),
            "children": definition_projection(w.get("children") or []),
        })
    return fields


def check_delivery(report):
    """Check an observed workbench click against an independently identified business object."""
    require(isinstance(report.get("delivery"), dict), "缺少从工作台点击到原生业务页的独立证据")
    proof = report["delivery"]
    target, trip, landing = proof["expected"], proof["journey"], proof["landing"]
    intent = target["intent"]
    require(intent in {"draft_review", "handle_instance", "view_instance"}, "审批交付目的不明")
    for k in ("object_id", "viewer", "business", "platform", "href"):
        require(isinstance(target[k], str) and bool(target[k].strip()), "目标、身份、业务或入口缺失")
    url = urlsplit(target["href"])
    require(url.scheme in {"https", "lark"} and url.hostname and not url.username and not url.password
            and any(url.hostname == h or url.hostname.endswith("." + h)
                    for h in ("feishu.cn", "larksuite.com")), "链接未经官方入口核验")
    require(trip["origin"] == "workbench" and trip["href"] == target["href"] and
            trip["platform"] == target["platform"] and isinstance(trip["row"], str) and trip["row"].strip() and
            trip["previous_object_id"] != target["object_id"], "缺少从工作台独立打开目标行的证据")
    # A platform handoff can require consent without requiring a second business selection.
    steps = strings(trip["steps"])
    require(steps[:1] == ["row_click"] and set(steps[1:]) <= {"auto_redirect", "client_prompt", "open_client"},
            "工作台点击后仍需业务选单或手动跳转")
    prompts = sum(step in {"client_prompt", "open_client"} for step in steps)
    start = datetime.fromisoformat(trip["clicked_at"])
    end = datetime.fromisoformat(landing["observed_at"])
    require(start.utcoffset() is not None and end.utcoffset() is not None and start <= end,
            "点击和页面读回时间无效")
    require(landing["native"] is True and landing["rendered"] is True and
            all(landing[k] == target[k] for k in ("object_id", "viewer", "business")),
            "原生业务页面、实际单据、身份或业务不匹配")
    require(isinstance(trip["writes"], list) and not trip["writes"], "点验期间存在业务写入或决定")
    if intent == "draft_review":
        exp = report["expected"]
        require(landing["page"] == "draft_editor" and
                (exp["draft"], exp["owner"]) == (target["object_id"], target["viewer"]),
                "落点没有绑定原草稿与当前本人")
        ui = landing["snapshot"]
        require(datetime.fromisoformat(ui["at"]) == end, "缺少本次点击落点的独立读数")
        count, rows = check_snapshot(exp, ui, indexed(exp["schema"], "id"))
        require(landing["node"] == "draft" and target["action"] == "提交", "草稿操作不符")
        detail = {"required_controls": count, "rows": rows, "attachments": len(ui["attachments"])}
    else:
        require(landing["page"] == "instance_detail" and isinstance(target["serial"], str) and
                bool(target["serial"].strip()) and landing["serial"] == target["serial"] and
                bool(target["status"]) and landing["status"] == target["status"],
                "已发起实例的原单编号、状态或详情不符")
        if intent == "handle_instance":
            require(isinstance(target["node"], str) and bool(target["node"].strip()) and
                    landing["node"] == target["node"] and landing["assignee"] == target["viewer"]
                    and bool(target["task_id"]) and landing["task_id"] == target["task_id"],
                    "当前办理人、待办或节点不符")
        detail = {"instance_matched": True}
    if intent != "view_instance":
        action = landing["action"]
        require(isinstance(target["action"], str) and bool(target["action"].strip()) and
                action["label"] == target["action"] and action["visible"] is True and
                action["enabled"] is True and action["invoked"] is False, "本人下一步按钮未就绪或已被操作")
    return {"ok": True, "delivery_verified": True, "business_direct": True, "business_clicks": 1,
            "client_confirmations": prompts, "human_clicks": 1 + prompts,
            "prompt_free_one_click": prompts == 0, "intent": intent, **detail}


def verify_profile(profile, expected):
    from bridge_scope_audit import user_identity_status
    from proactive_sources import user_read

    identity = user_identity_status(profile)
    require(identity.get("verified_owner_id") == expected["owner"] and identity.get("owner_matches") is True,
            "本应用已验证的本人身份与证据不同")
    try:
        result = user_read(profile, ["approval", "approvals", "get", "--approval-code", expected["definition"]])
        raw_form = result["form"]
        live = json.loads(raw_form) if isinstance(raw_form, str) else raw_form
        same = definition_projection(live) == definition_projection(expected["schema"])
    except Exception:
        # CLI errors and source definitions can contain personal or tenant data.
        raise CheckError("本人接口未能回读可比对的当前表单定义") from None
    require(same, "当前原表定义与验收时的字段或条件不一致")


def verify_instance_profile(profile, target):
    from bridge_scope_audit import user_identity_status
    from proactive_sources import user_read

    identity = user_identity_status(profile)
    require(identity.get("owner_matches") is True and identity.get("verified_owner_id") == target["viewer"],
            "已登记本人和实例查看人不一致")
    try:
        data = user_read(profile, ["approval", "instances", "get", "--instance-code", target["api_instance"],
                                   "--user-id-type", "open_id"])
    except Exception:
        raise CheckError("本人接口无法核对当前实例") from None
    require(data["instance_code"] == target["api_instance"] and data["serial_number"] == target["serial"]
            and data["status"] == target["status"], "原实例编号或当前状态不符")
    if target["intent"] == "handle_instance":
        tasks = data.get("tasks", data.get("task_list", []))
        require(any(t.get("status") == "PENDING" and t.get("id") == target["task_id"] and
                    t.get("node_name") == target["node"] and target["viewer"] in (t.get("open_id"), t.get("user_id"))
                    for t in tasks), "当前本人已无该节点待办")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="私有目录中的脱敏证据 JSON，格式见 SOP-151")
    parser.add_argument("--profile", help="用指定登记身份只读核对当前本人和原表定义")
    parser.add_argument("--delivery", action="store_true", help="严格核工作台一次点击后实际到达的原生业务页面")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
        if args.delivery:
            result = check_delivery(report)
        else:
            result = {**check(report), "scope": "saved_draft_content", "delivery_verified": False}
        if args.profile:
            if args.delivery and result["intent"] != "draft_review":
                verify_instance_profile(args.profile, report["delivery"]["expected"])
                result["live_instance_matches"] = True
            else:
                verify_profile(args.profile, report["expected"])
                result["live_definition_matches"] = True
    except CheckError as ex:
        print(f"未通过：{ex}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        print("未通过：证据无法读取或结构不完整", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
