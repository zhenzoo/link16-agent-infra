"""Executable rules for registered workbench deliverables and human-facing drafts."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check_message(body, policy):
    if not isinstance(body, str) or not body.strip():
        raise ValueError("消息正文为空")
    if not policy:
        return
    if policy["kind"] != "procurement":
        raise ValueError("未登记的消息类别")
    lines = body.strip().splitlines()
    salutation = policy["salutation"]
    greeting = lines[0].strip().rstrip("，,：:！!。")
    if greeting not in (salutation, salutation + "你好"):
        raise ValueError("采购草稿第一行单列约定称呼")
    points = [line.strip() for line in lines[1:] if line.strip()]
    if not 2 <= len(points) <= 6:
        raise ValueError("采购草稿须分成 2–6 行信息点")
    measured = [re.sub(r"https://[^\s]+", "〔链接〕", line) for line in points]
    if max(map(len, measured)) > 52 or sum(map(len, measured)) > 160:
        raise ValueError("采购草稿每行最多 52 字，信息点合计最多 160 字")
    normalized = re.sub(r"[ \t]+", "", body)
    for pattern in policy.get("required_patterns", []):
        if not re.search(pattern, normalized):
            raise ValueError("草稿中的数量、日期或指标与原单不符")
    for pattern in policy.get("forbidden_patterns", []):
        if re.search(pattern, normalized):
            raise ValueError("草稿包含其他业务的数量或指标")
    for alternatives in policy.get("required", []):
        if not any(term in body for term in alternatives):
            raise ValueError("草稿缺原单信息点：" + " / ".join(alternatives))


def check_outgoing(bot, recipient, body, business=None):
    """Route human sends/replies by the registered business and recipient."""
    check_message(body, None)
    state = Path(__file__).resolve().parent / "_state" / "proactive"
    matched = False
    for path in state.glob("*/morning-business.json"):
        plan = json.loads(path.read_text("utf-8-sig"))
        for key, targets in plan.get("message_policies", {}).items():
            policy = targets.get(recipient)
            if not policy or bot not in (path.parent.name, policy.get("author")):
                continue
            if business and key != business:
                continue
            if not business and not any(tag in body for tag in policy.get("subjects", [])):
                continue
            check_message(body, policy)
            matched = True
    if business and not matched:
        raise ValueError("指定业务与收件人没有已登记的消息规则")


def artifact_links(pair):
    local, online = urlsplit(pair["local"]), urlsplit(pair["online"])
    path = unquote(local.path)
    if (local.scheme or local.netloc or path.startswith(("/", "\\")) or "\\" in path
            or ":" in path or ".." in Path(path).parts or Path(path).suffix.lower() not in {".htm", ".html"}):
        raise ValueError("本地成品须指向工作台目录中的原 HTML")
    if online.scheme != "https" or not online.hostname or online.username or online.password:
        raise ValueError("线上成品须为登记的 HTTPS 演示")
    title = pair["title"].strip()
    if not title:
        raise ValueError("成品缺少名称")
    return [{"label": side + " · " + title, "url": pair[name]} for side, name in (("本地", "local"), ("在线", "online"))]


def check_pair_row(row, key, pair):
    expected = artifact_links(pair)
    result = row.select_one("td.outcomes")
    anchors = row.select("a[href]")
    if (result is None or len(anchors) != 2 or anchors != result.select(":scope > .result-links > a")
            or [a.get_text() for a in anchors] != [r["label"] for r in expected]
            or anchors[1]["href"] != pair["online"] or row.select(".attachments img,.attachments video")
            or row.select_one(".attachments") is None or row.select_one(".attachments").get_text(strip=True)):
        raise ValueError("成品行只展示本地、在线两个对应入口")
    path_id = "local-" + key
    visible = "".join(a.get_text() for a in anchors)
    if anchors[0]["href"] == "#" + path_id:
        pointer = result.find("code", id=path_id)
        if not pointer or not Path(pointer.get_text()).as_posix().endswith("/" + unquote(urlsplit(pair["local"]).path)):
            raise ValueError("手机工作台缺少登记的本地原件路径")
        visible += pointer.get_text()
    elif anchors[0]["href"] != pair["local"]:
        raise ValueError("本地成品链接与登记原件不符")
    if result.get_text(strip=True) != visible:
        raise ValueError("成品结果只展示本地与在线原件")


def check_registered_board(html, plan):
    from bs4 import BeautifulSoup
    page = BeautifulSoup(html, "html.parser")
    for key in set(plan.get("artifact_pairs", {})) | set(plan.get("message_policies", {})):
        rows = page.find_all("tr", attrs={"data-business-id": key})
        if len(rows) != 1:
            raise ValueError("成品缺少唯一的已登记业务行：" + key)
        if key in plan.get("artifact_pairs", {}):
            check_pair_row(rows[0], key, plan["artifact_pairs"][key])
        policies = plan.get("message_policies", {}).get(key, {})
        expected = {r["recipient"]: r["text"] for r in plan.get("receipts", {}).get(key, {}).get("results", []) if r["type"] == "draft_message"}
        if policies:
            actual = {}
            for draft in rows[0].select(".draft-message"):
                name = draft.summary.get_text().removeprefix("拟发给 ")
                if name not in policies or name in actual:
                    raise ValueError("草稿收件人未登记或重复")
                actual[name] = draft.blockquote.get_text()
                check_message(actual[name], policies[name])
            if actual != expected:
                raise ValueError("采购草稿与登记原文不符")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    message = commands.add_parser("message", help="Validate an outgoing business draft without sending")
    for name in ("bot", "recipient", "business", "text-file"):
        message.add_argument("--" + name, required=True)
    board = commands.add_parser("workbench", help="Check the rendered workbench against its private business register")
    board.add_argument("--plan", required=True, type=Path)
    board.add_argument("--html", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "workbench":
            check_registered_board(args.html.read_text("utf-8-sig"), json.loads(args.plan.read_text("utf-8-sig")))
        else:
            check_outgoing(args.bot, args.recipient, Path(args.text_file).read_text("utf-8-sig"), args.business)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("OK")


if __name__ == "__main__":
    main()
