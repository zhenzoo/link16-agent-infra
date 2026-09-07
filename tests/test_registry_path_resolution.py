#!/usr/bin/env python3
"""舰队通讯录的路径解析顺序（PLAN-926 §S1.1 · 公开前脱敏）。

背景：`feishu/agent-registry.json` 曾经是 committed 的真数据（全舰队 open_id +
主机名 + 内网拓扑），公开仓不能带它。真数据迁到 **runtime profile home**：
`~/.claude-personal/link16/agent-registry.json`，没装 Claude 就落 `~/.codex-personal/`。

这一条规则对两种人都成立、不需要分叉：对维护者，那个 home 本身是私有 git 仓的
工作树，通讯录自动搭上已有的跨机同步；对只有一台机的陌生人，它就是个本机目录。

锁死在测试里的三件事：
1. 显式 env override 永远最高优先——测试与特殊部署要能强制指定。
2. profile home 命中时必须用它，且 claude 侧优先于 codex 侧。
3. profile home 不存在时**必须静默落回仓内旧档**——这是迁移期零风险的保证：
   还没建 profile home 通讯录的机器，行为要和改动前逐字节一致。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_env  # noqa: E402

try:
    from test_support import temp_dir  # type: ignore  # noqa: F401
except ImportError:  # 本仓没有共用 helper 时用标准库
    import tempfile

    class temp_dir:  # noqa: N801  (与可能存在的 helper 同名)
        def __enter__(self):
            self._t = tempfile.TemporaryDirectory()
            return Path(self._t.name)

        def __exit__(self, *exc):
            self._t.cleanup()
            return False


class RegistryPathResolution(unittest.TestCase):
    def test_env_override_wins_over_everything(self):
        """① 环境变量最高优先，且不要求文件存在（特殊部署可指向还没建的路径）。"""
        with temp_dir() as tmp:
            target = tmp / "somewhere" / "agent-registry.json"
            with mock.patch.dict("os.environ", {"LINK16_AGENT_REGISTRY": str(target)}):
                self.assertEqual(bridge_env.registry_path(), target)

    def test_claude_profile_home_preferred_over_codex(self):
        """② claude 侧命中就用它，即使 codex 侧也存在。"""
        with temp_dir() as tmp:
            claude = tmp / "claude" / "link16" / "agent-registry.json"
            codex = tmp / "codex" / "link16" / "agent-registry.json"
            for p in (claude, codex):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("{}", encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("LINK16_AGENT_REGISTRY", None)
                with mock.patch.object(bridge_env, "profile_home_registries",
                                       return_value=[claude, codex]):
                    self.assertEqual(bridge_env.registry_path(), claude)

    def test_codex_home_used_when_claude_absent(self):
        """③ 只装 Codex 的人：claude 侧不存在时落 codex 侧。"""
        with temp_dir() as tmp:
            claude = tmp / "claude" / "link16" / "agent-registry.json"   # 不建
            codex = tmp / "codex" / "link16" / "agent-registry.json"
            codex.parent.mkdir(parents=True, exist_ok=True)
            codex.write_text("{}", encoding="utf-8")
            import os
            os.environ.pop("LINK16_AGENT_REGISTRY", None)
            with mock.patch.object(bridge_env, "profile_home_registries",
                                   return_value=[claude, codex]):
                self.assertEqual(bridge_env.registry_path(), codex)

    def test_falls_back_into_repo_when_no_profile_home(self):
        """④ 迁移期零风险：profile home 都没有 → 回落仓内旧档，行为与改动前一致。"""
        with temp_dir() as tmp:
            missing = [tmp / "nope" / "agent-registry.json"]
            import os
            os.environ.pop("LINK16_AGENT_REGISTRY", None)
            with mock.patch.object(bridge_env, "profile_home_registries",
                                   return_value=missing):
                resolved = bridge_env.registry_path()
            self.assertEqual(resolved.parent.name, "feishu")
            self.assertTrue(resolved.name.startswith("agent-registry"))


class ProfileHomeCandidates(unittest.TestCase):
    def test_derives_both_runtimes_in_claude_first_order(self):
        doc = {
            "default_profiles": {"claude": "ccp", "codex": "cxp"},
            "profiles": {"ccp": {"home": "~/.claude-personal"},
                         "cxp": {"home": "~/.codex-personal"}},
        }
        with mock.patch.object(bridge_env, "_profiles_document", return_value=doc):
            got = bridge_env.profile_home_registries()
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], Path("~/.claude-personal").expanduser() / "link16" / "agent-registry.json")
        self.assertEqual(got[1], Path("~/.codex-personal").expanduser() / "link16" / "agent-registry.json")

    def test_skips_runtime_without_default_profile(self):
        """只登记了 codex 的机器不该凭空造出一个 claude 候选。"""
        doc = {
            "default_profiles": {"codex": "cxp"},
            "profiles": {"cxp": {"home": "~/.codex-personal"}},
        }
        with mock.patch.object(bridge_env, "_profiles_document", return_value=doc):
            got = bridge_env.profile_home_registries()
        self.assertEqual(got, [Path("~/.codex-personal").expanduser() / "link16" / "agent-registry.json"])

    def test_empty_document_yields_no_candidates(self):
        with mock.patch.object(bridge_env, "_profiles_document", return_value={}):
            self.assertEqual(bridge_env.profile_home_registries(), [])


class WritableRegistryGuard(unittest.TestCase):
    """写入闸：绝不把真数据写进随仓发布的脱敏样例。

    registry.py sync-groups、register_feishu_app 的自动登记、reset_bot_identity 换
    open_id 都会【写回】解析结果。陌生人第一次注册 bot 时本机还没有名册，
    registry_path() 会一路落到样例档 —— 那时若直接写，他的真 open_id 会被写进
    仓库自带的样例文件，一 commit 就泄漏，而他自己的名册其实没建起来。
    """

    def test_returns_real_registry_untouched(self):
        """正常情况：已经有真名册，原样返回，不做任何多余动作。"""
        with temp_dir() as tmp:
            real = tmp / "agent-registry.json"
            real.write_text("{}", encoding="utf-8")
            with mock.patch.object(bridge_env, "registry_path", return_value=real):
                self.assertEqual(bridge_env.writable_registry_path(), real)

    def test_refuses_example_and_seeds_empty_skeleton(self):
        """落到样例档时：改用推荐位置，并落一份空骨架（不复制样例里的假数据）。"""
        with temp_dir() as tmp:
            example = tmp / bridge_env.EXAMPLE_REGISTRY_NAME
            example.write_text(json.dumps({"agents": [{"name": "fake"}]}), encoding="utf-8")
            target = tmp / "home" / "link16" / "agent-registry.json"
            with mock.patch.object(bridge_env, "registry_path", return_value=example),                  mock.patch.object(bridge_env, "profile_home_registries", return_value=[target]):
                got = bridge_env.writable_registry_path()
            self.assertEqual(got, target)
            self.assertTrue(target.is_file())
            doc = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(doc["agents"], [], "空骨架不能带样例里的假 agent")
            for key in ("shared_repos", "machines", "groups", "tenants", "agents"):
                self.assertIn(key, doc, "空骨架必须 schema 完整，否则调用方会 KeyError")
            self.assertEqual(example.read_text(encoding="utf-8"),
                             json.dumps({"agents": [{"name": "fake"}]}),
                             "样例文件一个字节都不该被动")

    def test_create_false_fails_loudly_instead_of_writing_example(self):
        """不允许创建时，宁可显式报错停住，也不回退去写样例。"""
        with temp_dir() as tmp:
            example = tmp / bridge_env.EXAMPLE_REGISTRY_NAME
            example.write_text("{}", encoding="utf-8")
            target = tmp / "home" / "link16" / "agent-registry.json"
            with mock.patch.object(bridge_env, "registry_path", return_value=example),                  mock.patch.object(bridge_env, "profile_home_registries", return_value=[target]):
                with self.assertRaises(SystemExit):
                    bridge_env.writable_registry_path(create=False)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
