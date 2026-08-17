---
doc_type: SOP
doc_id: SOP-010
title: 在挂着大量 bot 的情况下安全升级 wmux
status: active
purpose: 给出升级 wmux 桌面端与 daemon 的顺序、验收与回退，避免升级把全舰队会话打断且无法恢复。
owns:
  - 认准正确 wmux 项目（有同名项目）
  - 升级前必须确认的契约与方法
  - 两条升级路径的取舍
  - 四步验收与 10 分钟回退
does_not_own:
  - wmux 怎么驱动面板（见 ARCH-010）
  - wmux 本体的功能（第三方项目）
  - 桥的重启步骤（见 SOP-100）
read_when:
  - 要升级 wmux
  - 升级后面板行为异常需要回退
last_reviewed: 2026-08-17
---
# SOP-010 · 升级 wmux（桌面端 + daemon）

> 配套 [`ARCH-010-wmux-orchestration.md`](ARCH-010-wmux-orchestration.md)（wmux 怎么驱动面板）。
> 本文只答一件事：**桥在跑着 27 个 bot 的情况下，怎么把 wmux 安全升到新版、怎么验、怎么退。**

## 0 · 先认准是哪个 wmux（⚠️ 有同名项目，别装错）

| | 我们用的 | 同名的另一个（**不是**我们的） |
|---|---|---|
| 仓库 | **`openwong2kim/wmux`** | `amirlehmam/wmux` |
| 版本线 | `3.x`（2026-07-31 时 v3.38.1） | `0.x`（v0.39.1） |
| 架构 | **daemon + MCP + CLI 三件套**，会话托管在 daemon | 纯 Electron、**没有 daemon** |
| 本机配置 | `~/.wmux/config.json` | `config.toml` |

**认准方法**（一条命令，不靠记忆）：

```bash
python - <<'EOF'
import json,struct
p=r"C:/Users/zhenz/AppData/Local/wmux/app-<版本>/resources/app.asar"
f=open(p,"rb"); h=f.read(16); n=struct.unpack("<I",h[12:16])[0]
j=json.loads(f.read(n).decode("utf-8","replace").rsplit("}",1)[0]+"}")
pk=j["files"]["package.json"]; f.seek(16+n+int(pk["offset"]))
print(f.read(int(pk["size"])).decode("utf-8","replace")[:400])
EOF
```
`repository.url` 必须是 `github.com/openwong2kim/wmux`。**装错那个 = 没有 daemon = 全舰队的桥当场失联**（我们的 `wmux/wmux-rpc.js` 走的是 daemon 的命名管道）。

## 1 · 我们依赖 wmux 的哪些契约（升级前必须确认新版没改）

桥 → wmux 只经由 `wmux/wmux-rpc.js` 这一个客户端，它依赖 4 件事 + 5 个方法：

| 依赖 | 值 | 上游何处保证 |
|---|---|---|
| 鉴权 token | `~/.wmux-auth-token`（裸 UUID，不是 JSON） | `docs/how-to/connect-to-wmux.md` |
| 端点 | `\\.\pipe\wmux-<用户名>` | 同上 |
| Windows 兜底 | `~/.wmux-tcp-port` → `127.0.0.1:<port>` | 同上 |
| 帧格式 | NDJSON `{id,method,params,token}` → `{id,ok,result}` | `docs/PROTOCOL.md` |
| 方法 | `workspace.list/new/close`、`input.send`、`pane.list/split` | `docs/api/reference.md` |
| 返回字段 | `id` / `ptyIds` / `metadata.agentName` / `metadata.agentStatus` | `src/shared/workspaceMirror.ts` |
| daemon 指纹 | `~/.wmux/daemon.pid`（内容+mtime，桥用它判会话作废） | — |

⚠️ 新版给 RPC 加了 capability 门，`workspace.new` / `workspace.close` 标 `wmux.internal`；上游文档明写
**「legacy envelope-less callers grandfather through」**——我们这种不带 `clientName` 的裸客户端被放行。
**升级后第一件事就是验它**（见 §4 第 2 条）。

## 2 · 前置（做完再动手）

```bash
# ① 快照：谁在跑、谁有活会话（升完逐条对）
python feishu/feishu_bridge.py status > <scratch>/pre-upgrade.txt

# ② 确认回退件在手（两处任一即可）
ls ~/AppData/Local/wmux/packages/wmux-<旧版>-full.nupkg      # 本机 Squirrel 原件
gh release view v<旧版> -R openwong2kim/wmux                 # GitHub 上的 Setup.exe
```

