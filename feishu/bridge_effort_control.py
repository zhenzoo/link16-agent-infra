"""Change one idle Codex TUI's model/effort and verify its live session state."""

import re
import time
import tomllib
from pathlib import Path

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
    live = live_footer(screen)
    return bool(live and live[0].casefold() == model.casefold() and live[1] == effort)


def switch_current(*, model, effort, profile_home, read_screen, send, key):
    """Drive /model, then verify picker, confirmation and live /status.

    This is a human selection: retain the native picker's profile-wide save.
    Existing sibling sessions keep their own live settings.
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
        try:
            send("/model")
            key("enter")
            picker = _wait(read, lambda value: "Select Model and Effort" in value)
            choices = [(index, label) for index, label, _ in _rows(picker)
                       if label.split(" (")[0].split()[0].casefold() == model.casefold()]
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
            confirmation = f"Model changed to {model} {effort}".casefold()
            earlier_toasts = read().casefold().count(confirmation)
            key("enter")
            _wait(read, lambda value: value.casefold().count(confirmation) > earlier_toasts)
            live = _status(read, send, key)
            if live[0].casefold() != model.casefold() or live[1] != effort:
                raise RuntimeError(f"Codex 实时状态是 {live[0]}/{live[1]}，目标是 {model}/{effort}")
            _wait(read, lambda value: _footer_matches(value, model, effort))
            send("/model")
            key("enter")
            picker = _wait(read, lambda value: "Select Model and Effort" in value)
            if not any(text.casefold().startswith(model.casefold() + " (current)")
                       for _, text, _ in _rows(picker)):
                raise RuntimeError("Codex 模型选择器没有把目标模型标为 current")
            key("enter")
            levels = _wait(read, lambda value: f"Select Reasoning Level for {model}" in value)
            if not any(text.lower().startswith(label) and "(current)" in text.lower()
                       for _, text, _ in _rows(levels)):
                raise RuntimeError("Codex effort 选择器没有把目标档位标为 current")
            key("escape")
            key("escape")
            saved = tomllib.loads(config.read_text(encoding="utf-8"))
            if saved.get("model") != model or saved.get("model_reasoning_effort") != effort:
                raise RuntimeError("当前会话已切换，但默认设置已被另一终端更新；保留最新默认")
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
