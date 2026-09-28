"""Change one idle Codex TUI's model/effort and verify its live session state."""

import os
import re
import time
import tomllib
from pathlib import Path

import tomlkit

from bridge_injection import ProcessFileLock


_ROW = re.compile(r"^\s*(?P<selected>›)?\s*(?P<index>\d+)\.\s+(?P<label>.+)$")
_STATUS = re.compile(r"\bModel:\s+(\S+)\s+\(reasoning\s+(low|medium|high|xhigh|max|ultra)\b")
_FOOTER = re.compile(r"^\s*(\S+)\s+(low|medium|high|xhigh|max|ultra)\s+·\s+", re.MULTILINE)
_EFFORT_LABELS = {"medium": "medium", "high": "high", "xhigh": "extra high"}


def _wait(read, predicate, *, timeout=8):
    deadline = time.monotonic() + timeout
    while True:
        screen = read()
        if predicate(screen):
            return screen
        if time.monotonic() >= deadline:
            raise RuntimeError("Codex 终端没有显示预期的模型选择/状态画面")
        time.sleep(.15)


def _rows(screen):
    rows = []
    for line in screen.splitlines():
        match = _ROW.match(line)
        if match:
            rows.append((int(match["index"]), match["label"].strip(), bool(match["selected"])))
    return rows


def _selected_index(screen):
    selected = [index for index, _, active in _rows(screen) if active]
    if len(selected) != 1:
        raise RuntimeError("Codex 选择器没有唯一的当前选中项")
    return selected[0]


def _navigate(read, key, target):
    current = _selected_index(read())
    for _ in range(abs(target - current)):
        key("down" if target > current else "up")
    if _selected_index(read()) != target:
        raise RuntimeError("Codex 选择器没有移动到目标选项")


def _status(read, send, key):
    earlier = len(_STATUS.findall(read()))
    send("/status")
    key("enter")
    screen = _wait(read, lambda value: len(_STATUS.findall(value)) > earlier)
    matches = _STATUS.findall(screen)
    return matches[-1]


def live_footer(screen):
    """The bottom status line is live; the startup banner is historical."""
    found = _FOOTER.findall("\n".join(screen.splitlines()[-10:]))
    return found[-1] if found else None


def _footer_matches(screen, model, effort):
    return live_footer(screen) == (model, effort)


def _restore_profile_config(config, original, chosen_model, chosen_effort):
    """Undo only the profile-wide keys the native /model menu changed."""
    with ProcessFileLock(config.with_name(".config.toml.link16-effort.lck")):
        current_text = config.read_text(encoding="utf-8")
        current = tomlkit.parse(current_text)
        keys = {"model": chosen_model, "model_reasoning_effort": chosen_effort}
        for key, chosen in keys.items():
            if current.get(key) != original.get(key) and current.get(key) != chosen:
                raise RuntimeError(f"profile 的 {key} 被另一进程修改；没有覆盖它")
        for key in keys:
            if key in original:
                current[key] = original[key]
            else:
                current.pop(key, None)
        updated = tomlkit.dumps(current)
        if updated != current_text:
            temporary = config.with_name(f".config.toml.link16-effort.{os.getpid()}.tmp")
            temporary.write_text(updated, encoding="utf-8")
            os.replace(temporary, config)
        verified = tomllib.loads(config.read_text(encoding="utf-8"))
        if any(verified.get(key) != original.get(key) for key in keys):
            raise RuntimeError("Codex profile 默认模型/effort 恢复核验失败")


def switch_current(*, model, effort, profile_home, read_screen, send, key):
    """Drive /model, then verify picker, confirmation and live /status.

    The native picker writes the shared profile config. Restore its original
    model/effort immediately so sibling bots keep their own defaults; the TUI's
    live session choice remains changed, as verified by /status.
    """
    config = Path(profile_home) / "config.toml"
    original = tomllib.loads(config.read_text(encoding="utf-8"))
    if original.get("model") != model:
        raise RuntimeError("profile 默认模型已变化；请重新读取后再切换")
    with ProcessFileLock(config.with_name(".config.toml.link16-effort-session.lck")):
        def read():
            return read_screen()
        if not any("› Ask Codex to do anything" in line for line in read().splitlines()[-20:]):
            raise RuntimeError("当前 Codex 终端不是空闲输入框；先等正在执行的任务结束")
        before = _status(read, send, key)
        if "› Ask Codex to do anything" not in read():
            raise RuntimeError("Codex 状态查询后未回到空闲输入框")
        changed = False
        try:
            send("/model")
            key("enter")
            picker = _wait(read, lambda value: "Select Model and Effort" in value)
            choices = [(index, label) for index, label, _ in _rows(picker)
                       if label.split(" (")[0].split()[0] == model]
            if len(choices) != 1:
                raise RuntimeError(f"Codex 模型选择器找不到唯一的 {model}")
            _navigate(read, key, choices[0][0])
            key("enter")
            levels = _wait(read, lambda value: f"Select Reasoning Level for {model}" in value)
            label = _EFFORT_LABELS[effort]
            choices = [(index, text) for index, text, _ in _rows(levels)
                       if text.lower().startswith(label) and
                       (len(text) == len(label) or not text[len(label)].isalpha())]
            if len(choices) != 1:
                raise RuntimeError(f"Codex effort 选择器找不到唯一的 {effort}")
            _navigate(read, key, choices[0][0])
            earlier_toasts = read().count(f"Model changed to {model} {effort}")
            key("enter")
            toast = _wait(read, lambda value: value.count(
                f"Model changed to {model} {effort}") > earlier_toasts)
            changed = True
            # Native /model also writes the shared profile. Undo that write
            # before the slower status/picker checks so sibling bots see the
            # profile default again as soon as possible.
            _restore_profile_config(config, original, model, effort)
            live = _status(read, send, key)
            if live != (model, effort):
                raise RuntimeError(f"Codex 实时状态是 {live[0]}/{live[1]}，目标是 {model}/{effort}")
            _wait(read, lambda value: _footer_matches(value, model, effort))
            send("/model")
            key("enter")
            picker = _wait(read, lambda value: "Select Model and Effort" in value)
            if not any(text.startswith(model + " (current)") for _, text, _ in _rows(picker)):
                raise RuntimeError("Codex 模型选择器没有把目标模型标为 current")
            key("enter")
            levels = _wait(read, lambda value: f"Select Reasoning Level for {model}" in value)
            if not any(text.lower().startswith(label) and "(current)" in text.lower()
                       for _, text, _ in _rows(levels)):
                raise RuntimeError("Codex effort 选择器没有把目标档位标为 current")
            key("escape")
            key("escape")
            return {"before": before, "after": live, "footer_verified": True,
                    "picker_verified": True, "confirmation_verified": True}
        finally:
            # Esc is harmless at an idle prompt; it also closes either picker
            # if a validation or I/O error interrupted the navigation.
            try:
                if "Select Reasoning Level" in read() or "Select Model and Effort" in read():
                    key("escape")
                    key("escape")
            except Exception:
                pass
            _restore_profile_config(config, original, model, effort)
