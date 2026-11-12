"""Creation must not depend on vendor defaults or leave ungoverned success."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import docio_cli as cli
import docio_create as create


POLICY = {"owner_id": "owner", "group_id": "group", "visibility": "tenant"}
ACCESS = {"checks": {"owner": True, "link": True, "group_edit": True}, "receipt": "access.json"}


def result(data=None, ok=True):
    return subprocess.CompletedProcess([], 0 if ok else 1, json.dumps({"ok": ok, "data": data or {}}), "")


@pytest.fixture
def harness(tmp_path):
    args = SimpleNamespace(bot=None, kind="sheet", title="spec", spec=None, state=str(tmp_path / "job"),
                           external_reason="", apply=True, resume=False)
    fake = SimpleNamespace(resolve_bot=lambda _: "current", _json_out=cli._json_out,
        run_lark=Mock(side_effect=[result(), result({"spreadsheet_token": "newtoken", "url": "https://tenant.feishu.cn/sheets/newtoken"})]))
    with patch("send_feishu_msg._creds_for", return_value=("current-app", "private-secret")), \
         patch.object(create.rest, "tenant_token", return_value="private-token"), \
         patch.object(create.access, "resolve_policy", return_value=POLICY) as policy, \
         patch.object(create.access, "apply_new", return_value=ACCESS) as apply, \
         patch.object(create.access, "verify_delivery", return_value=ACCESS) as verify, \
         patch.object(create.access, "snapshot", side_effect=lambda token, resource, kind:
                      {"metadata": {"url": f"https://tenant.feishu.cn/sheets/{resource}"}}):
        yield args, fake, policy, apply, verify


def test_creation_pins_bot_and_returns_only_after_fresh_three_way_check(harness):
    args, fake, policy, apply, verify = harness
    output = create.execute(args, fake)
    assert output["ok"] and all(output["checks"].values())
    assert all(c.kwargs["profile"] == "current" for c in fake.run_lark.call_args_list)
    assert all(c.args[0][-2:] == ["--as", "bot"] or "--dry-run" in c.args[0] for c in fake.run_lark.call_args_list)
    apply.assert_called_once_with("private-token", "newtoken", "sheet", POLICY)
    verify.assert_called_once()
    record = json.loads((Path(args.state) / "create.json").read_text())
    assert record["status"] == "verified"
    assert "private-secret" not in json.dumps(record) and "private-token" not in json.dumps(record)


def test_missing_owner_or_invalid_tenant_group_prevents_even_vendor_create(harness):
    args, fake, policy, *_ = harness
    policy.side_effect = create.access.AccessPolicyError("missing owner/group")
    with pytest.raises(create.access.AccessPolicyError):
        create.execute(args, fake)
    fake.run_lark.assert_not_called()
    assert not (Path(args.state) / "create.json").exists()


def test_acl_failure_never_reports_success_and_resume_never_creates_again(harness, capsys):
    args, fake, _, apply, verify = harness
    apply.side_effect = create.access.AccessPolicyError("group rejected")
    assert create.run(args, fake) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    record = json.loads((Path(args.state) / "create.json").read_text())
    assert record["resource"]["token"] == "newtoken" and record["status"] == "access_incomplete"
    apply.side_effect = None
    args.apply, args.resume = False, True
    fake.run_lark.reset_mock()
    assert create.execute(args, fake)["ok"]
    fake.run_lark.assert_not_called()


def test_unknown_post_result_and_duplicate_invocation_never_replay(harness):
    args, fake, *_ = harness
    fake.run_lark.side_effect = [result(), subprocess.TimeoutExpired("vendor", 300)]
    with pytest.raises(ValueError, match="不确定"):
        create.execute(args, fake)
    fake.run_lark.reset_mock()
    with pytest.raises(ValueError, match="已有记录"):
        create.execute(args, fake)
    args.resume, args.apply = True, False
    with pytest.raises(ValueError, match="结果不确定"):
        create.execute(args, fake)
    fake.run_lark.assert_not_called()


def test_partial_content_is_governed_but_never_called_complete(harness):
    args, fake, _, apply, _ = harness
    fake.run_lark.side_effect = [result(), result({"spreadsheet_token": "partial"}, ok=False)]
    with pytest.raises(ValueError, match="未完整成功"):
        create.execute(args, fake)
    apply.assert_called_once()
    assert json.loads((Path(args.state) / "create.json").read_text())["status"] == "content_incomplete"


def test_failed_final_readback_is_not_a_success(harness):
    args, fake, _, _, verify = harness
    verify.side_effect = create.access.AccessPolicyError("owner drift")
    with pytest.raises(ValueError, match="owner drift"):
        create.execute(args, fake)
    assert json.loads((Path(args.state) / "create.json").read_text())["status"] == "access_incomplete"


def test_dry_run_and_identity_override_payload_never_create(harness, tmp_path):
    args, fake, _, apply, _ = harness
    args.apply = False
    assert create.execute(args, fake)["dry_run"]
    assert len(fake.run_lark.call_args_list) == 1 and "--dry-run" in fake.run_lark.call_args.args[0]
    apply.assert_not_called()
    args.spec = str(tmp_path / "bad.json")
    Path(args.spec).write_text('{"profile":"other"}')
    fake.run_lark.reset_mock()
    with pytest.raises(ValueError, match="不可由spec覆盖"):
        create.execute(args, fake)
    fake.run_lark.assert_not_called()


def test_resume_different_input_or_bot_is_rejected(harness):
    args, fake, *_ = harness
    create.execute(args, fake)
    args.apply, args.resume = False, True
    args.title = "different"
    fake.run_lark.reset_mock()
    with pytest.raises(ValueError, match="不一致"):
        create.execute(args, fake)
    fake.run_lark.assert_not_called()


def test_base_resource_uses_same_acl_gate(harness):
    args, fake, _, apply, _ = harness
    args.kind = "bitable"
    fake.run_lark.side_effect = [result(), result({"app": {"app_token": "newbase", "url": "https://tenant.feishu.cn/base/newbase"}})]
    assert create.execute(args, fake)["type"] == "bitable"
    assert fake.run_lark.call_args.args[0][:2] == ["base", "+base-create"]
    apply.assert_called_once_with("private-token", "newbase", "bitable", POLICY)


def test_vendor_defaults_and_foreign_credentials_cannot_override_current_bot():
    env = {"LARKSUITE_CLI_PROFILE": "other", "LARKSUITE_CLI_APP_ID": "foreign",
           "LARKSUITE_CLI_TENANT_ACCESS_TOKEN": "foreign-token", "LARKSUITE_CLI_USER_ACCESS_TOKEN": "foreign-user"}
    with patch.dict(cli.os.environ, env), patch.object(cli, "roster", return_value=[("current", "APP", "SECRET")]), \
         patch.object(cli.audit, "_env_val", side_effect=lambda key: {"APP": "current-app", "SECRET": "current-secret"}[key]), \
         patch.object(cli, "_tenant_token_with_retry", return_value="current-token"), \
         patch.object(cli, "lark_cli", return_value=["node", "vendor.js"]), \
         patch.object(cli.subprocess, "run", return_value=result()) as run:
        cli.run_lark(["sheets", "+workbook-create", "--as", "bot", "--title", "x"], profile="current")
    child = run.call_args.kwargs["env"]
    assert child["LARKSUITE_CLI_APP_ID"] == "current-app"
    assert child["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "current-token"
    assert "LARKSUITE_CLI_PROFILE" not in child and "LARKSUITE_CLI_USER_ACCESS_TOKEN" not in child


def test_bridge_creation_rejects_explicit_other_bot(harness):
    args, fake, policy, *_ = harness
    args.bot = "other"
    fake.resolve_bot = lambda explicit: explicit or "current"
    with patch.dict(create.os.environ, {"FEISHU_BRIDGE_SESSION": "current"}):
        with pytest.raises(ValueError, match="当前会话"):
            create.execute(args, fake)
    policy.assert_not_called()
    fake.run_lark.assert_not_called()
