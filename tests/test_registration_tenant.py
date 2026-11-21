import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import register_feishu_app as register
import registration_tenant as tenant


class RegistrationTenantTests(unittest.TestCase):
    registry = {"machines": {"tuf19": {"hostname": "test-laptop"}},
                "tenants": [{"kind": "enterprise", "label": "测试公司"}]}

    def target(self, bot, name=None, host="test-runner"):
        return tenant.fleet_target(bot, name, self.registry, host)

    def test_all_five_machines_and_conflicting_names(self):
        for machine in ("TB26", "TX26", "TUF19", "TB24", "TB25"):
            expected = "enterprise" if machine in ("TB26", "TX26") else "personal"
            self.assertEqual(self.target(machine + "_3D-model")["kind"], expected)
            self.assertEqual(self.target(None, machine.lower() + "-agent")["kind"], expected)
        self.assertEqual(self.target("agent", host="TEST-LAPTOP")["kind"], "personal")
        self.assertIsNone(self.target("tb260-demo"))
        with self.assertRaisesRegex(ValueError, "不同机器"):
            self.target("tx26-demo", "tuf19-demo")

    def test_wrong_console_never_gets_launcher(self):
        target = self.target("tuf19-test")
        stop = mock.Mock(side_effect=[None, RuntimeError("test stop")])
        browser = tenant.RegistrationBrowser(target, stop)
        company = {"path": "/app", "organization": "测试公司", "window": 7}
        with mock.patch.object(tenant, "browser_pages", return_value=[company]) as read, \
                mock.patch.object(tenant.time, "sleep"), \
                mock.patch.object(tenant.webbrowser, "open_new"), \
                self.assertRaisesRegex(RuntimeError, "test stop"):
            browser.select_console()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(read.call_args.kwargs["window"], 7)
        self.assertTrue(read.call_args.kwargs["show_picker"])
        self.assertNotIn("url", read.call_args.kwargs)

    def test_same_window_checked_again_and_returned_id_verified(self):
        target = self.target("tb24-test")
        browser = tenant.RegistrationBrowser(target, lambda: None)
        page = {"path": "/app", "organization": "飞书个人版", "window": 9}
        launcher = "https://open.feishu.cn/page/launcher?user_code=TEST"
        with mock.patch.object(tenant, "browser_pages", side_effect=[[page], []]) as read:
            browser.select_console()
            with self.assertRaisesRegex(tenant.RegistrationTransportError, "已改变"):
                browser.open_launcher(launcher)
            read.assert_called_with(window=9, from_path="/app", organization="飞书个人版", url=launcher)
        for org in ("测试公司", "飞书个人版"):
            other = {"window": 2, "path": "/app/cli_other/baseinfo", "organization": "飞书个人版"}
            exact = dict(page, path="/app/cli_new/baseinfo", organization=org)
            with mock.patch.object(tenant, "browser_pages", return_value=[other, exact]):
                if org == "测试公司":
                    with self.assertRaises(tenant.TenantMismatch):
                        browser.verify_app("cli_new")
                else:
                    self.assertEqual(browser.verify_app("cli_new")["app_id"], "cli_new")

    def test_conflicting_explicit_tenant_and_wrong_return_never_install(self):
        args = ["register", "--name", "TUF19-example", "--bot", "tuf19-example",
                "--profile", "fixture", "--capability", "core", "--no-monitor"]
        with contextlib.ExitStack() as stack:
            def patch(obj, key, **kwargs):
                return stack.enter_context(mock.patch.object(obj, key, **kwargs))
            patch(register.agent_runtime, "profile_spec", return_value=mock.Mock(runtime="codex"))
            patch(register.agent_runtime, "profile_doctor", return_value={"ok": True})
            patch(tenant, "fleet_target", return_value=self.target("tuf19-example"))
            patch(register, "_recovery_app_id", return_value=None)
            browser = patch(tenant, "RegistrationBrowser").return_value
            grant = patch(register, "_run_device_grant", return_value={"client_id": "cli_new", "client_secret": "test"})
            persist = patch(register, "_persist_registration")
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            with mock.patch.object(sys, "argv", args + ["--tenant-kind", "enterprise"]), self.assertRaises(SystemExit):
                register.main()
            grant.assert_not_called()
            browser.verify_app.side_effect = tenant.TenantMismatch("应用属于错误组织")
            with mock.patch.object(sys, "argv", args), self.assertRaises(SystemExit):
                register.main()
            browser.select_console.assert_called_once()
            browser.verify_app.assert_called_once_with("cli_new")
            persist.assert_not_called()

    def test_rejected_cloud_app_is_not_automatically_resumed(self):
        previous = {"app_id": "cli_wrong", "rejected_tenant_app_id": "cli_wrong"}
        with mock.patch.object(register.registration_monitor, "get_job", return_value=previous), \
                mock.patch.object(register.bridge_scope_audit, "_env_val", return_value=None):
            self.assertIsNone(register._recovery_app_id(None, "APP_ID", "tb24-test"))
            with self.assertRaisesRegex(ValueError, "错误租户"):
                register._recovery_app_id("cli_wrong", "APP_ID", "tb24-test")


if __name__ == "__main__":
    unittest.main()
