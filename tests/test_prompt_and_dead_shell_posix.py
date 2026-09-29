"""POSIX 提示符与死壳判据（PLAN-1140 P0）。

wmux_session._PROMPT_TAIL_RE 只认 $/> /❯ 时，mac 上 zsh 的 `% ` 提示符永不命中，
_shell_ready 退回盲等；agent_runtime.is_live 的死壳判据写死 MINGW64（Git Bash 特征），
mac 上裸 zsh/bash 壳判不出来。这里钉两边的 POSIX 形状。
"""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import agent_runtime
import wmux_session


def test_prompt_tail_matches_zsh_percent():
    assert wmux_session._PROMPT_TAIL_RE.search('kintojee@MacBook link16-agent-infra %')
    assert wmux_session._PROMPT_TAIL_RE.search('bash-3.2$ ')
    assert wmux_session._PROMPT_TAIL_RE.search('PS C:\\Users\\x> ')
    assert wmux_session._PROMPT_TAIL_RE.search('❯ ')
    assert not wmux_session._PROMPT_TAIL_RE.search('still running')


def test_shell_ready_accepts_zsh_prompt():
    assert wmux_session._shell_ready('一些输出\nkintojee@MacBook link16-agent-infra % \n') is True
    assert wmux_session._shell_ready('一些输出\n没有任何提示符') is False
    assert wmux_session._shell_ready('') is False


def test_is_live_posix_dead_zsh_shell():
    screen = '上一次会话的输出\nkintojee@MacBook link16-agent-infra %'
    assert agent_runtime.is_live({'name': 'x', 'agent': 'claude'}, screen) is False


def test_is_live_posix_dead_bash_shell():
    screen = 'exit\nbash-3.2$'
    assert agent_runtime.is_live({'name': 'x', 'agent': 'claude'}, screen) is False


def test_is_live_posix_busy_agent_is_never_killed():
    """忙碌中的 TUI 屏底是 spinner/状态栏，不是提示符形状 —— 绝不能误判死。"""
    screen = '✻ Ideating… (47s · ↓ 1.1k tokens · esc to interrupt)'
    assert agent_runtime.is_live({'name': 'x', 'agent': 'claude'}, screen) is True
    assert agent_runtime.is_live({'name': 'x', 'agent': 'claude'}, '') is True
    # 就绪屏（❯ composer）→ is_ready 先命中，是活会话
    assert agent_runtime.is_live({'name': 'x', 'agent': 'claude'}, '❯ ') is True


@pytest.mark.skipif(os.name != 'nt', reason='Git Bash 死壳特征只在 Windows 上有')
def test_is_live_windows_mingw64_dead_shell():
    screen = 'user@host MINGW64 /c/repo\n$ '
    assert agent_runtime.is_live({'name': 'x', 'agent': 'claude'}, screen) is False
