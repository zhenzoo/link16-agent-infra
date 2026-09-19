"""Quota selection and user-visible failover evidence; no live I/O."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import agent_quota as quota
import bridge_watchdog as watchdog


def row(profile, runtime="codex", used=8, recommended=False):
    return dict(profile=profile, runtime=runtime, status="ok", verdict="够用",
                session_percent=0, weekly_percent=used, recommended=recommended,
                session_reset="09-13 05:00", weekly_reset="09-19 16:15")


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_quota_uses_registry_recommendation_in_either_order(reverse):
    rows = [row("cx"), row("cxp", recommended=True)]
    if reverse:
        rows.reverse()
    assert quota.pick(rows, exclude=["ccp"], prefer_runtime="claude")["profile"] == "cxp"


def test_recommendation_preserves_runtime_capacity_and_unavailable_boundaries():
    cxp = row("cxp", recommended=True)
    assert quota.pick([cxp, row("cc", "claude", 60)], prefer_runtime="claude")["profile"] == "cc"
    assert quota.pick([row("cx", used=2), cxp])["profile"] == "cx"
    assert quota.pick([row("cx"), cxp], exclude=["cxp"])["profile"] == "cx"
    for verdict in ["问不到", "满"]:
        assert quota.pick([row("cx"), dict(cxp, verdict=verdict)])["profile"] == "cx"
    assert quota.pick([dict(cxp, verdict="问不到")]) is None


def test_collect_carries_recommendation_from_registry_to_real_selector(monkeypatch, tmp_path):
    specs = [SimpleNamespace(name=p, runtime="codex", home_path=tmp_path,
                             label=p, recommended=p == "cxp") for p in ["cx", "cxp"]]
    monkeypatch.setattr(quota.agent_runtime, "profile_specs", lambda: specs)
    monkeypatch.setattr(quota, "_codex_quota", lambda home: dict(status="ok",
                        session_percent=0, weekly_percent=8, severity="normal"))
    rows = quota.collect()
    assert quota.pick(rows, prefer_runtime="claude")["profile"] == "cxp"


def test_claude_scoped_limit_survives_normalization_and_reporting(monkeypatch, tmp_path):
    (tmp_path / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "test-placeholder"}}), encoding="utf-8")
    payload = {
        "five_hour": {"utilization": 10, "resets_at": "2026-09-12T16:40:00+00:00"},
        "seven_day": {"utilization": 74, "resets_at": "2026-09-14T15:00:00+00:00"},
        "limits": [
            {"kind": "session", "percent": 10, "severity": "normal", "is_active": False},
            {"kind": "weekly_scoped", "percent": 100, "severity": "critical",
             "is_active": True, "resets_at": "2026-09-14T15:00:00+00:00",
             "scope": {"model": {"display_name": "Fable"}}},
        ],
    }
    monkeypatch.setattr(quota, "_get_json_dual", lambda *a, **k: (payload, None, "代理"))
    actual = quota._claude_quota(tmp_path)
    assert quota._verdict(actual) == "够用"  # Fable's ceiling is not an account ceiling.
    assert actual["active_limits"][0]["model_scoped"] is True
    checked = dict(actual, verdict=quota._verdict(actual))
    screen = "You've hit your session limit · resets 5am (Asia/Shanghai)"
    limited, why, uncertain = watchdog.is_limited(screen, checked)
    assert limited is False and uncertain is False
    assert "专项额度" in why and "/model opus" in why
    assert watchdog.scoped_limit_notice("Working", checked) is None
    text = quota.quota_summary(actual)
    assert "5小时 10%" in text and "09-13 00:40" in text
    assert "周 74%" in text and "09-14 23:00" in text
    assert "活跃限制 Fable 100%" in text
    assert len(actual["active_limits"]) == 1
    assert "test-placeholder" not in json.dumps(actual)


def test_unknown_quota_is_never_displayed_as_zero():
    text = quota.quota_summary({"session_percent": None, "weekly_percent": None})
    assert "未知" in text and "0%" not in text


def test_automatic_selection_respects_manual_only_profiles_even_with_more_quota():
    cx = dict(row("cx", used=0), auto_failover=False)
    cc = dict(row("cc", "claude", 0), auto_failover=False)
    cxp = row("cxp", used=80, recommended=True)
    assert quota.pick([cx, cc, cxp], prefer_runtime="claude")["profile"] == "cxp"
    assert quota.pick([cx, cc, dict(cxp, verdict="问不到")]) is None
    assert quota.pick([cx, cc, dict(cxp, verdict="满")]) is None


def test_registry_manual_profiles_are_still_resolvable_and_flag_reaches_quota(monkeypatch, tmp_path):
    import agent_runtime
    for name in ["cx", "cc"]:
        spec = agent_runtime.profile_spec(name)
        assert spec.auto_failover is False
        assert agent_runtime.profile_public_dict(spec)["auto_failover"] is False
    specs = [SimpleNamespace(name=p, runtime="codex", home_path=tmp_path,
                             label=p, recommended=p == "cxp", auto_failover=p != "cx")
             for p in ["cx", "cxp"]]
    monkeypatch.setattr(quota.agent_runtime, "profile_specs", lambda: specs)
    monkeypatch.setattr(quota, "_codex_quota", lambda home: dict(status="ok",
                        session_percent=0, weekly_percent=8, severity="normal"))
    rows = quota.collect()
    assert next(r for r in rows if r["profile"] == "cx")["auto_failover"] is False
    assert quota.pick(rows)["profile"] == "cxp"


@pytest.mark.parametrize("kind,scope", [("session", {}), ("weekly_all", {})])
def test_global_limit_remains_full_when_scoped_limit_also_exists(monkeypatch, tmp_path, kind, scope):
    (tmp_path / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "test-placeholder"}}), encoding="utf-8")
    data = {"five_hour": {"utilization": 10}, "seven_day": {"utilization": 72},
            "limits": [{"kind": kind, "scope": scope, "severity": "critical", "is_active": True},
                       {"kind": "weekly_scoped", "severity": "critical", "is_active": True,
                        "scope": {"model": {"display_name": "Fable"}}}]}
    monkeypatch.setattr(quota, "_get_json_dual", lambda *a, **k: (data, None, "代理"))
    result = quota._claude_quota(tmp_path)
    assert quota._verdict(result) == "满"
    checked = dict(result, verdict=quota._verdict(result))
    assert watchdog.scoped_limit_notice("You've hit your session limit", checked) is None
    assert watchdog.is_limited("You've hit your session limit", checked)[0] is True


def test_invalid_auto_failover_flag_is_rejected(tmp_path):
    import agent_runtime
    p = tmp_path / "profiles.json"
    p.write_text(json.dumps({"version": 1, "default_profiles": {"codex": "cxp"},
        "profiles": {"cxp": {"runtime": "codex", "home": "~/.codex-personal",
                               "launcher": "direct", "auto_failover": "false"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="auto_failover"):
        agent_runtime.profile_specs(p)


def test_failover_alert_preserves_session_trigger_and_distinct_reset_times(monkeypatch):
    import feishu_bridge as bridge
    bot = {"name": "unit-tennis", "profile": "ccp", "cwd": "."}
    current = dict(row("ccp", "claude", 72), verdict="满", session_percent=100,
                   session_reset="09-12 05:00", weekly_reset="09-14 23:00")
    unknown = dict(row("ccp2", "claude"), verdict="问不到", status="unknown")
    rows = [current, unknown, row("cx"), row("cxp", recommended=True)]
    messages = []
    monkeypatch.setattr(bridge, "load_bots", lambda: [bot])
    monkeypatch.setattr(watchdog, "session_record", lambda name: {"profile": "ccp"})
    monkeypatch.setattr(quota, "collect", lambda: rows)
    monkeypatch.setattr(watchdog, "_failover_gate", lambda name: (True, "unit"))
    monkeypatch.setattr(watchdog, "snapshot_handoff", lambda *a, **k: {
        "screen_tail": "  ⎿  You've hit your session limit · resets 5am (Asia/Shanghai)"})
    monkeypatch.setattr(watchdog, "build_handoff_prompt", lambda *a: "unit handoff")
    monkeypatch.setattr(watchdog, "notify", lambda name, kind, text:
                        messages.append((kind, text)) or True)
    monkeypatch.setattr(watchdog, "log", lambda text: None)
    def stop_before_mutation(*a, **k):
        raise RuntimeError("test stops before changing any account")
    monkeypatch.setattr(watchdog.agent_runtime, "persist_account", stop_before_mutation)
    assert watchdog.failover("unit-tennis") is False
    text = next(text for kind, text in messages if kind == "limit")
    assert "session limit · resets 5am" in text
    assert "5小时 100%（09-12 05:00 重置）" in text
    assert "周 72%（09-14 23:00 重置）" in text
    assert "正在切到 cxp" in text
    assert "问不到不等于额度已耗尽" in text
    assert "其他号都用不了" not in text
