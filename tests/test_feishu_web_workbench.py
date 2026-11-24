import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import feishu_web_workbench as wb  # noqa: E402
import send_as_user_web as web_send  # noqa: E402


class TestFeishuWebWorkbench(unittest.TestCase):
    def test_resolve_domain_urls_formats_tenant_host(self):
        urls = wb.resolve_domain_urls("okr", "https://demo.feishu.cn/")
        self.assertEqual(urls[0], "https://demo.feishu.cn/okr/")
        self.assertIn("attendance", wb.DOMAIN_ROUTES)
        self.assertIn("mail", wb.DOMAIN_ROUTES)

    def test_detect_login_wall(self):
        self.assertTrue(wb.detect_login_wall("请扫码登录飞书", "https://n8r2cr07gk.feishu.cn/mail/"))
        self.assertTrue(wb.detect_login_wall("welcome", "https://accounts.feishu.cn/login"))
        self.assertFalse(wb.detect_login_wall("收件箱 Inbox 写邮件", "https://n8r2cr07gk.feishu.cn/mail/"))

    def test_attendance_and_okr_dry_run_never_launch_browser(self):
        with mock.patch.object(web_send, "discover_chrome_direct_browser", return_value={"ok": True, "browser_id": "direct_1"}):
            with mock.patch.object(web_send, "run_cmd") as m_run:
                r1 = wb.read_web_domain(domain="attendance", apply=False)
                r2 = wb.read_web_domain(domain="okr", apply=False)
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
                res = wb.send_mail_via_web(to="a@obsbot.com", subject="测试", body="正文", apply=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "DRY_RUN")
        self.assertFalse(res["sent"])
        m_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
