import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import register_feishu_app as register
import registration_transport as transport


def response(payload, status=200):
    result = transport.requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    return result


class RegistrationTransportTests(unittest.TestCase):
    def test_real_sdk_keeps_same_device_code_across_transient_poll_failure(self):
        outcomes = [
            response({"supported_auth_methods": ["client_secret"]}),
            response({"device_code": "device-private", "interval": 0, "expires_in": 100,
                      "verification_uri_complete": "https://open.feishu.cn/page/launcher?user_code=private"}),
            transport.requests.Timeout("secret-in-error-url"),
            response({"error": "authorization_pending"}),
            response({"client_id": "cli_original", "client_secret": "secret-private"}),
        ]
        diagnostics = []
        with mock.patch.object(transport.requests, "post", side_effect=outcomes) as post, \
             mock.patch.object(transport.time, "sleep"), \
             mock.patch.object(register, "on_qr") as qr, \
             mock.patch.object(register.registration_monitor, "record_progress",
                               side_effect=lambda job, **data: diagnostics.append(data) or {}), \
             mock.patch.object(register.registration_monitor, "record_stage"), \
             mock.patch.object(register.registration_monitor, "notify_oauth_link", return_value={"ok": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            result = register._run_device_grant("test", "cli_original", "job-test")
        self.assertEqual(result["client_id"], "cli_original")
        self.assertEqual(result["client_secret"], "secret-private")
        payloads = [parse_qs(call.kwargs["data"]) for call in post.call_args_list]
        self.assertEqual([p["action"][0] for p in payloads], ["init", "begin", "poll", "poll", "poll"])
        self.assertEqual({p["device_code"][0] for p in payloads if "device_code" in p}, {"device-private"})
        query = parse_qs(urlparse(qr.call_args.args[0]["url"]).query)
        self.assertEqual(query["clientID"], ["cli_original"])
        self.assertNotIn("createOnly", query)
        self.assertTrue(all(call.kwargs["timeout"] == (10, 20) for call in post.call_args_list))
        raw = json.dumps(diagnostics)
        for private in ("device-private", "secret-private", "secret-in-error-url", "https://"):
            self.assertNotIn(private, raw)
        self.assertTrue(any(row.get("outcome") == "retry" for row in diagnostics))
        self.assertIs(transport.sdk.requests, transport.requests)

    def test_begin_is_never_replayed_after_ambiguous_network_failure(self):
        post = mock.Mock(side_effect=transport.requests.Timeout("private"))
        http = transport.RegistrationHTTP(mock.Mock(), post=post, sleep=mock.Mock())
        with self.assertRaisesRegex(transport.RegistrationTransportError, "begin:Timeout"):
            http.post("https://example.invalid", data="action=begin")
        self.assertEqual(post.call_count, 1)

    def test_authorization_link_delivery_retries_in_memory_and_reports_exhaustion(self):
        def invoke_sdk(**kwargs):
            kwargs["on_qr_code"]({"url": "https://example.invalid/private", "expire_in": 600})
            return {"client_id": "cli_test", "client_secret": "private"}
        # PLAN-1000 S1.1（2026-09-08 机器 3050）：投递失败不再中止注册——链接真源是终端输出，
        # bot 投递只是顺手转发。绑定了 bot 就重试三次；没绑定（第一只 bot）只记一次状态。
        for job, receipts, expected_calls in (
            ({"notify_bot": "tb26-link16"}, [{"ok": False}, {"ok": True}], 2),
            ({"notify_bot": "tb26-link16"}, [{"ok": False, "error": "x"}] * 3, 3),
            ({"notify_bot": None}, [{"ok": False, "error": "未绑定 notify_bot"}], 1),
        ):
            with self.subTest(job=job, receipts=len(receipts)), \
                 mock.patch.object(register.lark, "register_app", side_effect=invoke_sdk), \
                 mock.patch.object(register, "on_qr"), \
                 mock.patch.object(register.registration_monitor, "record_stage"), \
                 mock.patch.object(register.registration_monitor, "get_job", return_value=job), \
                 mock.patch.object(register.registration_monitor, "notify_oauth_link", side_effect=receipts) as notify, \
                 mock.patch.object(register.time, "sleep"):
                self.assertEqual(register._run_device_grant("test", job_id="test-job")["client_id"], "cli_test")
                self.assertEqual(notify.call_count, expected_calls)

    def test_poll_retry_budget_and_diagnostics_never_include_response_body(self):
        for failure in (lambda: transport.requests.Timeout("private"),
                        lambda: response({"secret": "private"}, 503),
                        lambda: response(["private"])):
            with self.subTest(failure=failure):
                post = mock.Mock(side_effect=[failure(), failure(), failure()])
                report = mock.Mock()
                http = transport.RegistrationHTTP(report, post=post, sleep=mock.Mock())
                with self.assertRaises(transport.RegistrationTransportError) as error:
                    http.post("https://example.invalid", data="action=poll&device_code=private")
                self.assertEqual(post.call_count, 3)
                self.assertNotIn("private", str(error.exception))
                self.assertNotIn("private", str(report.call_args_list))

    def test_old_sdk_fails_preflight_before_any_request(self):
        with mock.patch.object(transport.lark, "register_app", new=lambda on_qr_code: None), \
             mock.patch.object(transport.requests, "post") as post:
            with self.assertRaisesRegex(transport.RegistrationTransportError, "incompatible"):
                transport.preflight()
        post.assert_not_called()

    def test_permanent_oauth_denial_stays_sdk_error_and_restores_transport(self):
        with mock.patch.object(transport.requests, "post", side_effect=[
            response({"supported_auth_methods": ["client_secret"]}),
            response({"device_code": "private", "interval": 0,
                      "verification_uri_complete": "https://example.invalid"}),
            response({"error": "access_denied", "error_description": "private"}),
        ]) as post, mock.patch.object(register, "on_qr"):
            with self.assertRaises(transport.sdk.AppAccessDeniedError) as error:
                register._run_device_grant("test")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(transport.safe_error(error.exception), "AppAccessDeniedError")
        self.assertIs(transport.sdk.requests, transport.requests)


if __name__ == "__main__":
    unittest.main()
