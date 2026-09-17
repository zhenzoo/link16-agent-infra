---
doc_type: SOP
doc_id: SOP-141
title: 飞书文档原生媒体上传与在线审阅
status: active
purpose: 将本地审阅媒体放进飞书文档正文，并用实际播放器验证无需保存原文件即可查看。
owns:
  - 原生媒体块的发布和播放验收顺序
  - 单次上传与分片上传的选择
  - 原生媒体审阅失败的处理
does_not_own:
  - 在线发布授权与回执格式（属 SPEC-210）
  - 文档通用权限和读写流程（属 SOP-140）
  - 影片内容、音量或画质标准
read_when:
  - 向主人交付图片、视频或音频的在线审阅链接
  - 文档内媒体点击后只出现下载而不能播放
last_reviewed: 2026-09-14
related:
  - SOP-140
  - SPEC-210
---

# 飞书文档原生媒体审阅

主人要的是打开文档后直接看图、点播音视频，不必把原文件保存到手机。播放器仍会使用网络、运行内存和临时缓存，不能承诺“零内存”。成功上传或出现文件名都不等于达到这个目标，最后必须验证真实文档里的播放器。

## 发布与验证

1. 运行 `python feishu/artifact_delivery.py decide --json`，遵守当前在线开关或本轮明确的在线授权；确认媒体已冻结，记住本地路径和大小。
2. 使用已有媒体入口。自动回址的 p2a 会话优先只创建文档，避免验证前另发一条消息：

   ```text
   python feishu/send_feishu_media.py --bot <本轮bot> --media <精确文件> --title <标题> --publish-only --receipt <新的回执.json> --verify-out <新的验证目录>
   ```

   多个 `--media` 可在同一文档混排，`--caption` 与媒体一一对应。**插入已有文档**仍用这条命令，加 `--document <docx链接或token>`，默认追加到末尾；指定父块与位置时加 `--parent-block <块ID> --index <0起位置>`，caption和媒体按顺序插入。工具先回读父块，位置越界或不可达时不写入；已有正文、标题和分享权限不变。wiki链接先用 `docio_cli.py inspect` 解析，不能把wiki节点当docx token。

   `--verify-out` 自动调用下述真实页面验证器，逐项核对源SHA、块ID、file token、实际加载／播放；任一失败记 `preview_failed` 并停止发消息，保留文档和报告供续查，不能盲目重新创建。成功才记 `preview_verified=true`；它证明本次浏览器页面路径，不代表已在人手机App或人工听过。只在本轮明确要求在线稿而全局开关关闭时加 `--explicit-online`。不要改用普通文件附件，也不要把对象存储外链称为正文播放器。
3. 发布回执包含源文件 SHA256、字节数、文档 URL、内层媒体块 ID、文件 token、上传路径，以及独立回读的 token 绑定结果。`playback_verified=false` 是有意保留的状态：API 只能证明绑到了文件，不能证明播放器已经可用。
4. 统一入口已带 `--verify-out` 时检查生成报告即可。分开调试或接续已上传文档时，用下面的验证器，勿重复上传：

   ```text
   python feishu/verify_media_doc.py --url <回执url> --block-id <items中的block_id> --file-token <items中的file_token> --media <同一精确文件> --out <新的验证目录> --mobile
   ```

   浏览器必须实际加载对应媒体、点击播放，并在开头、中间和结尾继续播放；报告保留本地文件身份、远端时长、播放状态和下载事件。图片须在对应块加载出实际图像。没有下载事件只是本次浏览器路径的观察，不表示客户端永远不缓存。`--mobile` 是手机浏览器模拟，不能写成已在主人手机飞书 App 实测；也不能把浏览器解码称为人工听过。
5. 回读报告、查看截图，确认源文件身份与发布回执一致，再用 `artifact_delivery.py receipt` 输出规定的路径和链接。记录审核范围及仍未完成的内容检查。在线失败时保留失败证据和已创建文档，不自动改成附件。

## 工具怎样处理大小和块类型

`feishu_docs.publish_media_as_doc` 是实现真源。图片用图片块；支持预览的音视频创建 `file.view_type=2`，API 返回外层 View 块和内层 file 块，上传与绑定使用内层 ID。不能将外层 View ID 当作上传父节点。创建后再 PATCH `update_view` 的做法曾返回 1770001，不能把它当成等价入口。

20 MiB 是 `medias/upload_all` 的单次上传边界，不是文档视频的统一上限。较大文件走 `upload_prepare → upload_part → upload_finish`；以服务端返回的块大小和数量为准，每片发送从零开始的序号、真实长度和十进制 Adler-32 校验值，完成后取得 file token，再绑定媒体块。分片失败立即报错，不调用 finish，也不降级发附件。当前工具尚未实现跨进程断点续传。