## 3 · 升级（两条路，任选）

**A · 应用内（最省事）**：wmux → Settings → Updates → **Restart to install**。
后台自动更新平时就已经把包下好并校验过了（应用日志里 `[AutoUpdater] Update downloaded + verified (sha256 match) — ready to install`），这一步只是触发安装。**需要人点。**

**B · 命令行（可自动化，本文推荐给 agent 用）**：

```bash
gh release download v<新版> -R openwong2kim/wmux -p "update-manifest.json" -p "wmux-<新版>.Setup.exe" -D <scratch>
# 必须校验：manifest 里的 sha256 == 实际文件的
python -c "import hashlib,json,sys;m=json.load(open(r'<scratch>/update-manifest.json'));h=hashlib.sha256(open(r'<scratch>/'+m['setupExe'],'rb').read()).hexdigest();print('MATCH' if h==m['sha256'] else 'MISMATCH 拒绝安装')"
python feishu/feishu_bridge.py stop        # 停桥（避免升级中途有消息进来起会话）
<scratch>/wmux-<新版>.Setup.exe            # 安装器自己会关掉在跑的 wmux 窗口，装完重新拉起
```

- **终端会话在升级中存活**：安装器只终止 wmux 窗口，**daemon 是故意留着的**（上游 v3.28.0）。
- 若 daemon 也被换代（上游 v3.17.0 的 daemon 自替换）：旧 daemon 先把每个会话**durably suspend**，新 daemon 起来后恢复面板。

## 4 · 验收（全绿才算完成 · 不许跳）

```bash
# 1) 版本确实换了 + daemon 活着
ls ~/AppData/Local/wmux/ | grep app-          # 应出现 app-<新版>
node ~/wmux-rpc.js rpc system.identify "{}"

# 2) ★ 我们的裸客户端仍被放行（最关键一条）
node ~/wmux-rpc.js rpc workspace.list "{}"    # 要能返回，且元素带 ptyIds / metadata.agentName
python -c "import sys;sys.path.insert(0,'feishu');import wmux_session as w;ws=w.workspaces();print(len(ws), ws[0].keys())"

# 3) 建/关 workspace 走得通（验 wmux.internal 那道门）
python feishu/wmux_session.py spawn --name upgrade-probe --cwd <任意目录>
python feishu/wmux_session.py close --id <上一步返回的 ws id>

# 4) 单 bot 端到端，再放全量
python feishu/feishu_bridge.py start --bot <一个低风险 bot>   # 飞书 @ 它发一句，确认有回复
python feishu/feishu_bridge.py start                          # 全量
python feishu/feishu_bridge.py status                         # 逐条对 pre-upgrade.txt
```

## 5 · 回退（10 分钟内）

```bash
python feishu/feishu_bridge.py stop
gh release download v<旧版> -R openwong2kim/wmux -p "wmux-<旧版>.Setup.exe" -D <scratch>
<scratch>/wmux-<旧版>.Setup.exe     # 或直接用本机 packages/wmux-<旧版>-full.nupkg
python feishu/feishu_bridge.py start
```

## 6 · 已知副作用（正常现象，别当故障）

- **所有 bot 会冷启新会话**：daemon 换了进程 → `~/.wmux/daemon.pid` 指纹变 → 桥按设计判定旧会话作废，下一条消息重开。**所以别在 agent 干到一半时升。**
- 升级不影响飞书凭据 / 名册 / outbox，桥进程本身也不在 wmux 里（是脱离终端的后台进程）。

## 7 · 2026-07-31 首次执行记录（3.8.0 → 3.38.1）

- 起因：开久了花屏、内存越积越多、`workspace.list` 偶发 `RPC timeout (5000ms)` 吞消息。
- 升级跨 30 个版本，上游对应修复：`v3.32.0` detached 会话永不回收（曾有人报 40 个 powershell 占 3.3GB）、
  `v3.25.0` 每个 agent pane 省 ~50MB、`v3.31.0` 「Scale to 30+ concurrent sessions」（hook 风暴 +
  sessions.json 同步写饿死健康 ping）、`v3.20/3.21.3/3.22/3.29/3.32` 一串渲染线程改造、
  `v3.37.2` Windows 上 node-pty 误报 pane 退出。
- 同日配套改动：`feishu/wmux_session.py` 的 `_wmux()` 对**只读/幂等**调用撞瞬时错误自动重试 2 次
  （`workspace.new` / `send` 这类会改状态的**绝不重试**，防重复开 workspace / 重复注入）。
