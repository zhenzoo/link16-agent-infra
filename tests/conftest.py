import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
from bridge_env import AGENT_SESSION_ENV_KEYS  # noqa: E402


@pytest.fixture(autouse=True)
def _no_outer_agent_stamp(monkeypatch):
    """The suite is often run from inside an agent's shell (a bot testing its own
    repo). That agent's session stamp would make every simulated bot hook look
    like an agent nested inside the bot (bridge_env.nested_agent)."""
    # A simulated worker must never inspect the real pane hosting pytest.
    for key in (*AGENT_SESSION_ENV_KEYS, "WMUX_MEMBER_ID", "WMUX_PTY_ID",
                "WMUX_WORKSPACE_ID", "WMUX_WORKSPACE_NAME", "WMUX_SHELL_INTEGRATION"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _isolated_profile_registry(monkeypatch):
    """Compatibility examples belong to tests, never to a new user's default registry."""
    import agent_runtime
    fixture = Path(__file__).parent / 'fixtures' / 'agent-profiles.legacy.json'
    monkeypatch.setattr(agent_runtime, 'PROFILE_REGISTRY_PATH', fixture)
    monkeypatch.setattr(agent_runtime, 'PROFILE_REGISTRY_LOCAL_PATH', fixture.parent / 'absent.local.json')
    monkeypatch.delenv(agent_runtime.PROFILE_REGISTRY_ENV, raising=False)
