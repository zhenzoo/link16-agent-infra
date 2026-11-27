"""cron without pyyaml must say so instead of looking like an empty schedule."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_cron as cron


def _jobs_dir(tmp_path, monkeypatch):
    d = tmp_path / 'cron-jobs'
    d.mkdir()
    (d / 'tb-x.yaml').write_text('jobs:\n- name: nightly\n  cron: 0 0 * * *\n  prompt: go\n', encoding='utf-8')
    monkeypatch.setattr(cron, 'JOBS_DIR', d)
    monkeypatch.setattr(cron, 'JOBS_PATH', tmp_path / 'cron-jobs.json')
    monkeypatch.setattr(cron.bridge_process, 'require_known', lambda pids: [])
    monkeypatch.setattr(cron, '_cron_pids', lambda: [])
    monkeypatch.setattr(cron, '_roster_bots', lambda: {'tb-x'})
    monkeypatch.setattr(cron, '_load_lastfire', lambda: {})


def test_board_names_missing_pyyaml_instead_of_empty_schedule(tmp_path, monkeypatch, capsys):
    _jobs_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(cron, 'yaml', None)

    cron.cmd_board()

    out = capsys.readouterr().out
    assert '没装 pyyaml' in out and 'tb-x.yaml' in out
    assert '还没有任何定时任务' not in out


def test_board_lists_jobs_when_pyyaml_present(tmp_path, monkeypatch, capsys):
    _jobs_dir(tmp_path, monkeypatch)

    cron.cmd_board()

    out = capsys.readouterr().out
    assert 'nightly' in out
    assert cron.yaml_blind_warning() is None


def test_recurring_cron_waits_while_watchdog_has_one_shot_short_limit(tmp_path, monkeypatch):
    import json

    state = tmp_path / 'watchdog-short-waits.json'
    monkeypatch.setattr(cron, 'SHORT_WAIT_PATH', state)
    messages = []
    monkeypatch.setattr(cron, 'log', messages.append)
    state.write_text(json.dumps({'tb-x': {'due_at': 10400, 'status': 'scheduled'}}), encoding='utf-8')
    assert cron.fire({'name': 'nightly', 'bot': 'tb-x', 'prompt': 'continue'}) is False
    assert any('正在等短时额度恢复' in msg for msg in messages)
    state.write_text('{}', encoding='utf-8')
    assert cron._watchdog_waiting_for_short_limit('tb-x') is False


def test_recurring_cron_does_not_restart_terminal_quota_session(tmp_path, monkeypatch):
    import feishu_bridge as fb
    import quota_recovery

    monkeypatch.setattr(cron, 'SHORT_WAIT_PATH', tmp_path / 'missing-short-waits.json')
    monkeypatch.setattr(fb, 'STATE_DIR', tmp_path)
    bot = {'name': 'tb-x', 'profile': 'cxp'}
    monkeypatch.setattr(fb, 'load_bots', lambda: [bot])
    monkeypatch.setattr(fb.agent_runtime, 'profile_name', lambda *a, **kw: 'cxp')
    monkeypatch.setattr(fb, 'ensure_session',
                        lambda bot: (_ for _ in ()).throw(AssertionError('must not restart quota-stopped session')))
    full = {'profile': 'cxp', 'runtime': 'codex', 'status': 'ok', 'weekly_percent': 100}
    monkeypatch.setattr(quota_recovery.agent_quota, 'collect', lambda names: [full])
    messages = []
    monkeypatch.setattr(cron, 'log', messages.append)
    quota_recovery.stop(tmp_path, 'tb-x', 'cxp', 'weekly-1')

    assert cron.fire({'name': 'nightly', 'bot': 'tb-x', 'prompt': 'continue'}) is False
    assert any('不重启已终止会话' in message for message in messages)
    assert quota_recovery.blocked(tmp_path, 'tb-x', 'cxp2') is False

    full['weekly_percent'] = 5
    assert quota_recovery.blocked(tmp_path, 'tb-x', 'cxp') is False
    assert quota_recovery.get(tmp_path, 'tb-x') == {}


def test_corrupt_quota_stop_state_fails_closed(tmp_path):
    import quota_recovery

    quota_recovery.path(tmp_path).write_text('{broken', encoding='utf-8')
    assert quota_recovery.blocked(tmp_path, 'tb-x', 'cxp') is True
    assert quota_recovery.path(tmp_path).read_text(encoding='utf-8') == '{broken'
