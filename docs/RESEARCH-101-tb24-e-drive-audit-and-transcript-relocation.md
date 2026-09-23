---
doc_type: RESEARCH
doc_id: RESEARCH-101
title: tb24 E 盘全盘彻查：零决策可释放清单、待拍板清单与聊天记录搬迁方案
status: draft
purpose: 回答主人 2026-09-19 的三个问题——E 盘 1.86 TB 到底被什么占满、哪些能不经拍板直接删并能释放多少、C 盘聊天记录怎样搬出去而不影响 find-session 等默认路径。
owns:
  - 2026-09-19 tb24 E 盘与 C 盘用户目录的容量分布证据
  - 零决策可删清单（逐字节重复副本、工具运行时残留、回收站）的验证方法与结果
  - 待主人拍板的清理候选及其依据
  - Codex/Claude 聊天记录迁出 C 盘的 junction 方案与边界
does_not_own:
  - 实际删除或迁移的执行回执（执行后另记 PLAN/LOG）
  - Windows 自动更新策略（RESEARCH-100）
  - VoiceOver P005/P006 生产产物的取舍标准（voiceover 仓 PLAN-320 / SOP-080）
  - TB25 media_meta 库的权威清单（tb25-media-meta 回复为准）
read_when:
  - 决定 tb24 E 盘删什么、先删什么
  - 处理 C 盘反复触顶、评估聊天记录搬迁对 find-session 的影响
  - 与 tb25-media-meta 核对个人照片视频是否已被 TB25 全量覆盖
related:
  - RESEARCH-100
last_reviewed: 2026-09-19
---

# tb24 E 盘全盘彻查：零决策可释放清单、待拍板清单与聊天记录搬迁方案

## 0. 先回答三个问题

