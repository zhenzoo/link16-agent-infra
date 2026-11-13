"""Failed startup cleanup must never forget a still-owned workspace."""
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import feishu_bridge as fb


@pytest.fixture
def scene(tmp_path, monkeypatch):
    bot = {'name': 'bot', 'profile': 'cxp', 'agent': 'codex'}
    launch = {'workspace_id': 'ws', 'pty': 'pty', 'startup_id': 'attempt',
              'profile': 'cxp', 'daemon_fp': 'daemon'}
    monkeypatch.setattr(fb, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(fb, 'blog', Mock())
    monkeypatch.setattr(fb.wmux_session, 'daemon_fingerprint', lambda: 'daemon')
    monkeypatch.setattr(fb.wmux_session, 'workspaces', lambda: [{'id': 'ws', 'ptyIds': ['pty']}])
    monkeypatch.setattr(fb, '_load_startup_failure', lambda *args: {'workspace_id': 'ws', 'pty': 'pty'})
    monkeypatch.setattr(fb.wmux_session, 'shell_tree_idle', Mock(return_value=True))
    monkeypatch.setattr(fb.wmux_session, 'close', Mock(side_effect=[RuntimeError('active-agent label'), {}]))
    monkeypatch.setattr(fb.time, 'sleep', lambda _: None)
    fb._merge_session('bot', {**launch, 'failed_launch': launch, 'jsonl': 'original-history'})
    return bot, launch


def test_failed_shell_cleanup_confirms_exit_and_preserves_history(scene):
    bot, launch = scene
    fb._cleanup_failed_launch(bot, launch)
    assert fb.wmux_session.close.call_args_list[1].kwargs == {'force': True}
    rec = fb.load_session('bot')
    assert rec['failed_launch'] is None and rec['workspace_id'] is None
    assert rec['jsonl'] == 'original-history'


def test_live_or_unknown_worker_retains_failed_binding_and_blocks_second_spawn(scene, monkeypatch):
    bot, launch = scene
    fb.wmux_session.shell_tree_idle.return_value = False
    monkeypatch.setattr(fb, '_invalidate_restart_resume_unlocked', lambda *args: False)
    monkeypatch.setattr(fb.wmux_session, 'spawn', Mock())
    with pytest.raises(RuntimeError, match='尚未确认退出'):
        fb._ensure_session_unlocked(bot)
    assert fb.load_session('bot')['failed_launch'] == launch
    fb.wmux_session.spawn.assert_not_called()
    fb.wmux_session.close.assert_called_once_with('ws')


@pytest.mark.parametrize('change', ['profile', 'daemon', 'pane', 'evidence'])
def test_changed_workspace_never_closed(scene, monkeypatch, change):
    bot, launch = scene
    if change == 'profile':
        launch['profile'] = 'cxp2'
    elif change == 'daemon':
        launch['daemon_fp'] = 'different'
    elif change == 'pane':
        monkeypatch.setattr(fb.wmux_session, 'workspaces', lambda: [{'id': 'ws', 'ptyIds': ['other']}])
    else:
        monkeypatch.setattr(fb, '_load_startup_failure', lambda *args: {})
    with pytest.raises(RuntimeError):
        fb._cleanup_failed_launch(bot, launch)
    fb.wmux_session.close.assert_not_called()
