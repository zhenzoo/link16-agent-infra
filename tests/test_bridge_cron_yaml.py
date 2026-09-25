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
