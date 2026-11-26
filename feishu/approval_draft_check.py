"""Validate two private observations of an approval draft. No Feishu write operations."""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys


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
    """Compute applicability from definition + selected option IDs, separately from stars."""
    present, choices = ui["fields"], ui["choices"]
    stars = set(strings(ui["stars"]))
    require(isinstance(present, dict) and isinstance(choices, dict) and
            all(type(v) is bool for v in present.values()), "控件读回必须是脱敏的有值/空值判断")
    require(set(present) <= widgets.keys() and stars <= widgets.keys() and
            choices.keys() <= present.keys(), "页面出现未映射的字段或选项")
    for k, choice in choices.items():
        require(isinstance(choice, str) and bool(choice.strip()) == present[k], "选择器读数与字段状态不符")
    active = {}
    visiting = set()

    def selected(k):
        if k not in widgets:
            require(outer_selection is not None, "条件引用了未知控件")
            return outer_selection(k)
        if not visible(k):
            return None
        require(k in choices, "条件驱动项未经读取，无法判定分支")
        return choices[k]

    def visible(k):
        if k not in active:
            require(k not in visiting, "字段条件存在循环")
            visiting.add(k)
            wd = widgets[k]
            require(type(wd["visible"]) is bool and type(wd["required"]) is bool, "定义缺少可见性或必填状态")
            active[k] = wd["visible"] and applies(wd.get("display_condition"), selected)
            visiting.remove(k)
        return active[k]

    required = {k for k, wd in widgets.items() if visible(k) and wd["required"]}
    require(all(active[k] for k in present), "页面呈现与定义的条件分支不符")
    for pos, k in enumerate(widgets, 1):
        if k in required or k in stars:
            require(present.get(k) is True, f"定义第 {pos} 个控件必填，当前为空或没有读回")
    return selected, active, required | stars


def check_snapshot(expected, ui, schema):
    for k in ("draft", "owner", "definition"):
        require(isinstance(expected[k], str) and expected[k].strip() and ui[k] == expected[k],
                "草稿、申请人或表单定义不一致")
    selected, active, required = check_fields(schema, ui)
    historical = set(strings(expected["historical"]))
    matches = ui["historical"]
    require(isinstance(matches, dict) and matches.keys() == historical and
            all(ui["fields"].get(k) is True and matches[k] is True for k in historical),
            "本人历史字段未全部匹配")
    require(Counter(strings(ui["attachments"])) == Counter(strings(expected["attachments"])),
            "附件与原始材料清单不符")
    tables = expected["tables"]
    require(isinstance(tables, dict) and ui["tables"].keys() == tables.keys(), "明细表清单不符")
    active_tables = {k for k, wd in schema.items() if wd.get("children") and active[k]}
    require(active_tables == tables.keys(), "当前条件下应核验的明细表缺失或多出")
    required_count = len(required)
    line_count = 0
    line_amounts = []
    for table_id, expected_rows in tables.items():
        columns = indexed(schema[table_id]["children"], "id")
        rows = indexed(ui["tables"][table_id], "key")
        sources = indexed(expected_rows, "key")
        require(rows.keys() == sources.keys() and (rows or table_id not in required), "明细缺行、重复或多行")
        for key, src in sources.items():
            row = rows[key]
            _, _, row_required = check_fields(columns, row, selected)
            required_count += len(row_required)
            line_count += 1
            require(Counter(strings(row["links"])) == Counter(strings(src["links"])), "明细关联了不同的原单")
            for attr in ("purpose", "category"):
                if attr in src:
                    require(row.get(attr) == src[attr] and bool(src[attr]), "明细用途或分类不符")
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
    exp, before, after = report["expected"], report["before"], report["reopened"]
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
    for k in ("choices", "fields", "stars", "historical", "stable"):
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="私有目录中的脱敏证据 JSON，格式见 SOP-151")
    parser.add_argument("--profile", help="用指定登记身份只读核对当前本人和原表定义")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
        result = check(report)
        if args.profile:
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
