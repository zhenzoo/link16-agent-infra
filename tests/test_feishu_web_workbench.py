import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import feishu_web_workbench as wb  # noqa: E402
from types import SimpleNamespace

web_send = SimpleNamespace(discover_chrome_direct_browser=None, browser_act_bin=lambda: 'browser-act', run_cmd=None)


class TestFeishuWebWorkbench(unittest.TestCase):
    def test_private_browser_and_tenant_are_optional_and_reported_separately(self):
        with mock.patch.object(wb, "web_send", None):
            missing_host = wb.read_web_domain(domain="attendance", host="")
            missing_adapter = wb.read_web_domain(domain="attendance", host="demo.feishu.cn", browser_id="direct", apply=True)
        self.assertIn("租户域名", missing_host["error"])
        self.assertFalse(missing_adapter["ok"])
        self.assertEqual(missing_adapter["error"], wb.BROWSER_UNAVAILABLE)

    def setUp(self):
        patcher = mock.patch.object(wb, 'web_send', web_send)
        patcher.start()
        self.addCleanup(patcher.stop)
    def test_resolve_domain_urls_formats_tenant_host(self):
        urls = wb.resolve_domain_urls("okr", "https://demo.feishu.cn/")
        self.assertEqual(urls[0], "https://demo.feishu.cn/okr/")
        self.assertIn("attendance", wb.DOMAIN_ROUTES)
        self.assertIn("mail", wb.DOMAIN_ROUTES)

    def test_detect_login_wall(self):
        self.assertTrue(wb.detect_login_wall("请扫码登录飞书", "https://demo.feishu.cn/mail/"))
        self.assertTrue(wb.detect_login_wall("welcome", "https://accounts.feishu.cn/login"))
        self.assertFalse(wb.detect_login_wall("收件箱 Inbox 写邮件", "https://demo.feishu.cn/mail/"))

    def test_live_leave_page_requires_records_and_balance_and_does_not_verify_punches(self):
        url = wb.resolve_domain_urls("attendance", "demo.feishu.cn")[0]
        for snap, expected in [
            ({"url": url, "text": "无法访问此页面，请联系管理员。"}, False),
            ({"url": url, "text": "工作台\n审批\n自助服务"}, False),
            ({"url": "https://demo.feishu.cn/next/messenger/", "text": "假期余额\n休假记录"}, False),
            ({"url": url, "text": "休假记录\n申请休假\n假期余额\n年假"}, True),
        ]:
            with self.subTest(snap=snap), mock.patch.object(wb, "_open_web_session", return_value=(True, "")), \
                 mock.patch.object(wb, "_capture_hydrated_page", return_value=snap), mock.patch.object(web_send, "run_cmd") as cmd:
                result = wb.read_web_domain(domain="attendance", host="demo.feishu.cn", browser_id="fixture", apply=True)
                self.assertEqual(result["ok"], expected)
                if expected:
                    self.assertEqual(result["coverage"], {"leave_balance": True, "leave_records": True, "punch_records": False})
                cmd.assert_called_once()
                self.assertIn("close", cmd.call_args.args[0])

    def test_empty_okr_is_valid_but_login_and_error_pages_are_not_mail(self):
        urls = wb.resolve_domain_urls("okr", "demo.feishu.cn")
        self.assertEqual(wb.read_coverage("okr", {"url": urls[0]+"user/42", "text": "我的 OKR\n暂无 OKR"}, urls),
                         {"okr_view": True})
        mail = wb.resolve_domain_urls("mail", "demo.feishu.cn")
        for text in ["扫码登录", "无法访问", "空白占位"]:
            self.assertEqual(wb.read_coverage("mail", {"url": mail[0], "text": text}, mail), {})

    def test_attendance_and_okr_dry_run_never_launch_browser(self):
        with mock.patch.object(web_send, "discover_chrome_direct_browser", return_value={"ok": True, "browser_id": "direct_1"}):
            with mock.patch.object(web_send, "run_cmd") as m_run:
                r1 = wb.read_web_domain(domain="attendance", host="demo.feishu.cn", apply=False)
                r2 = wb.read_web_domain(domain="okr", host="demo.feishu.cn", apply=False)
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["mode"], "DRY_RUN")
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["mode"], "DRY_RUN")
        m_run.assert_not_called()

    def test_mail_send_defaults_to_dry_run_and_validates_required_fields(self):
        err = wb.send_mail_via_web(to="", subject="hi", body="hello", apply=False)
        self.assertFalse(err["ok"])
        with mock.patch.object(web_send, "discover_chrome_direct_browser", return_value={"ok": True, "browser_id": "direct_1"}):
            with mock.patch.object(web_send, "run_cmd") as m_run:
                res = wb.send_mail_via_web(to="a@example.com", subject="测试", body="正文", host="demo.feishu.cn", apply=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "DRY_RUN")
        self.assertFalse(res["sent"])
        m_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
