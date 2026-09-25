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
    for key in AGENT_SESSION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
