"""Guarded Docx writes and resumable text batches, behind docio_cli only."""
import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from bridge_injection import ProcessFileLock
from doc_structure import DocStructureError
from doc_xml_structure import verify_xml, xml_contract
from docio_comments import preflight, read_comments, snapshot, text_patch, verify_comments


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fetch(api, token, bot):
    result = api.run_lark(["docs", "+fetch", "--doc", token, "--detail", "full", "--as", "bot"],
                          profile=bot, timeout=300)
    payload = api._json_out(result)
    doc = (payload.get("data") or {}).get("document") or {}
    if result.returncode or payload.get("ok") is not True or not isinstance(doc.get("content"), str) or doc.get("revision_id") is None:
        raise DocStructureError("未完整读到正文与版本，禁止写入")
    return doc


def observe(api, token, url, bot):
    doc = fetch(api, token, bot)
    records = snapshot(doc, read_comments(api.run_lark, url, bot))
    # Comments are not covered by the document revision alone. Read them independently.
    return doc, records


def binding(api, bot, token):
    return {"bot": bot, "token": token, "user_profile": api._READ_AS_USER}


def _verified(api, args, bot, token, journal, doc, comments):
    verified = verify_xml(journal["expected"], doc["content"])
    verified.update(verify_comments(journal["comments"], comments))
    if int(doc["revision_id"]) <= int(journal["before"]["revision_id"]):
        raise DocStructureError("回读版本未前进，不能证明此次修改生效")
    journal.update(state="verified", after_revision=doc["revision_id"])
    path = Path(args.patch)
    save(path.with_name(path.stem + ".docx-state.json"), journal)
    save(path.with_name(path.stem + ".docx-receipt.json"), {
        **verified, **binding(api, bot, token),
        "before_revision": journal["before"]["revision_id"], "after_revision": doc["revision_id"],
        "covers": "Full native XML plus every original comment ID, author, replies, status, live block and selected text",
        "caught": "Whole selected-title replacement is refused before writing; detached/reassigned comments fail readback",
        "judge": "Mechanical preservation; changed requirement/comment meaning still needs review",
    })
    print("回读通过：正文与原评论内容、状态、关联一致；已保存回执")
    return 0


def document_lock(token):
    # All local docio batches/patches for this document share a process lock.
    # The server revision still guards writers on other machines and native UI edits.
    return ProcessFileLock(Path(__file__).parent / "_state" / "docio-locks" /
                           (hashlib.sha256(token.encode()).hexdigest() + ".lock"), timeout=0)


def write_docx(api, args, bot, info, patch):
    try:
        with ProcessFileLock(Path(args.patch).with_suffix(".lock"), timeout=0), document_lock(info["token"]):
            return _write_docx(api, args, bot, info, patch)
    except OSError as exc:
        print(f"写入停止：另一个本机任务正在操作文档或恢复记录，或无法打开锁文件：{exc}")
        return 2


