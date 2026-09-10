#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLAN-1000 S1 · 2026-09-08 机器 3050 装机暴露的机械缺口，逐条离线复现。

每个用例名对应 PLAN-1000 §1 的一个 Step；夹具全在 tmp 目录，不碰真实 home / registry / 名册。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agent_runtime  # noqa: E402
import preflight  # noqa: E402
import windows_bootstrap  # noqa: E402
import register_feishu_app  # noqa: E402
import registration_monitor  # noqa: E402


def spec(name="claude-work", runtime="claude", home=None):
    return agent_runtime.ProfileSpec(name=name, runtime=runtime, home=str(home or "~/.nowhere"),
                                     launcher="launch-sh")


class S11FirstBotOauthLinkTests(unittest.TestCase):
    """S1.1 首只 bot 没有投递通道时，链接留在终端、注册继续，不再抛 oauth_link_delivery_failed。"""

    def test_no_notify_bot_returns_false_without_raising(self):
        calls = []
        with mock.patch.object(registration_monitor, "get_job", return_value={"bot": "x", "notify_bot": None}), \
             mock.patch.object(registration_monitor, "notify_oauth_link",
                               side_effect=lambda *a, **k: calls.append(a) or {"ok": False, "error": "未绑定 notify_bot"}):
            delivered = register_feishu_app._deliver_oauth_link("job-1", {"url": "https://x", "expire_in": 60})
        self.assertFalse(delivered)
        self.assertEqual(len(calls), 1, "无 notify_bot 只记一次 job 状态，不做三次重试")

    def test_bound_bot_delivery_failure_is_not_fatal(self):
        with mock.patch.object(registration_monitor, "get_job", return_value={"bot": "x", "notify_bot": "tb26-link16"}), \
             mock.patch.object(registration_monitor, "notify_oauth_link", return_value={"ok": False, "error": "inject failed"}), \
             mock.patch.object(register_feishu_app.time, "sleep", lambda *_: None):
            delivered = register_feishu_app._deliver_oauth_link("job-1", {"url": "https://x", "expire_in": 60})
        self.assertFalse(delivered)

    def test_bound_bot_delivery_success(self):
        with mock.patch.object(registration_monitor, "get_job", return_value={"bot": "x", "notify_bot": "tb26-link16"}), \
             mock.patch.object(registration_monitor, "notify_oauth_link", return_value={"ok": True}):
            self.assertTrue(register_feishu_app._deliver_oauth_link("job-1", {"url": "https://x"}))


