from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
ROLE = ROOT / "docs" / "ROLE-010-link16-deployment-engineer.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_entry_documents_have_frontmatter_and_shared_routes():
    targets = (AGENTS, CLAUDE, ROLE)
    for path in targets:
        text = _read(path)
        assert text.startswith("---\n"), path
        for field in (
            "doc_type:", "doc_id:", "title:", "status:", "purpose:",
            "owns:", "does_not_own:", "read_when:", "last_reviewed:",
        ):
            assert field in text.split("---", 2)[1], (path, field)
    for path in (AGENTS, CLAUDE):
        text = _read(path)
        assert "docs/ROLE-010-link16-deployment-engineer.md" in text
        assert "docs/SOP-100-new-machine-setup.md" in text
        assert "TOOLS.md" in text


def test_codex_entry_contains_only_codex_adapter_markers():
    text = _read(AGENTS)
    for marker in ("$feishu", "UserPromptSubmit", "spawn_agent", "Codex hook/event"):
        assert marker in text
    for forbidden in (
        "bridge_stop.py", "bridge_posttool.py", "CLAUDE_CODE_CHILD_SESSION",
        ".claude-personal", ".codex-personal", "共享正文只在",
    ):
        assert forbidden not in text


def test_claude_entry_contains_only_claude_adapter_markers():
    text = _read(CLAUDE)
    for marker in (
        "/feishu", "bridge_stop.py", "bridge_posttool.py",
        "CLAUDE_CODE_CHILD_SESSION", ".jsonl", "Agent tool",
    ):
        assert marker in text
    for forbidden in (
        "codex_app_server_worker", "UserPromptSubmit", "codex_bridge_stop",
        ".claude-personal", ".codex-personal", "共享正文只在 AGENTS",
    ):
        assert forbidden not in text


def test_role_owns_human_gates_and_completion_without_runtime_mechanics():
    text = _read(ROLE)
    for marker in ("人工停点", "机械继续信号", "Core ready", "Feishu ready", "Group ready"):
        assert marker in text
    for forbidden in (
        "spawn_agent", "Agent tool", "JSONL", "UserPromptSubmit",
        "CODEX_HOME", "CLAUDE_CONFIG_DIR",
    ):
        assert forbidden not in text


def test_entry_links_exist_and_do_not_copy_long_shared_paragraphs():
    for path in (AGENTS, CLAUDE):
        text = _read(path)
        for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text):
            resolved = (path.parent / target).resolve()
            assert resolved.exists(), (path, target)

    agent_paragraphs = {
        re.sub(r"\s+", " ", part).strip()
        for part in _read(AGENTS).split("\n\n")
        if len(re.sub(r"\s+", " ", part).strip()) >= 160
        and not all(marker in part for marker in ("ROLE-010", "SOP-100", "TOOLS.md"))
    }
    claude_paragraphs = {
        re.sub(r"\s+", " ", part).strip()
        for part in _read(CLAUDE).split("\n\n")
        if len(re.sub(r"\s+", " ", part).strip()) >= 160
        and not all(marker in part for marker in ("ROLE-010", "SOP-100", "TOOLS.md"))
    }
    assert not (agent_paragraphs & claude_paragraphs)


def test_active_setup_does_not_recreate_legacy_watchdog_task():
    text = _read(ROOT / "docs" / "SOP-100-new-machine-setup.md")
    assert not re.search(r"Register-ScheduledTask[^\n]+AutopilotWatchdog", text)
    assert not re.search(r"Start-ScheduledTask[^\n]+AutopilotWatchdog", text)
    assert "service_installer.py plan" in text
    assert "service_installer.py apply --yes --expect" in text
    assert "service_installer.py rollback --receipt" in text
    assert "service_doctor.py" in text
    for direct_write in ("Set-ItemProperty", "Register-ScheduledTask", "New-ScheduledTaskAction"):
        assert direct_write not in text
