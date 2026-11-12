import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_scope_audit as audit
import feishu_docs
import registration_monitor as monitor


def catalog():
    return {"snapshot": {"tenant_kind": "enterprise"}, "scopes": [
        {"scope": "both:read", "identity_levels": {"tenant": 3, "user": 3}},
        {"scope": "contact:user:search", "identity_levels": {"user": 3}},
        {"scope": "mixed:read", "identity_levels": {"tenant": 4, "user": 3}},
        {"scope": "admin:write", "identity_levels": {"user": 4}},
        {"scope": "unknown:read", "identity_levels": {"tenant": 0}},
    ]}


def test_same_scope_tenant_grant_cannot_satisfy_user_grant():
    with patch.object(audit, "scope_catalog", return_value=catalog()):
        plan = audit.registration_scope_plan(granted={("both:read", "tenant")}, tenant_kind="enterprise")
    assert plan["tenant"] == ()
    assert plan["user"] == ("both:read", "contact:user:search", "mixed:read")


def test_approval_and_unknown_never_enter_either_identity_plan():
    with patch.object(audit, "scope_catalog", return_value=catalog()):
        plan = audit.registration_scope_plan(tenant_kind="enterprise")
    assert plan["tenant"] == ("both:read",)
    assert "admin:write" not in plan["user"]
    assert "mixed:read" in plan["user"]


def test_enterprise_rejects_personal_or_untyped_catalog():
    for bad in ({"snapshot": {"tenant_kind": "personal"}},
                {"snapshot": {"tenant_kind": "enterprise"}, "scopes": [{"scope": "old:read", "level": 3}]}):
        with patch.object(audit, "scope_catalog", return_value=bad), pytest.raises(ValueError):
            audit.registration_scope_plan(tenant_kind="enterprise")


def test_personal_does_not_expand_to_enterprise_catalog():
    plan = audit.registration_scope_plan(tenant_kind="personal")
    assert plan["user"] == ()
    assert "contact:user:search" not in plan["tenant"]
    assert plan["tenant"] == audit.registration_scopes()


def test_user_scope_links_preserve_identity_and_length_limit():
    scopes = ["scope:" + str(i) + ":read" for i in range(80)]
    links = feishu_docs.auth_urls("cli_test", scopes, token_type="user")
    assert len(links) > 1
    assert all(len(x) <= feishu_docs.AUTH_URL_MAX_CHARS and x.endswith("token_type=user") for x in links)
    assert [s for url in links for s in url.split("?q=")[1].split("&")[0].split(",")] == scopes


def test_grants_keep_identity_and_discard_ungranted_rows():
    rows = [{"scope_name": "same", "scope_type": t, "grant_status": status}
            for t, status in [("tenant", 1), ("user", 0)]]
    with patch.object(audit, "_token", return_value=("test", None)), \
         patch.object(audit, "_req", return_value={"code": 0, "data": {"scopes": rows}}):
        pairs, err = audit.granted_scope_pairs("ID", "SECRET")
    assert pairs == {("same", "tenant")} and err is None


def test_monitor_uses_enterprise_plan_and_remains_missing_for_user_side():
    state = {"app_id": "cli_test", "bot": "test", "capabilities": [],
             "registration_context": {"tenant_kind": "enterprise"}}
    with patch.object(audit, "scope_catalog", return_value=catalog()), \
         patch.object(audit, "granted_scope_pairs", return_value=({("both:read", "tenant")}, None)), \
         patch.object(audit, "_env_val", return_value="cli_test"):
        urls, _, _, info = monitor.permission_review_link(state)
        checked = monitor._permission_check(state)
    assert info["by_identity"]["tenant"] == ()
    assert all(x.endswith("token_type=user") for x in urls)
    assert checked["status"] == "missing" and checked["baseline_ok"] is False
    assert checked["user_authorization"].startswith("separate_check_required")


@pytest.mark.parametrize("status,verified,scope,ready", [
    ("ready", True, "contact:user:search", True),
    ("missing", False, "contact:user:search", False),
    ("needs_refresh", False, "contact:user:search", False),
    ("ready", True, "docx:document", False),
])
def test_user_scope_without_valid_identity_does_not_pass(status, verified, scope, ready):
    import docio_cli
    data = {"identities": {"user": {"status": status, "verified": verified, "scope": scope, "openId": "owner"}}}
    with patch.object(docio_cli, "run_lark", return_value={}), patch.object(docio_cli, "_json_out", return_value=data), \
         patch.object(docio_cli, "owner_open_id", return_value="owner"):
        result = audit.user_identity_status("exact-bot")
    assert result["personnel_ready"] is ready


def test_valid_user_token_for_another_person_does_not_pass_owner_authorization():
    import docio_cli
    data = {"identities": {"user": {"status": "ready", "verified": True,
                                    "scope": "contact:user:search", "openId": "someone-else"}}}
    with patch.object(docio_cli, "run_lark", return_value={}), patch.object(docio_cli, "_json_out", return_value=data), \
         patch.object(docio_cli, "owner_open_id", return_value="owner"):
        result = audit.user_identity_status("exact-bot")
    assert not result["owner_matches"] and not result["personnel_ready"]


def test_live_catalog_includes_people_but_not_send_as_user():
    plan = audit.registration_scope_plan(tenant_kind="enterprise")
    assert "contact:user:search" in plan["user"]
    assert "contact:user.basic_profile:readonly" in plan["user"]
    assert "contact:user.basic_profile:readonly" not in plan["tenant"]
    assert "contact:user.base:readonly" in plan["tenant"]
    assert "im:message.send_as_user" not in plan["user"]


def test_unexplained_catalog_level_is_not_reported_as_confirmed_approval(capsys):
    data = {"scopes": [{"scope": "unknown:read", "identity_levels": {"user": 10}}]}
    with patch.object(audit, "scope_catalog", return_value=data):
        audit._print_levels(["unknown:read"], True, "user")
    row = json.loads(capsys.readouterr().out)[0]
    assert row["self_serve"] is False
    assert "未知" in row["verdict"] and "管理员审批" not in row["verdict"]