**问题一：E 盘为什么只剩 79 GB？** 1,863 GB 里四块大头占了 1,560 GB：个人照片视频 749 GB（其中约 281 GB 是同一批文件的第二份逐字节副本）、VoiceOver 生产中间文件 259 GB（P005 从 9 月 12 日到 18 日的每一轮渲染、分轨、审阅版都留在 `E:\410_VibeCoding\Post\voiceover\runs\`）、硕士资料 308 GB、下载与微信 280 GB。P005 确实是最近的增量来源：`runs\PLAN-310` 118 GB + `runs\PLAN-301` 61 GB + `runs\PLAN-300` 14 GB = 193 GB 全是 9 月 12 日以后生成的。

**问题二：零决策能删多少？** 约 **341 GB**（两轮核验后），删完 E 盘剩约 **425 GB**。这 341 GB 只含三类东西：① 同一块盘上已全量核过哈希的第二份副本（281 GB）② Chrome 临时用户目录和飞书上传回读副本（44 GB）③ 回收站（16 GB）。删掉它们不会让任何一份内容从这台电脑消失。

**问题三：照片视频要不要留？** 已与 TB25 真实目录清单机器比对（§5）：TB25 按名字+大小覆盖了 TB24 全部 583 GB 视频和 94.8% 的照片；真正只在 TB24 有的只剩 198 个文件 / 8.75 GB，加 972 个 Live Photo MOV 版本差 3.86 GB。先把这 12.6 GB 搬去 TB25，再删 TB24 的 510_视频 + 500_照片 可再释放 **658 GB**，E 盘可到约 **1,080 GB**。这一步删的是 TB24 上的最后一份，要主人拍板。

**C 盘问题：** 不需要改任何路径。用 NTFS 目录联接（junction）把 `sessions/`、`projects/` 的实体搬到数据盘，原路径保留为入口，Codex、Claude Code、find-session、Link16 桥全部照旧（§6）。

---

## 1. E 盘现在装了什么

扫描时间 2026-09-19 22:58，逻辑大小（NVMe SSD，无压缩、无稀疏文件）。

| 一级目录 | 大小 | 性质 |
|---|---|---|
| `E:\510_视频` | 583 GB | iPhone/DJI/Insta360 视频导出（2025-10 前后） |
| `E:\410_VibeCoding` | 415 GB | 所有代码仓；其中 voiceover 275 GB、xhs-card-gen 98 GB |
| `E:\Documents` | 363 GB | `硕士` 308 GB、`本科` 55 GB |
| `E:\Downloads` | 201 GB | 微信文件 75 GB（与 `xwechat_files` 硬链接共享 67 GB）、Bandicam 60 GB、百度网盘 32 GB、Chrome 下载 32 GB |
| `E:\xwechat_files` | 79 GB | 微信 4.0 数据（大部分与上一行是同一批物理文件） |
| `E:\Seafile` | 78 GB | Seafile 同步目录 |
| `E:\500_照片` | 74.6 GB | iPhone 照片导出 2010–2025 |
| `E:\50_photo` | 74.6 GB | **`500_照片` 的逐字节重复副本** |
| `E:\Book` | 26 GB | 音乐 18 GB 等 |
| `E:\$RECYCLE.BIN` | 16.4 GB | 回收站 |
| `E:\Contest` / `GitHub` / `Courseware` / `51_video` | 13 / 12 / 10.5 / 12.3 GB | 竞赛、旧仓库、课件、老视频 |
| `E:\C盘安全归档\2026-08-15` | 9.1 GB | 8 月 15 日从 C 盘迁出的相机胶卷、腾讯会议录像、Telegram 视频 |

VoiceOver `runs\` 259 GB 的内部分布：

| 子目录 | 大小 | 内容 |
|---|---|---|
| `PLAN-310` | 118 GB | P005 声音返工与最终成片：参考片分轨 41 GB（`reference-stem-bank-*`）、`production-v1/v2/v3` 每一轮整片渲染各 4–5 GB、飞书回读探针 6 GB、浏览器运行时残留 |
| `PLAN-301` | 61 GB | P005 主人审阅修订：`phone-review-v1/v2`、`preview-first-v2..v5b`、`full-runtime-v1..v4` 等每轮完整渲染 |
| `P004` | 23 GB | P004 帧图 16 GB（`S70\frames*` 三套） |
| `PLAN-300` | 14 GB | P005 首版与 9 月 12 日恢复渲染 |
| `P003` / `P002` / `P001` | 13 / 10.6 / 4.8 GB | 历史各期 |
| `P005` | 5.9 GB | P005 正式产物目录（最终视频经 `deliveries\P005` 硬链接引用） |

---

## 2. 零决策可删清单（Tier 0）

判据：删除后这台电脑上不会少任何一份内容——要么同盘另有一份哈希相同的副本，要么是工具自动生成、下次运行自动重建的残留，要么是回收站。

| # | 路径 | 释放 | 为什么零决策 | 验证（两轮） |
|---|---|---|---|---|
| T0-1 | `E:\50_photo\` 整个目录 | **74.6 GB** | 与 `E:\500_照片\` 31,944 个文件相对路径、大小全部一致；保留较新的 `500_照片` | 全量 31,944/31,944 SHA-256 一致；第二轮：certutil MD5 抽样 25/25 一致、两目录文件 ID 全不同（真两份，不是硬链接/联接）、`500_照片` 反向也没有 `50_photo` 缺的文件 |
| T0-2 | `E:\510_视频\PhotoSync_202307_20251011视频备份\ZhuZhen\最近项目\` 中与 `20230730_20251007_iphone11_iphone16pro\` 哈希相同的 1,022 个文件 | **205.9 GB** | 同一批 iPhone 视频，PhotoSync 用 UUID 命名存了第二份；只删哈希对上的那 1,022 个，另 120 个（13.7 GB）没有对应原件、保留 | 全量 1,022 对 SHA-256 一致、0 对大小同内容异；第二轮：certutil MD5 抽样 20/20 一致、文件 ID 全不同、无 dup==keep、两目录都只有 MOV/MP4 无 sidecar |
| T0-3 | `E:\$RECYCLE.BIN` | **16.4 GB** | 回收站 | 第二轮列了全部 65 项：15.2 GB 是 2025-11-24 删的 4 个安装包 ZIP 及其解压目录（Analog Lab、Ableton Lite、TPO、VibrationData），其余全是 KB 级图片；最新一项 9 月 8 日的 25 MB `IMG_5568.MOV` |
| T0-4 | `runs\` 里 205 个 `chrome-<pid>\` 目录（HyperFrames 每次真机播放/渲染开的 Chrome 临时用户目录） | **38.0 GB** | 里面只有 Chrome 自己的东西：`Local State`、`Default\`、`component_crx_cache`、`optimization_guide_model_store\*.tflite`、扩展缓存；进程早已退出。真正的证据——`observations.json` 和抽样 `frame-*.png`——在它的上一级 `CH0N-runtime\`，不删 | 第二轮改为按内容签名认定（必须同时有 `Local State` 文件 + `Default\` 目录，且内部不含 `observations.json` / `frame-*.png` / 非扩展的媒体文件）：205/205 通过，比第一轮多出 69 个（13.3 GB）第一轮按父目录名漏掉的同类目录；`runs` 里所有 json/log 中没有任何指向这些目录内部的引用（只有 2 份全盘媒体路径扫描清单顺带列到了扩展自带的教程 mp4）；当前无进程使用 |
| T0-5 | `runs\PLAN-310\**\*online-readback*\assets\`、`stage38-feishu-doc-read-v*\assets\`（25 个目录、43 个 `file-NNNN.bin`） | **6.5 GB** | 把已上传飞书的视频/音频再下载回来核对播放的副本；`manifest.json` 等回执保留 | 第二轮：43/43 个 .bin 都按 manifest 里的文件名在 `runs`/`deliveries`/`posts` 找到同名、同大小、同 SHA-256 的本地原件 |
| 合计 | | **≈ 341 GB** | 删后 E 盘剩约 425 GB | |

第一轮列过的 `render-temp\`（0.2 GB）第二轮剔除：`PLAN-300\render-temp` 里除 node 编译缓存外还有 `hf-de-verify-fail-*` 失败现场，不碰。

### 2.1 核验方法与结果（第一轮 23:41–23:51，第二轮 00:00–00:20）

第一轮：6 进程并行、完整文件 SHA-256（不是抽样）。

- `50_photo` vs `500_照片`：31,944 / 31,944 个文件哈希一致，74.60 GB；0 个不一致、0 个缺失。
- PhotoSync 备份 vs iPhone 目录：1,022 个文件哈希一致，205.89 GB；0 个大小相同但内容不同；另 120 个（13.66 GB）没有对应原件，保留不删。

第二轮（主人要求"全面核实一遍"）：换工具、换判据、查引用。

- 用 Windows 自带 `certutil` 算 MD5 复核抽样，与 Python SHA-256 结论一致；核对文件 ID 证明两份是独立字节，不是硬链接或联接。
- 查计划任务、Windows 库（`*.library-ms`）、注册表 Shell Folders、桌面/开始菜单/最近使用的快捷方式、`AppData`、PhotoSync/PhotoPcReceiver/Seafile/i4Tools 配置：没有任何一处引用 `50_photo`、`PhotoSync_202307…视频备份`、`510_视频`、`500_照片`。
- Chrome 目录改按内容签名认定并逐个检查内部有无证据文件；飞书回读 .bin 逐个反查本地原件哈希。
- 现场：P006 此刻在 Stage 2 写稿（`runs\P006\editorial-story.json`），没有渲染、没有 Chrome 进程；`runs` 里只有 5 个 9 月 18 日残留的 HyperFrames preview（服务 `avatar-pilots-stage35-v1\P0xx-final-avatar-project`，不在删除目标内）。

结果写入 `dup_verify_photo.json`、`dup_verify_video.json`（每个文件的相对路径、大小、SHA-256）和 `tier0_runs_manifest.json`；全部清单、脚本与两台机媒体清单已落盘到 `E:\410_VibeCoding\Lab\2026-09-19-tb24-disk-audit\`（不进仓）。删除脚本 `tier0_delete.py` 只按这三份清单逐路径删，删前再核一次大小和 Chrome 签名，无通配符、无变量拼接；`--dry-run`：photo 31,944 文件 74.6 GB、video 1,022 文件 205.9 GB、runs 230 目录 44.5 GB，合计 325.0 GB，加回收站 16.4 GB ≈ 341 GB。

### 2.2 明确不在 Tier 0 里的东西

- `runs\PLAN-310\reference-stem-bank-resume-20260915-v1\`（32.9 GB）：20 期参考片的分轨成品，RESEARCH-100 的分析已用它出结论，但 P006 配乐参照可能还会读——归 Tier 1。
- `runs\` 里每一轮 `full-render-*`、`preview-first-*`、`phone-review-*`（约 128 GB）：P005 迭代历史，最终版已在 `deliveries\P005`；哪些是评价卡引用的证据要 tb24-voiceover 判——归 Tier 1。
- `posts\_failed\`（1.5 GB）：`.gitignore` 明写"作为证据保留"。
- `E:\C盘安全归档\`：8 月 15 日主人确认迁出的个人视频，不是缓存。

---

## 3. 只要主人点头即可删（Tier 1）

| # | 路径 | 释放 | 依据 | 建议 |
|---|---|---|---|---|
| T1-1 | `runs\PLAN-310\reference-stem-bank-v1\` | 7.8 GB | 9 月 15 日中断的旧分轨版本，`-resume-20260915-v1` 已 20/20 完成并写入 PLAN | 删 |
| T1-2 | `runs\P001`、`P002`、`P003`、`P004`（含 P004 三套帧图 11 GB） | 52 GB | 已发布各期的中间文件；正式产物在 `posts\` 与 `deliveries\` | 请 tb24-voiceover 确认无评价基线引用后删 |
| T1-3 | `runs\PLAN-300/301/310` 里被后续版本取代的整片渲染（v1/v2 等） | 约 100–128 GB | P005 已发布，最终版 `final-render-stage36-v1` 与 `deliveries\P005` 保留 | 请 tb24-voiceover 出保留清单（最终版 + 评价引用的版本），其余删 |
| T1-4 | `runs\PLAN-310\reference-stem-bank-resume-20260915-v1\` | 32.9 GB | 分轨成品；源视频可重下、可重算（每期约 10 分钟） | P006 若不再做全库音乐分析则删 |
| T1-5 | `E:\410_VibeCoding\Post\xhs-card-gen-ban020-before\`、`xhs-card-gen-P47-P48\` | 6 GB | 旧仓库快照副本，主仓 git 历史都在 | 删 |
| T1-6 | `E:\410_VibeCoding\Post\voiceover-release-snapshots\`、`video-studio-release-snapshots\` | 3.6 GB | 2026-08-11 隔离快照 | 删 |
| T1-7 | 5 个 9 月 18 日残留的 HyperFrames preview 进程（`avatar-pilots-stage35-v1\P0xx-final-avatar-project`） | 0 GB（只占内存/端口） | 数字人试播预览，任务已完成 | 结束进程 |

---

## 4. 要主人自己决定（Tier 2，个人数据）

| 路径 | 大小 | 说明 |
|---|---|---|
| `E:\Documents\硕士` | 308 GB | 硕士阶段数据/视频 |
| `E:\Downloads\Bandicam` + `Bandicam2` | 60 GB | 屏幕录像（含面试录像） |
| `E:\Downloads\WeChat Files` + `E:\xwechat_files` | 实占约 87 GB | 微信数据；两处硬链接共享 67 GB，删一处几乎不释放，要一起处理 |
| `E:\Downloads\BaiduNetdisk` | 32 GB | ZIP 与解压后素材并存 |
| `E:\Downloads\Chrome` + `Chrome02` | 32 GB | 旧 ISO、重复安装包 |
| `E:\Seafile` | 78 GB | 同步目录 |
| `E:\C盘安全归档\2026-08-15` | 9.1 GB | 相机胶卷、会议录像、Telegram 视频 |
| `E:\Book` / `Contest` / `Courseware` / `GitHub` | 62 GB | 音乐、竞赛、课件、旧仓库 |

---

## 5. 个人照片视频 vs TB25（已比对，2026-09-20 00:47–00:58）

tb25-media-meta 当时挂在 Codex profile 上、Codex 无额度冷启不起来，我的请求被主人 00:32 的 /close 撤掉，它没处理过任何东西。改由 tb25-link16 直接读 TB25 的真实媒体目录（`D:ŀ_photo`、`D:ň_video`、`D:Ő_staging`、`D:Ř/540/550`，共 56,642 个文件 / 1,346.7 GB）导出 relpath/bytes/mtime 清单（`D:Ř_media_meta\scans	b25_media_inventory_2026-09-20.csv`，00:46 发到群），在 TB24 与本机 66,800 行清单机器比对。判据：文件名+大小相同 = 同一文件；文件名不同但字节数完全相同且 ≥1 MB = 同一文件的改名副本（多 MB 级视频字节数撞车概率可忽略）。**不是哈希级**：TB25 侧只给了名字和大小；要 SHA 级证据得让 TB25 再跑一遍哈希（922 GB 约一小时），可按需追加。

| TB24 目录 | 文件 / 大小 | TB25 覆盖 | 结论 |
|---|---|---|---|
| `E:ň_视频30730_20251007_iphone11_iphone16pro` | 1,085 / 225.1 GB | 1,085/1,085 名字+大小一致（在 `D:ň_video20826_20260730_iphone11_iphone16pro`，TB25 那份还多 1,107 个文件） | **全覆盖** |
| `E:ň_视频50829_1003_DJIpocket3` | 408 / 124.8 GB | 408/408 | **全覆盖** |
| `E:ň_视频\PhotoSync_202307_20251011视频备份` | 1,142 / 219.5 GB | 1,142/1,142 | **全覆盖**（含 TB24 上没原件的那 120 个） |
| `E:ň_视频30731-0808…insta360Go3` | 65 / 13.8 GB | 65/65 | **全覆盖** |
| `E:ŀ_照片00129_20251011_iphone_photo` | 31,944 / 74.6 GB | 30,969 名字+大小一致 + 3 大小一致（70.7 GB）；**972 个 Live Photo 的 .MOV（3.86 GB）TB25 有同名文件但字节数不同**（TB25 多为更大的新导出） | 覆盖 94.8% 字节；972 个 MOV 是"同名不同版本" |
| `E:)_video`（含 `硕士视频`） | 26 / 12.3 GB | 8 个按大小对上（在 TB25 的 PhotoSync 目录，UUID 名，8.1 GB）；**18 个不在 TB25：4.2 GB**——`20101218石门森林公园.mp4` 1.27 GB、`20091101青秀山亲子活动.mp4` 0.57 GB、`2024_06_21_学部24级欢送会.mp4`、`硕士视频` 里 15 个 2025-04 的 MOV 2.34 GB | 部分缺 |
| `E:\PhotoSync\ZhuZhen`、`ZhuZhen (2)` | 12 / 2.9 GB | **9 个不在 TB25：2.95 GB**（大头 `Flow_VID_20260407_163925_02_090.MOV` 2.9 GB，2026-04 的新片） | 缺 |
| `E:\i4Tools7` | 3 / 1.5 GB | **不在 TB25**：`DJI_20250411163853_0239_D.mp4` 1.36 GB + `i4AirPlayer3.zip` | 缺 |
| `E:\图片` | 171 / 0.14 GB | 不在 TB25（桌面截图，不属于相册） | 缺，但不是相册 |

汇总：TB24 上共 749 GB 个人媒体（含 281 GB 同盘副本）。**TB25 按名字+大小覆盖了其中 725 GB；真正只在 TB24 有的是 198 个文件 / 8.75 GB**（清单 `E:\410_VibeCoding\Lab\2026-09-19-tb24-disk-audit\tb24_absent_on_tb25.csv`），另有 972 个 Live Photo MOV / 3.86 GB 是同名不同字节的版本差。

建议顺序（都要主人拍板，这是删 TB24 上"最后一份"）：

1. 先把 8.75 GB 真缺的 + 3.86 GB 版本差的 MOV 搬到 TB25（12.6 GB，U 盘或 OSS 中转；tb24 没配 envsync）。
2. 搬完、TB25 侧核对到位后，删 TB24 的 `E:ň_视频`（583 GB）与 `E:ŀ_照片`（74.6 GB，`50_photo` 已在 Tier 0）→ E 盘再 +658 GB。
3. `51_video`、`PhotoSync`、`i4Tools7`、`图片` 在第 1 步搬走后一并删（约 17 GB）。

---

## 6. C 盘：为什么清了又满，怎样一次到位

### 6.1 现状

C 盘 249 GB，剩 9.7 GB。用户目录 145 GB 里持续增长的是：

| 对象 | 现在 | 近 7 天新写入 |
|---|---|---|
| Codex 聊天记录 `~\.codex-personal\sessions` | 14.7 GB（8 月 15 日 9.2 GB） | 1.0 GB |
| Claude 聊天记录 `~\.claude-personal\projects` + `~\.claude-personal2\projects` | 3.5 + 2.1 GB | 0.8 GB |
| `AppData\Local\Temp\claude\bash-edit-diff` | 2.8 GB / 13,814 文件 | 全部 |
| `AppData\Local\npm-cache` | 10.6 GB | 1.8 GB |
| `AppData\Roaming\LarkShell`（飞书客户端缓存） | 4.0 GB | 3.2 GB |
| `AppData\Local\Google`（Chrome） | 8.5 GB | 1.3 GB |
| `miniconda3\pkgs`、`.cache\huggingface`、pip 缓存 | 4.3 + 3.1 + 1.2 GB | 1.2 GB |

聊天记录增速约 7 GB/月（8 月 15 日至今 Codex +4.8 GB、Claude +3 GB）；C 盘每次清出 20 GB，两三个月就回到 10 GB 以下，这就是"清了又清还是满"的机制。

本机联接早已在用：`~\.claude-personal2\skills`、`commands`、`memory` 都是指向 `~\.claude-personal` 的 junction，当前这个会话（profile ccp2）读技能就是走的联接。

### 6.2 方案：搬实体、留入口——不改任何默认路径

用 NTFS 目录联接（junction，`mklink /J`）：把 `%USERPROFILE%\.codex-personal\sessions` 整个搬到 `E:\...\codex-personal\sessions`，再在原位置建一个同名联接指向它。对所有程序来说，`%USERPROFILE%\.codex-personal\sessions\2026\09\...` 这个路径继续存在、继续可读可写，只是字节落在 E 盘。

为什么它不影响任何现有工具：

- Codex CLI 和 Claude Code 只按 `CODEX_HOME` / `CLAUDE_CONFIG_DIR` 下的相对路径打开文件，Win32 打开联接目录里的文件与打开普通目录完全相同，它们感知不到。
- `find-session` 的发现逻辑是 `home / "sessions"` 然后 `rglob("rollout-*.jsonl")`（`~\.claude-personal\scripts\session_runtime.py` 第 171–191 行），联接目录能正常枚举；路径归属校验两边都做了 `resolve()`（第 443–452 行），联接解析后仍在同一真实目录下，不会报"path is not owned"。
- Link16 桥的 session pin/resume 用的是 session id 和同一套 home 路径，不看目录是不是联接。
- 主人的 `~\.claude-personal\skills` 本来就是靠联接分发到 `.claude`、`.claude-work*` 的，这套机制在本机已经跑了两个月。

所以不存在"为了 C 盘不满就必须改路径、牺牲 find-session"的 trade-off；联接就是两者兼得的标准做法。

### 6.3 具体搬什么、搬到哪

| 对象 | 现大小 | 目标 | 方式 |
|---|---|---|---|
| `~\.codex-personal\sessions` | 14.7 GB | `E:\410_VibeCoding\_agent-home\codex-personal\sessions` | robocopy 复制 → 校验 → 删原目录 → `mklink /J` |
| `~\.codex\sessions` | 约 1 GB | 同上 `codex\sessions` | 同上 |
| `~\.claude-personal\projects` | 3.5 GB | 同上 `claude-personal\projects` | 同上 |
| `~\.claude-personal2\projects` | 2.1 GB | 同上 `claude-personal2\projects` | 同上 |
| `AppData\Local\npm-cache` | 10.6 GB | `E:\410_VibeCoding\_agent-home\npm-cache` | `npm config set cache`（npm 官方支持）；`_npx` 里 HyperFrames 正在运行，改配置后由新下载自然接管 |
| `AppData\Local\Temp\claude\bash-edit-diff` | 2.8 GB | 不搬，定期删 1 天前的 | Claude Code 的 bash 编辑差异快照，会话结束即无用 |
| `miniconda3\pkgs` 压缩包、pip 缓存 | 约 5 GB | 不搬，`conda clean --tarballs`、`pip cache purge` | 官方清理命令 |

预期：C 盘立即多出约 35–40 GB，且以后聊天记录和 npm 缓存的增长全部落在 E 盘；C 盘剩余只随 Windows 更新、Chrome、飞书客户端缓慢变化。

目标盘选 E 而不是 D 的理由：清完 Tier 0 后 E 盘有 400 GB 以上余量，几年内不需要再挪；D 盘只剩 62 GB 且装满了软件。代价是 E 盘不能再被批量任务写满——VoiceOver 分轨那天一次写了 93 GB，所以 §7 给 E 盘定 200 GB 低水位，低于就拒绝开新的渲染/分轨任务。

### 6.4 执行边界

- 搬 `sessions/` 时对应 profile 的 Codex/Claude 进程必须先停（正在追加写的 JSONL 搬不动），操作窗口约 10 分钟；先复制、逐文件比对大小与哈希，全部通过才删原目录、建联接，任一失败就停在"复制完成、原目录未动"的状态。
- 不改 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`、Link16 registry、任何 `.env`。
- 不动 Windows 全局 `TEMP`、不关休眠（主人 8 月 15 日已明确不考虑）。
- 搬完后 `find-session` 跑一次已知片段作验收；桥 `feishu_bridge.py` 重启一次验证 session pin。

