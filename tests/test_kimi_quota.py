"""Native Kimi quota adapts into the existing conservative failover contract."""
import json
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import agent_quota as q


@pytest.fixture
def home(tmp_path):
    (tmp_path / "config.toml").write_text('''default_model = "kimi-code/k3.1-pro"
[models."kimi-code/k3.1-pro"]
provider = "managed:kimi-code"
[providers."managed:kimi-code"]
base_url = "https://api.kimi.com/coding/v1"
[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"
''', encoding="utf-8")
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "kimi-code.json").write_text(
        json.dumps({"access_token": "fixture-token"}), encoding="utf-8")
    return tmp_path


def payload(short=0.34, weekly=1):
    return {"usages": {
        "limit_5h": {"used_ratio": short, "reset_time": "2026-11-12T07:03:33Z"},
        "limit_7d": {"used_ratio": weekly, "reset_time": "2026-11-16T15:03:34Z"},
    }}


def test_live_ratios_profile_identity_and_existing_picker(home, monkeypatch):
    calls = []
    def request(url, headers, **kwargs):
        calls.append((url, headers))
        return payload(), None, "直连"
    monkeypatch.setattr(q, "_get_json_dual", request)
    monkeypatch.setattr(q.agent_runtime, "profile_specs", lambda: [
        SimpleNamespace(name="kp", runtime="kimi", home_path=home, label="")])
    source = q.collect()[0]
    assert source["verdict"] == "满"
    assert source["session_percent"] == 34 and source["weekly_percent"] == 100
    assert source["weekly_reset"] == "11-16 23:03"
    assert source["session_reset_at"] == q._reset_epoch("2026-11-12T07:03:33Z")
    assert calls == [("https://api.kimi.com/coding/v1/usages", {
        "Authorization": "Bearer fixture-token", "Accept": "application/json"})]
    target = dict(source, profile="kp2", verdict="够用", weekly_percent=20)
    cross_runtime = dict(target, profile="cxp", runtime="codex", weekly_percent=0)
    monkeypatch.setattr(q, "auto_failover_profiles", lambda: frozenset({"kp", "kp2", "cxp"}))
    monkeypatch.setattr(q.agent_runtime, "_profile_document", lambda: {"profiles": {}})
    assert q.pick([source, target, cross_runtime], exclude=["kp"], source=source) == target
    assert q.pick([source, dict(target, verdict="问不到"), cross_runtime],
                  exclude=["kp"], source=source) is None


@pytest.mark.parametrize("data", [
    {}, {"usages": {}}, {"usages": {"limit_7d": {"used_ratio": 0}}},
    payload(None), payload(True), payload("NaN"), payload(-1), payload(float("inf")),
    payload(0, []), {"usages": None}, [],
])
def test_missing_or_invalid_windows_never_mean_free_capacity(home, monkeypatch, data):
    monkeypatch.setattr(q, "_get_json_dual", lambda *a, **kw: (data, None, "直连"))
    result = q._kimi_quota(home)
    assert result["status"] == "unknown"
    assert q._verdict(result) == "问不到"


def test_numeric_string_zero_is_valid(home, monkeypatch):
    monkeypatch.setattr(q, "_get_json_dual", lambda *a, **kw: (payload("0", "0.2"), None, "直连"))
    result = q._kimi_quota(home)
    assert result["session_percent"] == 0 and result["weekly_percent"] == 20
    assert q._verdict(result) == "够用"


@pytest.mark.parametrize("total,code,expected", [(1, 1, "满"), (0.2, 1, "满"), (0.2, 0.4, "够用")])
def test_monthly_plan_is_not_reported_as_zero_weekly_usage(home, monkeypatch, total, code, expected):
    data = {"usages": {"limit_5h": {"used_ratio": 0},
                       "limit_month_total": {"used_ratio": total},
                       "limit_month_code": {"used_ratio": code}}}
    monkeypatch.setattr(q, "_get_json_dual", lambda *a, **kw: (data, None, "直连"))
    result = q._kimi_quota(home)
    assert q._verdict(result) == expected
    assert result["weekly_percent"] is None
    assert result["monthly_percent"] == max(total, code) * 100