class S17RegistryStubTests(unittest.TestCase):
    """S1.7 agent-registry.json 空骨架（一行 `"agents": []`）也能追加。"""

    def _run(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Path(tmp) / "agent-registry.local.json"
            reg.write_text(text, encoding="utf-8")
            import bridge_env
            with mock.patch.object(bridge_env, "writable_registry_path", return_value=reg), \
                 mock.patch.object(register_feishu_app, "_bot_identity", return_value=("ou_1", "tb99-demo")):
                stub = register_feishu_app.append_registry_stub("cli_1", "sec", "tb99-demo", "tb99-demo")
            return stub, json.loads(reg.read_text(encoding="utf-8"))

    def test_one_line_empty_agents_array(self):
        stub, data = self._run('{"machines": {"tb99": {"hostname": "X"}}, "agents": []}\n')
        self.assertIsNotNone(stub)
        self.assertEqual([a["name"] for a in data["agents"]], ["tb99-demo"])
        self.assertEqual(data["agents"][0]["machine"], "tb99")
        self.assertEqual(data["agents"][0]["open_id"], "ou_1")

    def test_agents_not_last_array_still_appends_to_agents(self):
        stub, data = self._run(json.dumps({
            "machines": {"tb99": {}}, "agents": [{"name": "old"}], "tenants": [{"name": "t"}]
        }, indent=2))
        self.assertEqual([a["name"] for a in data["agents"]], ["old", "tb99-demo"])
        self.assertEqual(data["tenants"], [{"name": "t"}])

    def test_idempotent(self):
        stub, data = self._run(json.dumps({"machines": {}, "agents": [{"name": "tb99-demo"}]}))
        self.assertEqual(len(data["agents"]), 1)


class S12ProfileLoginStateTests(unittest.TestCase):
    """S1.2 登录态 = 凭据文件证据；从没登录过的 profile 在 preflight/桥 spawn 前就被点名。"""

    def test_missing_home(self):
        self.assertEqual(agent_runtime.profile_login_state(spec())["status"], "missing")

    def test_claude_never_logged_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude.json").write_text('{"userID": "u"}', encoding="utf-8")
            state = agent_runtime.profile_login_state(spec(home=tmp))
        self.assertEqual(state["status"], "missing")
        self.assertIn("claude-work", state["fix"]["powershell"])

    def test_claude_oauth_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "a@b"}}', encoding="utf-8")
            self.assertEqual(agent_runtime.profile_login_state(spec(home=tmp))["status"], "ok")

    def test_claude_credentials_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".credentials.json").write_text("{}", encoding="utf-8")
            self.assertEqual(agent_runtime.profile_login_state(spec(home=tmp))["status"], "ok")

    def test_claude_third_party_endpoint_needs_no_oauth(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "launch.sh").write_text("export ANTHROPIC_AUTH_TOKEN=xxx\n", encoding="utf-8")
            self.assertEqual(agent_runtime.profile_login_state(spec(home=tmp))["status"], "ok")

    def test_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(agent_runtime.profile_login_state(spec("codex-work", "codex", tmp))["status"], "missing")
            (Path(tmp) / "auth.json").write_text("{}", encoding="utf-8")
            self.assertEqual(agent_runtime.profile_login_state(spec("codex-work", "codex", tmp))["status"], "ok")

    def test_kimi_is_unknown_not_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(agent_runtime.profile_login_state(spec("kimi-work", "kimi", tmp))["status"], "unknown")

    def test_profile_doctor_reports_login_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = spec(home=tmp)
            (Path(tmp) / "launch.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            with mock.patch.object(agent_runtime, "profile_spec", return_value=s):
                result = agent_runtime.profile_doctor("claude-work", check_execution_env=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["login"]["status"], "missing")

    def test_preflight_fails_only_for_profiles_the_bridge_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            used = spec("claude-work", home=tmp + "/a")
            idle = spec("ccw3", home=tmp + "/b")
            roster = Path(tmp) / "bridge-bots.local.json"
            roster.write_text(json.dumps({"bots": [{"name": "x", "profile": "claude-work"}]}), encoding="utf-8")
            with mock.patch.object(agent_runtime, "profile_specs", return_value=[used, idle]), \
                 mock.patch.object(agent_runtime, "machine_default_profile", side_effect=ValueError("none")), \
                 mock.patch.object(agent_runtime, "ROSTER_LOCAL_PATH", roster):
                result = preflight.check_profile_login()
            self.assertEqual(result.status, preflight.FAIL)
            self.assertIn("claude-work", result.detail)
            self.assertNotIn("ccw3", result.detail)
            roster.write_text(json.dumps({"bots": []}), encoding="utf-8")
            with mock.patch.object(agent_runtime, "profile_specs", return_value=[idle]), \
                 mock.patch.object(agent_runtime, "machine_default_profile", side_effect=ValueError("none")), \
                 mock.patch.object(agent_runtime, "ROSTER_LOCAL_PATH", roster):
                result = preflight.check_profile_login()
            self.assertEqual(result.status, preflight.WARN)


class S13BashOnPathTests(unittest.TestCase):
    """S1.3 桥开面板敲裸 bash：PATH 里必须能解析到 Git 的 bash，System32 的 WSL 启动器不算。"""

    def test_missing(self):
        with mock.patch.object(preflight, "_fresh_which", return_value=None), \
             mock.patch.object(preflight, "_git_bash_path", return_value=Path("C:/Users/u/AppData/Local/Programs/Git/bin/bash.exe")):
            r = preflight.check_bash_on_path()
        self.assertEqual(r.status, preflight.FAIL)
        self.assertIn("Programs", r.fix)

    def test_wsl_launcher_rejected(self):
        with mock.patch.object(preflight, "_fresh_which", return_value=r"C:\Windows\System32\bash.exe"), \
             mock.patch.object(preflight, "_git_bash_path", return_value=None):
            self.assertEqual(preflight.check_bash_on_path().status, preflight.FAIL)

    def test_git_bash_ok(self):
        with mock.patch.object(preflight, "_fresh_which", return_value=r"C:\Program Files\Git\usr\bin\bash.EXE"), \
             mock.patch.object(preflight, "_git_bash_path", return_value=None):
            self.assertEqual(preflight.check_bash_on_path().status, preflight.OK)

    def test_configure_user_path_plan_lists_missing_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_bin = Path(tmp) / "Git" / "bin"
            git_bin.mkdir(parents=True)
            (git_bin / "bash.exe").write_bytes(b"")
            with mock.patch.object(preflight, "_git_bash_path", return_value=git_bin / "bash.exe"), \
                 mock.patch.object(windows_bootstrap, "_user_env_value", return_value=r"C:\Windows;C:\Other"), \
                 mock.patch.object(os, "name", "nt"), \
                 mock.patch.object(Path, "home", return_value=Path(tmp)):
                row = windows_bootstrap.configure_user_path(apply=False)
            self.assertEqual(row["status"], "missing")
            self.assertIn(str(git_bin), row["detail"])
            with mock.patch.object(preflight, "_git_bash_path", return_value=git_bin / "bash.exe"), \
                 mock.patch.object(windows_bootstrap, "_user_env_value", return_value=f"C:\\Windows;{git_bin}"), \
                 mock.patch.object(os, "name", "nt"), \
                 mock.patch.object(Path, "home", return_value=Path(tmp)):
                self.assertEqual(windows_bootstrap.configure_user_path(apply=False)["status"], "ok")


class S14WmuxDefaultShellTests(unittest.TestCase):
    """S1.4 wmux 默认 Shell 不再永远 FAIL：不是 Git Bash 只 WARN；needs-gui 不算装机失败。"""

    def test_powershell_default_is_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wmux").mkdir()
            (Path(tmp) / "wmux" / "session.json").write_text(
                json.dumps({"defaultShell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"APPDATA": tmp}):
                r = preflight.check_wmux_default_shell()
        self.assertEqual(r.status, preflight.WARN)
        self.assertIn("bash", r.detail)

    def test_needs_gui_not_a_deployment_failure(self):
        post = [{"task": "wmux-default-shell", "status": "needs-gui"},
                {"task": "python-utf8", "status": "blocked"}]
        failures = windows_bootstrap.deployment_failures([], [], post)
        self.assertEqual(failures, ["post:python-utf8:blocked"])


class S15MsixLocalAppDataTests(unittest.TestCase):
    """S1.5 Claude 桌面版（MSIX）里 LOCALAPPDATA 被重定向，wmux 路径要从真实 AppData\\Local 推。"""

    def test_redirected_localappdata_uses_userprofile(self):
        env = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local",
               "USERPROFILE": r"C:\Users\u"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(windows_bootstrap._real_local_appdata(), str(Path(r"C:\Users\u") / "AppData" / "Local"))
            self.assertTrue(windows_bootstrap.inside_desktop_client())

    def test_plain_localappdata_untouched(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}), \
             mock.patch.object(Path, "cwd", return_value=Path(r"C:\repo")):
            self.assertEqual(windows_bootstrap._real_local_appdata(), r"C:\Users\u\AppData\Local")
            self.assertFalse(windows_bootstrap.inside_desktop_client())

    def test_wmux_executable_prefers_stable_shim_under_real_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "AppData" / "Local"
            (real / "wmux" / "app-3.51.0").mkdir(parents=True)
            (real / "wmux" / "wmux.exe").write_bytes(b"")
            (real / "wmux" / "app-3.51.0" / "wmux.exe").write_bytes(b"")
            env = {"LOCALAPPDATA": str(Path(tmp) / "AppData" / "Local" / "Packages" / "Claude_x" / "LocalCache" / "Local"),
                   "USERPROFILE": tmp}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(windows_bootstrap.wmux_executable(), real / "wmux" / "wmux.exe")


class S16RosterTemplateTests(unittest.TestCase):
    """S1.6 名册模板不再写死别台机器的 ccp/cxp。"""

    def test_template_has_no_foreign_profile_names(self):
        data = json.loads((ROOT / "feishu" / "bridge-bots.local.example.json").read_text(encoding="utf-8"))
        self.assertEqual(data["defaults"]["profiles"], {})
        text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn('"cxp"', text)
        self.assertNotIn('"ccp"', text)


class S19CliAliasTests(unittest.TestCase):
    """S1.9 3050 助手两次猜错的参数名现在是别名。"""

    def test_registration_monitor_job_id_alias(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        stop = sub.add_parser("cancel")
        stop.add_argument("--job", "--job-id", dest="job", required=True)
        self.assertEqual(parser.parse_args(["cancel", "--job-id", "j1"]).job, "j1")
        src = (ROOT / "feishu" / "registration_monitor.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"--job-id"'), 3)

    def test_service_installer_digest_alias(self):
        src = (ROOT / "feishu" / "service_installer.py").read_text(encoding="utf-8")
        self.assertIn('"--expect", "--digest", dest="expect"', src)


if __name__ == "__main__":
    unittest.main()
