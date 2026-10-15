"""One profile default, plus explicitly requested effort for one accepted turn."""

import json
import os
import re
import threading
import time
import tomllib
import uuid
from pathlib import Path

import tomlkit
from bridge_injection import ProcessFileLock


EFFORTS = frozenset({"medium", "high", "xhigh"})
CONTRACT = "profile-default-v1"


def normalize(value):
    value = str(value or "").strip().lower().replace("-", "").replace(" ", "")
    if value == "extrahigh":
        value = "xhigh"
    if value in EFFORTS:
        return value
    raise ValueError("档位只接受 medium、high、xhigh（也可写 x-high）")


def profile_defaults(profile_home):
    config = Path(profile_home) / "config.toml"
    try:
        saved = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取当前 Codex profile 默认设置：{config}: {exc}") from exc
    model = saved.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"当前 Codex profile 未保存默认模型：{config}")
    return {"model": model.strip(), "effort": saved.get("model_reasoning_effort")}


def profile_model(profile_home):
    return profile_defaults(profile_home)["model"]


def save_default(profile_home, effort):
    """Human /effort saves the same key as the native picker; keep other keys."""
    effort = normalize(effort)
    config = Path(profile_home) / "config.toml"
    with ProcessFileLock(config.with_name(".config.toml.link16-effort.lck")):
        original = config.read_text(encoding="utf-8")
        document = tomlkit.parse(original)
        if not isinstance(document.get("model"), str) or not document["model"].strip():
            raise ValueError("当前 Codex profile 未保存默认模型")
        document["model_reasoning_effort"] = effort
        temporary = config.with_name(f".config.toml.link16-effort.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
            # Native terminals don't share our lock; reject observed concurrent saves.
            if config.read_text(encoding="utf-8") != original:
                raise RuntimeError("profile 默认设置被另一终端修改；请重试本次选择")
            os.replace(temporary, config)
        finally:
            temporary.unlink(missing_ok=True)
        saved = profile_defaults(profile_home)
        if saved["effort"] != effort:
            raise RuntimeError("profile 默认设置已被另一终端更新；以最新保存为准")
        return saved


def next_path(state_dir, bot):
    return Path(state_dir) / f"bridge-effort-next-{bot}.json"


def _read(path):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(record, dict):
        raise ValueError("飞书桥 effort 状态无效")
    return record


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def selection_record(state_dir, bot, profile, startup_id, thread_id):
    record = _read(next_path(state_dir, bot))
    if not record or record.get("consumed_at"):
        return None
    if any(record.get(key) != value for key, value in (
            ("profile", profile), ("startup_id", startup_id), ("thread_id", thread_id))):
        return None
    if not startup_id or not thread_id or record.get("effort") not in EFFORTS:
        raise ValueError("飞书桥临时 effort 状态无效")
    return record


def select_next(state_dir, bot, profile, effort, *, message_id=None):
    """Bind to the live worker and thread, never to a future /new."""
    import codex_startup
    effort = normalize(effort)
    ready = codex_startup.read_state(
        Path(state_dir) / f"bridge-codex-app-ready-{bot}.json")
    if (ready.get("profile") != profile or ready.get("effort_contract") != CONTRACT
            or not ready.get("startup_id") or not ready.get("thread_id")
            or not codex_startup.ready(ready, bot=bot)):
        raise RuntimeError("当前 worker 不支持一次性 effort；下次正常新建会话后可用")
    target = next_path(state_dir, bot)
    record = {"profile": profile, "effort": effort, "startup_id": ready["startup_id"],
              "thread_id": ready["thread_id"], "selection_id": uuid.uuid4().hex,
              "message_id": message_id, "updated_at": time.time()}
    with ProcessFileLock(target.with_suffix(".lck")):
        _write(target, record)
    return record


def status(state_dir, bot, profile, profile_home):
    defaults = profile_defaults(profile_home)
    ready = _read(Path(state_dir) / f"bridge-codex-app-ready-{bot}.json") or {}
    pending = selection_record(state_dir, bot, profile, ready.get("startup_id"), ready.get("thread_id"))
    receipt = _read(Path(state_dir) / f"bridge-effort-applied-{bot}.json")
    if receipt and (receipt.get("profile") != profile or receipt.get("contract") != CONTRACT
                    or receipt.get("startup_id") != ready.get("startup_id")
                    or receipt.get("thread_id") != ready.get("thread_id")):
        receipt = None
    return {**defaults, "pending": pending, "receipt": receipt}


def effective_effort(params):
    # Codex collaboration settings take precedence over the top-level field.
    settings = (params.get("collaborationMode") or {}).get("settings") or {}
    return settings.get("reasoning_effort") or params.get("effort")


class TurnEffort:
    """Correlate a one-shot selection with its actual RPC acceptance."""

    def __init__(self, state_dir, bot, profile, startup_id):
        self.state_dir, self.bot, self.profile, self.startup_id = state_dir, bot, profile, startup_id
        self.pending = {}
        self.consumed = set()
        self.lock = threading.Lock()

    def apply(self, raw):
        message = json.loads(raw)
        if message.get("method") != "turn/start" or "id" not in message:
            return raw
        params = message.get("params") or {}
        try:
            record = selection_record(self.state_dir, self.bot, self.profile,
                                      self.startup_id, params.get("threadId"))
        except (OSError, ValueError, UnicodeError):
            return raw
        if not record:
            return raw
        if record.get("message_id"):
            texts = [item.get("text", "") for item in params.get("input", [])
                     if isinstance(item, dict) and item.get("type") == "text"]
            marker = r"\bmid=" + re.escape(record["message_id"]) + r"(?=[\s\]])"
            if not any(re.search(marker, text) for text in texts):
                return raw
        with self.lock:
            if record["selection_id"] in self.pending.values() or record["selection_id"] in self.consumed:
                return raw
            self.pending[message["id"]] = record["selection_id"]
        params["effort"] = record["effort"]
        collaboration = params.get("collaborationMode")
        if isinstance(collaboration, dict):
            collaboration.setdefault("settings", {})["reasoning_effort"] = record["effort"]
        return json.dumps(message, ensure_ascii=False)

    def result(self, request, turn_id):
        with self.lock:
            selected_id = self.pending.pop(request["id"], None)
            if selected_id and turn_id:
                self.consumed.add(selected_id)
        if not turn_id:
            return  # Rejected requests may retry the same selection.
        if selected_id:
            target = next_path(self.state_dir, self.bot)
            with ProcessFileLock(target.with_suffix(".lck")):
                current = _read(target)
                if current and current.get("selection_id") == selected_id:
                    _write(target, {**current, "consumed_at": time.time(), "turn_id": turn_id})
        params = request.get("params") or {}
        effort = effective_effort(params)
        _write(Path(self.state_dir) / f"bridge-effort-applied-{self.bot}.json",
               {"contract": CONTRACT, "profile": self.profile, "startup_id": self.startup_id,
                "thread_id": params.get("threadId"), "effort": effort,
                "selection_id": selected_id, "turn_id": turn_id, "accepted_at": time.time()})