官方[Drive Media SDK说明](https://larksuite.github.io/oapi-sdk-java/com/lark/oapi/service/drive/v1/DriveService.Media.html#uploadAll-com.lark.oapi.service.drive.v1.model.UploadAllMediaReq-)同时列出单次上传的20MB边界与分片入口；支持分片并非本次服务端新开放。本次补齐的是本地媒体工具对这条能力的使用。

2026-09-14 的真实对照：107,560,331 字节、17:04.933 的 MP4 经 26 片上传成功，在真实飞书文档的手机浏览器模拟中能点播；之后 94,287,189 字节、2:46.233 的修订章用正文 `view_type=2` 发布，开头、中间、结尾均可播放，未触发下载事件。97,125 字节 MP3 的正文播放器与旧卡片预览也分别完成真实点击、三处播放；JPG 核对了实际图像 token、宽高比例和成功加载。由此取消“超过20MB就必须压缩”的工具限制；其他格式、租户限制和更大文件仍以各次实际结果为准，不推断为无限容量。

音频正文样式把可见按钮放在 `.docx-play-button-container`，实际 audio 节点的父播放器可能隐藏；旧卡片预览的按钮则在弹出的播放器内。不要直接点击不可见的 audio 节点或无关播放器的控件。长文档还会按视口装载块：先滚动到目标媒体并等控件就绪，不能把尚未装载的节点记成服务端不支持。

图片解码成功也可能仍处于加载淡入，首张截图曾只留下白底；验证器因此同时等待目标图片及祖先可见、淡入结束和实际绘制后再截图。仅有naturalWidth不够，最后仍复看截图中的真实内容。

同日完整P005的445,957,710字节、17:07.3、1080p版本也经分片上传完成，真实正文播放器在首／中／尾返回206分段响应并持续播放，无下载事件；验证记录在VoiceOver的`runs/PLAN-301/phone-review-v2/full-doc-playback/playback.json`。本次页面播放器音量为0.6、未静音，这与源文件音量是两层状态，应如实记录；不要改大测试端音量后声称源音轨已经增响。

## 失败时先辨别卡在哪里

| 观察 | 下一步 |
|---|---|
| 上传接口拒绝文件大小 | 确认是否仍走 upload_all；检查 prepare 返回的分片计划。不要先猜成文档总大小限制。 |
| 回读 token 不一致 | 视为绑定失败，核对内层 file 块与父节点，禁止宣布上传通过。 |
| 上传成功但暂无播放器 | 等待飞书处理媒体，检查块类型与当前源 token；保留处理中的状态。 |
| 只有文件卡片 | 点击卡片的原生预览入口实测；下一次创建支持预览的媒体时用 view_type=2。文件卡本身不证明只能下载。 |
| 浏览器在登录或授权页 | 按 SOP-140 区分权限与资源分享，不扩大权限或跨 bot 重试来掩盖问题。 |
| 编码格式不能播放 | 制作有记录的兼容审阅副本，重新上传并实测。分辨率和码率由播放结果及审阅用途决定，不固定压到20MB。 |

多音频文档开始播放时可能重排媒体节点。2026-09-15的七条试听文档中，第三项实际为36.83秒，旧验证器保存的位置选择器却在重排后读到第一项57.4秒；网络返回的目标token仍正确。验证器现在把token写入播放器选择器，每次观察再次检查播放源与播放状态，失败时也先保存实际读数。原文档七项首／中／尾复验通过；不能靠放宽时长误差或重传文件掩盖这种选错对象。

验证器、分片与 token 绑定的回归入口分别是 `feishu/verify_media_doc.py`、`tests/test_feishu_media_publish.py`；文本文档回归是 `tests/test_feishu_docs_text_publish.py`。这些结果不代替原影片的声音、字幕和画面验收。

## 入口归属与格式范围

Skill真源在仓库`.agents/skills/feishu/SKILL.md`。维护后用`python feishu/profile_bootstrap.py --profile <当前profile> --skills-only`先预览，再加`--apply`，最后同一范围`--doctor`；这个选项只处理Skill，不改Shell、hooks或其他账号入口。发现旧`claude-compat-feishu`同名路由时，先审阅再使用安装器的`--migrate-legacy-feishu-adapter`备份迁移；有用户独有内容的conflict不能直接覆盖。Codex/Kimi使用`~/.agents/skills/feishu`，Claude使用registry所选home下的安装副本，均以安装manifest和源hash检查漂移。已加载旧说明的长会话须重新读取当前Skill；磁盘同步不等于重写其既有上下文。

`send_feishu_media.py` 是媒体新建／插入的唯一高层入口，复用 `feishu_docs.publish_media_as_doc`；`verify_media_doc.py` 是它的验证器，不是另一套上传器。正文／表格读取、表格写回与权限诊断归 `docio_cli.py`，Markdown／HTML整篇发布归 `feishu_bridge.py send --doc`，聊天语音归 `send_feishu_voice.py`，明确要求的原文件附件归 `send_feishu_file.py`。不要将最后两者用于替代文档内音视频。

实测通用的是JPG、MP3和MP4的文档内插入与预览；其它图片和音视频格式沿同一上传流程，但编码能否被飞书处理仍需逐项验证。PDF／任意文件可创建文件块，当前浏览器验证器不把它们判作已通过的音视频；需要其专属预览检查，不能承诺所有大文件都会有播放器。分片解决传输，不改变格式支持、租户配额或网络带宽。720p和小体积保留为节省流量的可选规格，不能把当前446MB实测外推成无限容量。
