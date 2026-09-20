---
doc_type: SOP
doc_id: SOP-010
title: 将已有 HTML 与交互资源发布为妙搭应用
status: active
purpose: 将用户已有网页增量发布到稳定妙搭入口，并验证资源、交互和实际发布版本。
owns:
  - HTML 到妙搭发布的输入清单和验证流程
  - Link16 发布资源清单与回执工具的调用
does_not_own:
  - 妙搭平台 CLI 命令合同与认证
  - 各网页的业务算法或后台算力
  - 飞书消息发送授权和出站路由
read_when:
  - 用户要求把已有 HTML、视频交互或三维查看器变成可分享的妙搭应用
last_reviewed: 2026-09-10
---

# 已有 HTML → 妙搭应用

把现有页面、脚本和资源作为输入，保留用户要的布局与交互，交付稳定在线入口、准确版本和浏览器验收。复用已安装的 `$lark-apps`；先读该 skill，再按本次应用类型读取 local-dev、file、release 和 access-scope reference。没有安装时明确缺失的工具入口，不复制一套平台 API。

## 1. 指认输入和已有应用

从当前项目、用户链接和 `.spark/meta.json` 发现入口、运行方式和 app_id。已有应用继续使用同一 app_id 与路由；内部开发版本不要求用户换链接。保留原 Git 历史和无关改动。

用户已经要求修改本地 HTML、继续现有代码或完整发布时，沿用该开发方式与授权，不重新设置确认关卡。只有真实缺失且影响对象、访问范围或不可逆操作的信息才需要补问。

明确本地项目根、HTML 入口、需要保留的交互和资源允许清单。资源清单用根目录相对路径 JSON 数组，例如 `["index.html","viewer.js","style.css","model.glb","clip.mp4"]`。在 Link16 根目录运行：

```text
python feishu/miaoda_delivery.py inventory --root <网页目录> --entry index.html --files <允许清单.json> --output <资源回执.json>
```

清单测量文件字节数和 SHA-256，不自动解析所有动态依赖。检查脚本 import、CSS 字体/图片、fetch、Worker/WASM、视频/HLS 分段、三维模型与贴图；显式补齐需要的文件，不整仓上传。密钥、环境文件、虚拟环境与 node_modules 不进入交付包。

## 2. 适配和发布

按 lark-apps 选择应用类型并初始化独立应用仓库；已有仓库继续使用。少量不可变静态资源可随明确允许清单进入该应用 Git，例如短视频、模型和贴图，先核对实际字节数与仓库限制；大媒体或需独立权限的资源使用该应用文件存储。存储资源由运行时 SDK 获取有效地址，保留上传元数据和散列回执，不把临时签名 URL 固化进源码，不跨 app 复用存储地址。体积限制以当前 file reference 为准；大视频可以完整转为分段媒体，不截断比赛。

Windows 初始化若报 GNU tar 将 `C:` 当作远端主机，先检查实际 tar 路径；只在该 CLI 子进程的 PATH 前放入 Windows 原生系统目录（用系统 API 解析），不改全局 PATH 或认证。保留失败后产生的部分目录，确认原 app_id 后在新的明确目录继续初始化，不重复创建应用。

先让原页面在本地真实运行，再做必要的平台适配。嵌套路由、平台 URL 规范化、`document.baseURI` 和 MIME 类型必须用实际浏览器检查；浏览器直接拿到未构建模块并不等于模块能执行。网页发布不自动提供 Python 推理、硬件云台或本地磁盘访问。

运行项目已有检查并审阅差异，commit、push 精确提交，再通过 lark-apps release-create 发布。记录 release ID 后只查询该 release；结果不明先查 release-list/已有回执，不盲目再次创建。发布完成后运行：

```text
python feishu/miaoda_delivery.py verify-release --project <应用仓库> --release-id <ID> --expected-commit <完整SHA> --output <发布回执.json>
```

工具读取 `.spark/meta.json`，使用当前 lark-cli 用户身份回读发布状态；只有当前工作流已经指定 CLI profile 才传 `--cli-profile`，不要把 Link16 agent profile 当成 lark-cli profile。该工具不创建发布、不改访问权限、不发送消息。旧版没有 Git 的应用仅按 lark-apps 兼容流程处理，不能把旧上传接口作为新链路失败的绕过方式。

## 3. 验证交互和交付

分别记录发布版本、资源完整性和真实页面使用结果。按用户的访问范围测试：需要登录的应用不能因匿名打开登录页而宣称业务已通过，也不能擅自改成公开访问。

创意模式 HTML 的 `+access-scope-get` 可能返回“不支持查询可用范围”（40002）。保存该结果，并用实际浏览器记录当前访问行为；未得到范围元数据时明确写未知，不把查询失败解释成 private/public，也不通过改权限消除测试失败。

有视频时实际播放并跳转到中段/末段；有三维内容时实际加载模型、旋转/缩放并检查 WebGL 错误；有下载/保存时按原流程验证。使用真实截图和实际网络响应，包含目标设备尺寸与错误记录。缓存命中、首次访问和业务准确性分开报告。每项检查说明 covers、caught、judge；构建成功不替代浏览器验证。

视频跳转必须核对服务器的字节范围请求与 `206 Content-Range`；简单静态服务器能显示首帧也可能无法跳转。在线资源还须核对最终地址及实际 MIME，`200 text/html` 的登录页或错误页不算视频成功，不凭文档域名猜测应用存储域名。

给出本机绝对回执路径、实际 online_url、release ID、提交 SHA 和已知限制。在线说明与媒体交付遵守 feishu 主 skill 的 artifact_delivery 策略；群通知必须有单独授权，自动回址不手动重复发送。用户要求同一链接时回交原路径，并确认新 release 对应同一应用。