---

## 7. 建议执行顺序与安全规则

1. 等 §2.1 全量哈希跑完 → 主人回一句"Tier 0 删" → 按 `dup_verify.json` 和 `tier0_runs_manifest.json` 逐路径删（Python 显式路径，无通配符、无变量拼接），每类删完报实际释放数。
2. 等 tb25-media-meta 回复 → TB25 缺的先搬过去核验 → 主人拍板后删 §5 的 468 GB。
3. Tier 1 由 tb24-voiceover 给保留清单后删；T1-1、T1-5、T1-6 主人点头即删。
4. C 盘按 §6.3 执行，安排在没有 P006 渲染任务的时段。
5. 长期规则：E 盘低水位 200 GB、C 盘低水位 40 GB；VoiceOver 分轨/渲染脚本开工前估算最坏占用，低于水位拒绝开工（RESEARCH-100 已提，P006 途中按 SOP-080"就地最小改动"接入）。

---

## 8. 回执

- 2026-09-19 22:58｜全盘扫描完成：E 盘逻辑 1,987 GB（含微信硬链接重复计数 67 GB），实际占用 1,784 GB。
- 2026-09-19 23:12｜`50_photo` 与 `500_照片` 抽样 40/40 一致；PhotoSync 与 iPhone 目录大小配对 1,022 个、抽样 30/30 一致。
- 2026-09-19 23:26｜TB24 媒体清单已发 tb25-media-meta（群 `tb24-25交流水吧`，message_id `om_x100b65de392384a0b3e101fa6393ac1`）。
- 2026-09-19 23:33｜全量 SHA-256 核验启动；单线程版 145 MB/s 太慢，23:41 改为 6 进程并行重跑。
- 2026-09-19 23:51｜核验完成：照片 31,944/31,944 一致（74.60 GB）、视频 1,022 一致（205.89 GB）、0 不一致；`tier0_delete.py --dry-run` 合计 311.9 GB + 回收站 16.4 GB。E 盘此刻剩 85.2 GB。等主人拍板后执行。（已于 2026-09-20 01:00 执行完，见回执末尾）
- 2026-09-19 23:51｜tb25-media-meta 自 23:26 起持续回进度卡，尚未给出最终判定；§5 结论待补。
- 2026-09-20 00:37｜主人指出 tb25-media-meta 起不来、我误把它的启动心跳卡当成进度卡；改请 tb25-link16 代查并直接导 TB25 清单。
- 2026-09-20 00:58｜tb25-link16 00:46 发回 TB25 清单（56,642 行）；机器比对完成：510_视频 2,700/2,700 全覆盖，500_照片 30,972/31,944 覆盖、972 个 Live Photo MOV 同名不同大小，真缺 198 个 / 8.75 GB。§5 改写。
- 2026-09-20 00:20｜主人要求 Tier 0 全面再核一遍。第二轮：certutil MD5 抽样、文件 ID、反向集合、配置/任务/快捷方式引用扫描全部通过；Chrome 目录改按内容签名认定，多找到 69 个同类目录（+13.3 GB），`render-temp` 因含失败现场剔除；飞书回读 43/43 反查到本地原件。Tier 0 由 328 GB 修正为 341 GB，脚本 `--dry-run` 325.0 GB + 回收站 16.4 GB。E 盘此刻剩 85.1 GB。
- 2026-09-20 00:56｜主人拍板"同意删除 Tier 0，先做 Tier 0"；12.6 GB 搬 TB25 作为下一步，C 盘 junction 主人再考虑。
- 2026-09-20 00:57–01:00｜Tier 0 执行完毕（`E:Ĉ_VibeCoding\Lab6-09-19-tb24-disk-audit	ier0_delete.py --apply photo/video/runs` + `Clear-RecycleBin -DriveLetter E`）：photo 31,944 文件 74.6 GB、video 1,022 文件 205.9 GB、runs 230 目录 44.5 GB、回收站 15.4 GB，skipped/mismatch 全 0。E 盘可用 85.0 → 426.6 GB（+341.6 GB）。`E:(_photo` 已不存在；PhotoSync 备份目录剩 120 个无原件文件；`CH0N-runtime\observations.json` 与 `frame-*.png` 原样保留。
