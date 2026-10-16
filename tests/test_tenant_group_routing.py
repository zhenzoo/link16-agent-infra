#!/usr/bin/env python3
"""租户路由 + 权限开通链长度闸（2026-08-27 · PLAN 见 SOP-120 §4.1/§4.2）。

两条被实测打脸的假设，锁死在测试里：
1. 「共享 a2a 群只有交流水吧一个」—— 错。企业租户（企业租户A 企业租户A）的 bot 进自己租户的群，
   只有个人租户的才进交流水吧。判定只认 tenant_key，绝不看 bot 名字前缀。
2. 「scope 全塞进一条 auth?q= 链就行」—— 错。54 条 = 1446 字符 → 飞书整页「参数不合法」；
   18 条 = 523 字符 → 正常。链接一律过长度闸、超了拆条。
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import feishu_docs  # noqa: E402
import bridge_scope_audit as audit  # noqa: E402
import tenant_probe  # noqa: E402
import registration_monitor as monitor  # noqa: E402
import scope_level  # noqa: E402


class AuthUrlLengthGateTests(unittest.TestCase):
    def test_long_scope_list_is_split_and_每条都在闸内(self):
        urls = feishu_docs.auth_urls("cli_demo", scope_level.ENTERPRISE_PRESET)
        self.assertGreater(len(urls), 1, "企业 preset 必须拆条，不能吐一条超长死链")
        for url in urls:
            self.assertLessEqual(len(url), feishu_docs.AUTH_URL_MAX_CHARS)

    def test_split_preserves_every_scope_exactly_once(self):
        scopes = list(scope_level.ENTERPRISE_PRESET)
        urls = feishu_docs.auth_urls("cli_demo", scopes)
        seen = []
        for url in urls:
            q = url.split("auth?q=", 1)[1].split("&", 1)[0]
            seen.extend(q.split(","))
        self.assertEqual(seen, scopes, "拆条不得丢 scope、不得重复、不得改顺序")

    def test_short_list_stays_one_link(self):
        urls = feishu_docs.auth_urls("cli_demo", ["im:chat", "im:resource"])
        self.assertEqual(len(urls), 1)

    def test_empty_scopes_yield_no_link(self):
        self.assertEqual(feishu_docs.auth_urls("cli_demo", []), [])

    def test_scope_audit_delegates_to_the_same_gate(self):
        urls = audit.fix_auth_urls("cli_demo", scope_level.ENTERPRISE_PRESET)
        self.assertEqual(urls, feishu_docs.auth_urls("cli_demo", scope_level.ENTERPRISE_PRESET))
        self.assertEqual(audit.fix_auth_url("cli_demo", ["im:chat"]), urls and
                         feishu_docs.auth_urls("cli_demo", ["im:chat"])[0])


FAKE_TENANTS = [
    {
        "tenant_key": "ent-key",
        "label": "企业租户A 企业租户A",
        "kind": "enterprise",
        "group_chat_id": "oc_ent",
        "group_name": "obsagent 大乱斗",
    },
    {
        "tenant_key": "personal-key",
        "label": "个人租户",
        "kind": "personal",
        "group_chat_id": "oc_personal",
        "group_name": "tb24-25交流水吧",
    },
]


class TenantRoutingTests(unittest.TestCase):
    def test_chat_refresh_sees_join_after_empty_cached_result(self):
        tenant_probe._CHATS_CACHE.clear()
        spec = {"app_id_env": "NEW_ID", "app_secret_env": "SECRET"}
        joined = {"tenant_key": "ent-key", "external": False}
        with mock.patch.object(tenant_probe.audit, "_token", return_value=("token", None)), \
                mock.patch.object(tenant_probe.audit, "_req", side_effect=[
                    {"code": 0, "data": {"items": []}},
                    {"code": 0, "data": {"items": [joined]}},
                ]):
            self.assertIsNone(tenant_probe._query_via_chats(spec)[0])
            self.assertEqual(tenant_probe._query_via_chats(spec, refresh=True)[0], "ent-key")

    def test_chat_probe_checks_later_pages_before_persisting_unique_key(self):
        spec = {"app_id_env": "PAGED_ID", "app_secret_env": "SECRET"}
        with mock.patch.object(tenant_probe.audit, "_token", return_value=("token", None)), \
                mock.patch.object(tenant_probe.audit, "_req", side_effect=[
                    {"code": 0, "data": {"items": [{"tenant_key": "ent-key", "external": False}],
                                         "has_more": True, "page_token": "page 2"}},
                    {"code": 0, "data": {"items": [{"tenant_key": "other-key", "external": False}]}},
                ]) as request:
            key, error, _ = tenant_probe._query_via_chats(spec, refresh=True)
        self.assertIsNone(key)
        self.assertIn("多个", error)
        self.assertIn("page_token=page+2", request.call_args.args[1])

    def test_enterprise_tenant_routes_to_its_own_group(self):
        entry = tenant_probe.tenant_entry("ent-key", FAKE_TENANTS)
        self.assertEqual(entry["group_name"], "obsagent 大乱斗")

    def test_unknown_tenant_does_not_guess_a_group(self):
        entry = tenant_probe.tenant_entry("who-knows", FAKE_TENANTS)
        self.assertIsNone(entry)

    def test_missing_tenant_key_does_not_guess_a_group(self):
        entry = tenant_probe.tenant_entry(None, FAKE_TENANTS)
        self.assertIsNone(entry)

    def test_shipped_example_registry_maps_enterprise_tenant(self):
        """随仓发布的样例名册必须把企业租户接到它自己的群。

        这两个用例原先读 committed 的真名册、硬编码真 tenant_key。名册迁出 git 之后
        （PLAN-926 §S1.1），公开 clone 里没有真数据，那样写等于把测试绑死在维护者
        的私人舰队上。现在改读**样例名册**：走的仍是
        `registry_path()` → `load_tenants()` → `tenant_entry()` 这条真实代码路径，
        只是数据换成脱敏的，任何人 clone 下来都能跑。
        """
        example = ROOT / "feishu" / "agent-registry.example.json"
        with mock.patch.dict("os.environ", {"LINK16_AGENT_REGISTRY": str(example)}):
            entry = tenant_probe.tenant_entry("0000000000000001")
        self.assertEqual(entry.get("group_name"), "示例共享群")
        self.assertEqual(entry.get("kind"), "enterprise")

    def test_shipped_example_registry_maps_personal_tenant(self):
        example = ROOT / "feishu" / "agent-registry.example.json"
        with mock.patch.dict("os.environ", {"LINK16_AGENT_REGISTRY": str(example)}):
            entry = tenant_probe.tenant_entry("0000000000000002")
        self.assertEqual(entry.get("group_name"), "示例个人共享群")
        self.assertEqual(entry.get("kind"), "personal")

    def test_chat_probe_rejects_multiple_tenant_keys(self):
        tenant_probe._CHATS_CACHE.clear()
        spec = {"app_id_env": "ID", "app_secret_env": "SECRET"}
        with mock.patch.object(tenant_probe.audit, "_token", return_value=("token", None)), \
                mock.patch.object(tenant_probe.audit, "_req", return_value={
                    "code": 0, "data": {"items": [
                        {"tenant_key": "ent-key", "external": False},
                        {"tenant_key": "personal-key", "external": False},
                    ]},
                }):
            key, error, _groups = tenant_probe._query_via_chats(spec)
        self.assertIsNone(key)
        self.assertIn("多个 tenant_key", error)

    def test_chat_probe_does_not_treat_external_group_as_app_tenant(self):
        tenant_probe._CHATS_CACHE.clear()
        spec = {"app_id_env": "ID2", "app_secret_env": "SECRET2"}
        with mock.patch.object(tenant_probe.audit, "_token", return_value=("token", None)), \
                mock.patch.object(tenant_probe.audit, "_req", return_value={
                    "code": 0, "data": {"items": [
                        {"tenant_key": "foreign-key", "external": True},
                    ]},
                }):
            key, error, _groups = tenant_probe._query_via_chats(spec)
        self.assertIsNone(key)
        self.assertIsNone(error)


class MonitorGroupCheckTests(unittest.TestCase):
    """group 没显式给时，入群验收要按租户判定，而不是无脑「在任意群就算过」。"""

    def _probe(self, groups):
        return types.SimpleNamespace(bot_groups=lambda _bot: groups)

    def test_group_none_uses_tenant_resolved_target(self):
        probe = self._probe([{"chat_id": "oc_ent", "name": "obsagent 大乱斗"}])
        with mock.patch.dict(sys.modules, {"bridge_feishu_probe": probe}), \
                mock.patch.object(monitor, "_resolve_group_hint",
                                  return_value=("obsagent 大乱斗", None, "oc_ent")):
            result = monitor._group_check("tb26-x", None)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["expected_group"], "obsagent 大乱斗")

    def test_wrong_group_is_not_accepted_once_tenant_is_known(self):
        """在别的群里 ≠ 入群完成 —— 这是旧实现（group=None 匹配任意群）会误判绿的场景。"""
        probe = self._probe([{"chat_id": "oc_other", "name": "对外群B"}])
        with mock.patch.dict(sys.modules, {"bridge_feishu_probe": probe}), \
                mock.patch.object(monitor, "_resolve_group_hint",
                                  return_value=("obsagent 大乱斗", None, "oc_ent")):
            result = monitor._group_check("tb26-x", None)
        self.assertEqual(result["status"], "missing")

    def test_same_name_with_wrong_chat_id_is_not_accepted(self):
        probe = self._probe([{"chat_id": "oc_imposter", "name": "obsagent 大乱斗"}])
        with mock.patch.dict(sys.modules, {"bridge_feishu_probe": probe}), \
                mock.patch.object(monitor, "_resolve_group_hint",
                                  return_value=("obsagent 大乱斗", None, "oc_ent")):
            result = monitor._group_check("tb26-x", None)
        self.assertEqual(result["status"], "missing")

    def test_unresolvable_tenant_stays_unknown_even_when_bot_is_in_a_group(self):
        probe = self._probe([{"chat_id": "oc_any", "name": "随便什么群"}])
        with mock.patch.dict(sys.modules, {"bridge_feishu_probe": probe}), \
                mock.patch.object(monitor, "_resolve_group_hint",
                                  return_value=(None, "租户未判定", None)):
            result = monitor._group_check("tb26-x", None)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["group_hint_note"], "租户未判定")

    def test_explicit_group_still_wins(self):
        probe = self._probe([{"chat_id": "oc_ent", "name": "obsagent 大乱斗"}])
        with mock.patch.dict(sys.modules, {"bridge_feishu_probe": probe}), \
                mock.patch.object(monitor, "_resolve_group_hint",
                                  side_effect=AssertionError("显式 --group 时不该再判租户")):
            result = monitor._group_check("tb26-x", "obsagent")
        self.assertEqual(result["status"], "ready")


class TenantRegistrySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "registry.json"
        self.original = {"agents": [{"name": "显示名", "send_key": "new-bot", "repo": "keep"},
                                    {"name": "other", "tenant_key": "other-key"}],
                         "tenants": FAKE_TENANTS}
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        for name in ("registry_path", "writable_registry_path"):
            patch = mock.patch.object(tenant_probe, name, return_value=self.path)
            patch.start()
            self.addCleanup(patch.stop)

    def test_unknown_then_known_updates_only_missing_field_and_repeated_call_does_not_probe(self):
        with mock.patch.object(tenant_probe, "resolve_tenant", side_effect=[
            {"tenant_key": None, "error": "未入群"},
            {"tenant_key": "ent-key", "source": "chats"},
        ]) as probe:
            self.assertEqual(tenant_probe.sync_registry_tenant("new-bot")["status"], "unknown")
            self.assertEqual(json.loads(self.path.read_text()), self.original)
            self.assertEqual(tenant_probe.sync_registry_tenant("new-bot")["status"], "updated")
            self.assertEqual(tenant_probe.sync_registry_tenant("new-bot")["status"], "unchanged")
        self.assertEqual(probe.call_count, 2)
        probe.assert_called_with("new-bot", refresh=True)
        expected = json.loads(json.dumps(self.original))
        expected["agents"][0]["tenant_key"] = "ent-key"
        self.assertEqual(json.loads(self.path.read_text()), expected)

    def test_concurrent_registration_is_preserved_and_conflicting_value_is_not_overwritten(self):
        def live(_bot, **_kwargs):
            data = json.loads(self.path.read_text())
            data["agents"].append({"name": "new-other"})
            data["agents"][0]["tenant_key"] = "manual-key"
            self.path.write_text(json.dumps(data), encoding="utf-8")
            return {"tenant_key": "ent-key", "source": "chats"}
        with mock.patch.object(tenant_probe, "resolve_tenant", side_effect=live):
            result = tenant_probe.sync_registry_tenant("new-bot")
        self.assertEqual(result["status"], "unknown")
        self.assertIn("未覆盖", result["error"])
        data = json.loads(self.path.read_text())
        self.assertEqual(data["agents"][0]["tenant_key"], "manual-key")
        self.assertEqual(data["agents"][-1]["name"], "new-other")

    def test_write_failure_remains_diagnostic_without_leaking_exception_text(self):
        with mock.patch.object(tenant_probe, "resolve_tenant",
                               return_value={"tenant_key": "ent-key", "source": "chats"}), \
                mock.patch.object(tenant_probe, "atomic_write_json", side_effect=OSError("secret-private")):
            result = tenant_probe.sync_registry_tenant("new-bot")
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("secret-private", str(result))
        self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_monitor_retries_after_join_and_persists_before_ready(self):
        with mock.patch.object(monitor, "STATE_DIR", self.root / "state"), \
                mock.patch.object(monitor, "_permission_check", return_value={"status": "ready"}), \
                mock.patch.object(monitor, "_owner_check", return_value={"status": "ready"}), \
                mock.patch.object(monitor, "_group_check", side_effect=[{"status": "missing"}, {"status": "ready"}]), \
                mock.patch.object(tenant_probe, "resolve_tenant", side_effect=[
                    {"tenant_key": None, "error": "未入群"},
                    {"tenant_key": "ent-key", "source": "chats"},
                ]):
            state = monitor.arm_job("new-bot", ["core", "group-a2a"], group="指定群",
                                    notify_bot="controller", app_id="cli_test",
                                    id_env="ID", secret_env="SECRET", launch=False)
            monitor.record_sdk_result(state["job_id"], "cli_test")
            monitor.record_stage(state["job_id"], "registered", app_id="cli_test")
            first = monitor.check_once(state["job_id"], emit=False)
            self.assertFalse(first["milestones"]["ready"])
            self.assertEqual(first["checks"]["tenant"]["status"], "unknown")
            second = monitor.check_once(state["job_id"], emit=False)
        self.assertTrue(second["milestones"]["ready"])
        self.assertEqual(second["checks"]["tenant"]["status"], "updated")
        self.assertNotIn("tenant_ready", second["milestones"])
        self.assertEqual(json.loads(self.path.read_text())["agents"][0]["tenant_key"], "ent-key")


if __name__ == "__main__":
    unittest.main()