def _write_docx(api, args, bot, info, patch):
    path = Path(args.patch).resolve()
    state_path = path.with_name(path.stem + ".docx-state.json")
    token, body = info["token"], patch.get("docx") or {}
    signature = digest(body)
    current_binding = binding(api, bot, token)
    journal = None
    try:
        if state_path.exists():
            journal = json.loads(state_path.read_text(encoding="utf-8"))
            if journal["signature"] != signature or journal["binding"] != current_binding:
                raise DocStructureError("回执属于不同补丁/文档/身份；使用新补丁文件，不能覆盖旧恢复记录")
        if getattr(args, "recover", False) or (journal and journal["state"] == "verified"):
            if not journal:
                raise DocStructureError("没有可恢复的写入记录")
            doc, comments = observe(api, token, args.url, bot)
            # Recovery is read-only. Even unchanged state after a timeout is not
            # permission to replay a request that may still be in flight.
            return _verified(api, args, bot, token, journal, doc, comments)
        if journal and journal["state"] != "prepared":
            raise DocStructureError("上次可能已写入；先用write --recover只读核对，禁止盲重试")
        command, content = body.get("command"), body.get("content")
        if command not in api.DOCX_COMMANDS or body.get("format") != "xml":
            raise DocStructureError("须使用受支持的docx命令与原生XML")
        if not isinstance(content, str) or not content.strip():
            raise DocStructureError("补丁缺非空content")
        xml_contract(content)
        doc = fetch(api, token, bot)
        revision = doc["revision_id"]
        if body.get("revision_id") is not None and str(body["revision_id"]) != str(revision):
            raise DocStructureError("文档版本已变化，请重读并合并用户改动")
        comments = snapshot(doc, read_comments(api.run_lark, args.url, bot))
        if command == "str_replace" and not body.get("pattern"):
            raise DocStructureError("str_replace必须提供非空pattern")
        expected = (content if command == "overwrite" else
                    doc["content"] + content if command == "append" else body.get("expected_document"))
        xml_contract(expected or "")
        preflight(doc, comments, body)
        journal = {"state": "prepared", "signature": signature, "binding": current_binding,
                   "before": doc, "comments": comments, "expected": expected}
        save(state_path, journal)
        if not args.apply:
            print(f"预演通过：{command}，版本{revision}，检查{len(comments)}条评论；零在线写入")
            return 0
        # Re-observe just before mutation: a comment-only change may not advance revision.
        latest, latest_comments = observe(api, token, args.url, bot)
        if latest["revision_id"] != revision or latest["content"] != doc["content"]:
            raise DocStructureError("写前正文版本变化，零写入")
        verify_comments(comments, latest_comments)
        content_path = path.with_name(path.stem + ".content.xml")
        content_path.write_text(content, encoding="utf-8")
        cmd = ["docs", "+update", "--doc", token, "--command", command, "--doc-format", "xml",
               "--content", "@" + str(content_path), "--revision-id", str(revision), "--as", "bot"]
        for key in ("pattern", "block_id", "start_block_id", "end_block_id"):
            if body.get(key):
                cmd.extend(["--" + key.replace("_", "-"), str(body[key])])
        journal["state"] = "applying"
        save(state_path, journal)
        result = api.run_lark(cmd, profile=bot, timeout=300)
        payload = api._json_out(result)
        data = payload.get("data") or {}
        journal["state"] = "written_unverified"
        # Keep the terminal response before validation; recovery must be able to
        # distinguish a rejected request from a partial or uncertain write.
        # This is an API response, never the child process environment/credentials.
        journal["write_response"] = {"returncode": result.returncode, "payload": payload}
        save(state_path, journal)
        if result.returncode or payload.get("ok") is not True or data.get("result", "success") != "success" or data.get("warnings"):
            raise DocStructureError("接口未确认完整成功或返回警告；可能部分写入，先--recover核对")
        after, after_comments = observe(api, token, args.url, bot)
        return _verified(api, args, bot, token, journal, after, after_comments)
    except (DocStructureError, ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        if journal and journal.get("state") in ("applying", "written_unverified"):
            journal["error"] = str(exc)
            save(state_path, journal)
        print(f"停止：{exc}。未自动重建/解决评论，未自动重试。")
        return 2


def edit_text(api, args, bot, info):
    try:
        with ProcessFileLock(Path(args.state) / "batch.lock", timeout=0), document_lock(info["token"]):
            return _edit_text(api, args, bot, info)
    except OSError as exc:
        print(f"批次停止：另一个本机任务正在操作文档或批次，或无法打开锁文件：{exc}")
        return 2


def checkpoint(api, token, url, bot, pending):
    """Do not adopt a concurrent change between verification and checkpointing."""
    path = Path(pending)
    journal = json.loads(path.with_name(path.stem + ".docx-state.json").read_text(encoding="utf-8"))
    doc, comments = observe(api, token, url, bot)
    if journal["state"] != "verified" or doc["revision_id"] != journal["after_revision"]:
        raise DocStructureError("核验后文档版本又变化，保留pending并停止；不得沿用外部修改")
    verify_xml(journal["expected"], doc["content"])
    verify_comments(journal["comments"], comments)
    return doc, comments


def _edit_text(api, args, bot, info):
    folder = Path(args.state).resolve()
    state_path = folder / "batch.json"
    try:
        changes = json.loads(Path(args.changes).read_text(encoding="utf-8"))["changes"]
        if not isinstance(changes, list) or not changes:
            raise DocStructureError("changes必须为非空列表")
        token = info["token"]
        stamp = {"binding": binding(api, bot, token), "changes_hash": digest(changes)}
        if state_path.exists() and not args.resume:
            raise DocStructureError("批次目录已存在，使用--resume核对；不得覆盖原记录")
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
            **stamp, "completed": 0, "pending": None, "status": "prepared"}
        if any(state.get(k) != v for k, v in stamp.items()):
            raise DocStructureError("批次身份/目标/修改清单变化；拒绝续跑")
        if state.get("pending"):
            pending = Path(state["pending"])
            patch = json.loads(pending.read_text(encoding="utf-8"))
            journal_path = pending.with_name(pending.stem + ".docx-state.json")
            journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else None
            if journal and journal["state"] != "prepared":
                recovered = _write_docx(api, SimpleNamespace(url=args.url, patch=str(pending),
                                       apply=False, recover=True), bot, info, patch)
                if recovered:
                    raise DocStructureError("上次写入尚未核实；仅恢复读取，未重复写入")
                state["completed"] += 1
                doc, comments = checkpoint(api, token, args.url, bot, pending)
                state.update(pending=None, checkpoint={"doc": doc, "comments": comments})
                save(state_path, state)
            else:
                state["pending"] = None  # No update call was started.
        doc, comments = observe(api, token, args.url, bot)
        if state.get("checkpoint"):
            old = state["checkpoint"]
            if old["doc"]["revision_id"] != doc["revision_id"] or old["doc"]["content"] != doc["content"]:
                raise DocStructureError("续跑时正文已变化；请合并改动后创建新批次")
            verify_comments(old["comments"], comments)
        # Plan the whole remaining batch without mutation; reject late dangerous edits early.
        simulated = dict(doc)
        preview = []
        for change in changes[state["completed"]:]:
            patch = text_patch(simulated, change)
            preflight(simulated, comments, patch)
            preview.append({"block_id": change["block_id"], "pattern": patch["pattern"], "content": patch["content"]})
            simulated["content"] = patch["expected_document"]
        save(folder / "preview.json", preview)
        state["checkpoint"] = {"doc": doc, "comments": comments}
        save(state_path, state)
        if not args.apply:
            print(f"批次预演通过：剩余{len(preview)}处；preview.json可审阅，零在线写入")
            return 0
        while state["completed"] < len(changes):
            index = state["completed"]
            body = text_patch(doc, changes[index])
            pending = folder / f"edit-{index+1:04d}.json"
            save(pending, {"docx": body})
            state.update(pending=str(pending), status="running")
            save(state_path, state)
            if _write_docx(api, SimpleNamespace(url=args.url, patch=str(pending),
                          apply=True, recover=False), bot, info, {"docx": body}):
                return 2
            doc, comments = checkpoint(api, token, args.url, bot, pending)
            state.update(completed=index+1, pending=None, checkpoint={"doc": doc, "comments": comments})
            save(state_path, state)
        state["status"] = "verified"
        save(state_path, state)
        print(f"批次完成：{state['completed']}处正文和评论均已回读，回执在{folder}")
        return 0
    except (DocStructureError, ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"批次停止：{exc}；保留原状态，不自动跳过或重发")
        return 2
