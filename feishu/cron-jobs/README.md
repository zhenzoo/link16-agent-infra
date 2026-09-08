# feishu/cron-jobs/ · 每个 bot 一个 `<bot>.yaml`（本机文件，不入库）

文件名就是 bot 名（须在本机 `feishu/bridge-bots.local.json` 名册里），守护进程 `feishu/bridge_cron.py` 热读本目录，加 / 改 / 停无需重启。
新建任务用 `python feishu/bridge_cron.py add`，或手写：

```yaml
jobs:
- name: daily-digest          # 唯一名
  cron: '5 0 * * *'           # 分 时 日 月 周（周 0 = 周日）
  tz: Asia/Shanghai
  enabled: true
  prompt: 按 docs/SOP-xxx 跑今天的例行任务，做完回我一句结果。
```

`*.yaml` 已被 `.gitignore` 排除：任务 prompt 往往含你自己的项目细节，别提交。多机共用同一套 yaml 时各机只会真触发自己名册里 bot 的任务。
