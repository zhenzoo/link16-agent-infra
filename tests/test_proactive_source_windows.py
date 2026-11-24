from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_sources as sources


def test_resume_partial_complete_and_current_snapshots(monkeypatch):
    start, old_end, end = (datetime(2026, 11, day, tzinfo=timezone.utc) for day in (21, 22, 23))
    previous = dict(bot="worker", owner_id="owner", until=old_end.isoformat(), sources={
        "calendar": dict(status="partial", since=start.isoformat()),
        "meetings": dict(status="empty", since=start.isoformat()),
    })
    for name in ("calendar", "meetings", "tasks", "approvals"):
        monkeypatch.setattr(sources, name, lambda *args, **kwargs: [])
    with patch.object(sources.bridge_scope_audit, "user_identity_status",
                      return_value=dict(verified_owner_id="owner", owner_matches=True, status="ready")):
        result = sources.collect("worker", old_end, end, previous)
        assert result["ok"]
        assert result["sources"]["calendar"]["since"] == start.isoformat()
        assert result["sources"]["meetings"]["since"] == old_end.isoformat()
        with pytest.raises(ValueError):
            sources.collect("worker", old_end, end, dict(previous, owner_id="someone-else"))
        with pytest.raises(ValueError):
            sources.collect("worker", start, old_end, previous)
