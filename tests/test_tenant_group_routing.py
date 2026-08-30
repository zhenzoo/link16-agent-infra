#!/usr/bin/env python3
"""租户路由 + 权限开通链长度闸（2026-08-27 · PLAN 见 SOP-120 §4.1/§4.2）。

两条被实测打脸的假设，锁死在测试里：
1. 「共享 a2a 群只有交流水吧一个」—— 错。企业租户（企业租户A 企业租户A）的 bot 进自己租户的群，
   只有个人租户的才进交流水吧。判定只认 tenant_key，绝不看 bot 名字前缀。
2. 「scope 全塞进一条 auth?q= 链就行」—— 错。54 条 = 1446 字符 → 飞书整页「参数不合法」；
   18 条 = 523 字符 → 正常。链接一律过长度闸、超了拆条。
"""
import sys
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
        self.assertGreater(len(urls), 1, "54 条 preset 必须拆条，不能吐一条 1446 字符的死链")
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
    def test_enterprise_tenant_routes_to_its_own_group(self):
        entry = tenant_probe.tenant_entry("ent-key", FAKE_TENANTS)
        self.assertEqual(entry["group_name"], "obsagent 大乱斗")

    def test_unknown_tenant_does_not_guess_a_group(self):
        entry = tenant_probe.tenant_entry("who-knows", FAKE_TENANTS)
        self.assertIsNone(entry)

    def test_missing_tenant_key_does_not_guess_a_group(self):
        entry = tenant_probe.tenant_entry(None, FAKE_TENANTS)
        self.assertIsNone(entry)

    def test_real_registry_maps_obsbot_tenant_to_obsagent_group(self):
        """真名册（agent-registry.json）里 企业租户A 租户必须指向 obsagent 大乱斗。"""
        entry = tenant_probe.tenant_entry("TENANT_KEY_ENTERPRISE")
        self.assertEqual(entry.get("group_name"), "obsagent 大乱斗")
        self.assertEqual(entry.get("kind"), "enterprise")

    def test_real_registry_maps_personal_tenant_exactly(self):
        entry = tenant_probe.tenant_entry("TENANT_KEY_PERSONAL")
        self.assertEqual(entry.get("group_name"), "tb24-25交流水吧")
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


if __name__ == "__main__":
    unittest.main()
