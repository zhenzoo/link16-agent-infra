"""hook 命令必须钉解释器绝对路径（PLAN-1140 P0）：mac 没有 `python` 别名，
写死 `python` 的 hook 会静默失效。bridge_outbox 的 bridge-hooks.json 与
install_codex_bridge_hooks 的 hooks.json 两处都要用 sys.executable。
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_outbox
import install_codex_bridge_hooks as installer


def test_bridge_hooks_settings_commands_use_sys_executable(tmp_path):
    path = bridge_outbox.write_hooks_settings(tmp_path, ROOT / 'feishu' / 'hooks')
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    commands = [
        hook['command']
        for entries in cfg['hooks'].values()
        for entry in entries
        for hook in entry['hooks']
    ]
    assert commands, 'hooks.json 里必须有 hook 命令'
    for command in commands:
        assert command.startswith(f'"{sys.executable}" '), f'hook 必须钉解释器绝对路径：{command}'
        assert not command.startswith('python '), '裸 python 在 mac 上静默失效'
        assert command.endswith('.py"')


def test_hook_cmd_uses_sys_executable():
    cmd = installer._hook_cmd(Path('/x/hooks/codex_bridge_stop.py'))
    assert cmd == f'"{sys.executable}" "/x/hooks/codex_bridge_stop.py"'


def test_missing_hook_scripts_looks_at_script_not_interpreter(tmp_path):
    real = tmp_path / 'real.py'
    real.write_text('# x', encoding='utf-8')
    additions = {
        'Stop': [{'hooks': [
            {'type': 'command', 'command': f'"{sys.executable}" "{real.as_posix()}"'},
            {'type': 'command', 'command': f'"{sys.executable}" "{(tmp_path / "gone.py").as_posix()}"'},
        ]}],
    }
    missing = installer._missing_hook_scripts(additions)
    assert len(missing) == 1 and 'gone.py' in missing[0], '要查的是脚本路径，不是解释器'