@pytest.mark.parametrize("error", ["HTTP 401 fixture-token", "Timeout fixture-token", "HTTP 403 fixture-token"])
def test_query_failure_does_not_expose_error_bodies_or_choose_account(home, monkeypatch, error):
    monkeypatch.setattr(q, "_get_json_dual", lambda *a, **kw: (None, error, None))
    monkeypatch.setattr(q, "_kimi_native_usage", lambda home: (None, "原生查询失败"))
    result = q._kimi_quota(home)
    assert q._verdict(result) == "问不到"
    assert "fixture-token" not in json.dumps(result)


def test_expired_token_uses_native_refresh_without_treating_it_as_free(home, monkeypatch):
    monkeypatch.setattr(q, "_get_json_dual", lambda *a, **kw: (None, "直连: 凭据过期(401)", None))
    calls = []
    monkeypatch.setattr(q, "_kimi_native_usage", lambda h: calls.append(h) or (payload(), None))
    result = q._kimi_quota(home)
    assert calls == [home] and q._verdict(result) == "满"
    assert result["route"] == "原生登录刷新"


def test_native_refresh_can_finish_even_if_its_usage_fetch_loses_network(home, monkeypatch):
    calls = []
    def get(url, headers, **kwargs):
        calls.append(headers["Authorization"])
        return ((None, "HTTP 401", None) if len(calls) == 1 else (payload(), None, "直连"))
    def refresh(h):
        (h / "credentials" / "kimi-code.json").write_text(
            json.dumps({"access_token": "rotated-fixture-token"}), encoding="utf-8")
        return None, "原生查询网络失败"
    monkeypatch.setattr(q, "_get_json_dual", get)
    monkeypatch.setattr(q, "_kimi_native_usage", refresh)
    assert q._verdict(q._kimi_quota(home)) == "满"
    assert calls == ["Bearer fixture-token", "Bearer rotated-fixture-token"]


@pytest.mark.parametrize("ok", [True, False])
def test_native_usage_refresh_is_isolated_authenticated_and_always_stopped(home, monkeypatch, ok):
    calls = []
    class Process:
        stopped = False
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))
            port = args[-1]
            self.stdout = io.StringIO(f"Web UI: http://127.0.0.1:{port}/?token=private-local-token\n")
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, **kw):
            return 0
    processes = []
    def spawn(*a, **kw):
        p = Process(*a, **kw); processes.append(p); return p
    class Opener:
        def open(self, request, **kwargs):
            assert request.headers["Authorization"] == "Bearer private-local-token"
            assert request.full_url.endswith("/api/v1/oauth/usage")
            assert request.full_url.startswith("http://127.0.0.1:")
            if not ok:
                raise TimeoutError("secret body must not escape")
            return io.BytesIO(json.dumps({"data": {"kind": "ok", "quota": {"usages": {
                "limit5h": {"usedRatio": 0}, "limit7d": {"usedRatio": 1}}}}}).encode())
    monkeypatch.setenv("KIMI_CODE_HOME", "wrong-home")
    monkeypatch.setenv("KIMI_CODE_CONFIG", "wrong-config")
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", "wrong-bot")
    monkeypatch.setattr(q.shutil, "which", lambda _: "native-kimi")
    monkeypatch.setattr(q.subprocess, "Popen", spawn)
    monkeypatch.setattr(q, "_opener", lambda proxy: Opener())
    data, error = q._kimi_native_usage(home)
    assert processes[0].stopped
    args, kwargs = calls[0]
    assert args[:6] == ["native-kimi", "web", "--no-open", "--host", "127.0.0.1", "--port"]
    assert kwargs["env"]["KIMI_CODE_HOME"] == str(home)
    assert "KIMI_CODE_CONFIG" not in kwargs["env"] and "FEISHU_BRIDGE_SESSION" not in kwargs["env"]
    if ok:
        assert data["usages"]["limit_7d"]["used_ratio"] == 1 and error is None
    else:
        assert data is None and "secret" not in error


@pytest.mark.parametrize("old,new", [
    ("https://api.kimi.com/coding/v1", "https://api.kimi.com.example.org/coding/v1"),
    ('provider = "managed:kimi-code"', 'provider = "other"'),
    ('key = "oauth/kimi-code"', 'key = "oauth/other"'),
])
def test_wrong_provider_never_receives_saved_credentials(home, monkeypatch, old, new):
    path = home / "config.toml"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    monkeypatch.setattr(q, "_get_json_dual", lambda *a, **kw: pytest.fail("must not send credentials"))
    assert q._verdict(q._kimi_quota(home)) == "问不到"
