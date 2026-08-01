#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cron.py — 【主人自己开关定时任务的入口】一条命令，勾复选框。

    python feishu/cron.py            # 打开复选菜单：↑↓ 选 · 空格 开/关 · 回车 保存退出 · q 放弃
    python feishu/cron.py board      # 带参数 = 直接透传给 bridge_cron.py（board/status/add/rm/fire/start…）

【为什么单独一个文件】机制全在 `bridge_cron.py`（守护进程 + 完整 CLI），但那个名字是给桥 / agent / 脚本用的，
裸跑它必须保持 `status`（否则 agent 跑一句"看看状态"会被卡进交互 TUI）。所以给人留这个短门面：
名字好记、裸跑就是菜单、想用高级命令再带参数。改开关 = 写回 `cron-jobs/<bot>.yaml`，守护进程热读、免重启。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_cron                                     # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) == 1:
        bridge_cron.cmd_menu()                         # 裸跑 = 复选菜单（本文件存在的理由）
    else:
        bridge_cron.main()                             # 带参数 = 完整 CLI 门面（argv 原样透传）
