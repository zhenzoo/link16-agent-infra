#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""register_feishu_app.py — 一键创建飞书智能体应用（官方 OAuth Device Grant 流程）。

跑起来打印授权链接：Publisher 在飞书里打开（或手机扫码）确认 → 脚本拿回 client_id/client_secret
→ 自动写进 VibeCoding 的 .env（路径跨机解析·见 bridge_env.resolve_env_path·不写死盘符）。

  默认（建第一个 / 单 bot）：
    python orchestrator/register_feishu_app.py
    → 应用名「tb24-xhs-autopilot」· 写 FEISHU_BRIDGE_APP_ID / FEISHU_BRIDGE_APP_SECRET
  建第 N 个 bot（多 bot · ARCH-101 §4）：
    python orchestrator/register_feishu_app.py --name "xhs总控-ws2" --bot ws2
    → 写 FEISHU_BRIDGE_WS2_APP_ID / FEISHU_BRIDGE_WS2_APP_SECRET（bridge-bots.json 的 app_id_env 指它）

官方 preset 提供消息、事件与 WebSocket 基础能力；注册默认分成两条人工链接：
第一条只创建应用，第二条按 `--capability` 精确列出 tenant 权限供人审阅/发布。
默认 core + group-a2a 不申请 broad Drive 或听全群；公司租户默认再加
docs-consume（Sheet/图片/白板只读）。`--background` 把 Device Grant 和人工步骤监督
从当前 Claude/Codex turn 生命周期中解耦。
"""
import argparse
import json
import os
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

# 🔌 飞书 = 国内端点 → 本进程强制【直连·不走代理】。
# 为什么（2026-07-28 tb24-video-studio 注册实证）：本机开着 Clash（http(s)_proxy=127.0.0.1:7897 +
# Windows 注册表系统代理），OAuth 轮询前 123 次都正常，第 124 次代理回了个 HTML 错误页 →
# SDK 的 resp.json() 抛 JSONDecodeError 整个注册崩掉、device_code 作废、链接得重开。
# 两条都要清：① 环境变量 ② no_proxy（requests 在 Windows 还会读注册表系统代理，靠 no_proxy 才绕得掉）。
# 只影响本进程，不动系统设置。下面 _bot_identity() 早就单独绕代理，这里是把同一条规矩铺到整个注册流程。
for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_pk, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "feishu.cn,larksuite.com,larkoffice.com,localhost,127.0.0.1"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import resolve_env_path  # noqa: E402
import agent_runtime  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

import lark_oapi as lark  # noqa: E402
import bridge_scope_audit  # noqa: E402
import registration_monitor  # noqa: E402
import registration_transport  # noqa: E402

ENV_PATH = resolve_env_path()   # 跨机解析·不写死盘符


def on_qr(info):
    print(f"\n=== 请在飞书里打开此链接授权（{info.get('expire_in')}s 内有效）===\n{info.get('url')}\n", flush=True)


def on_status(info):
    print(f"[register] status: {info.get('status')}", flush=True)


def _set_key(text, key, value):
    """有该键就替换，没有就追加（多 bot 的新键首次写要追加）。"""
    if re.search(rf"(?m)^{re.escape(key)}=", text):
        return re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", text)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def write_env(app_id, secret, id_key, sec_key, env_path=None):
    """原子更新本机 .env。

    已存在的 .env 可按旧规则向上查找；首次创建必须由
    XHS_ENV_FILE 或 VIBECODING_ROOT 明确指定，禁止在新同事电脑上默写历史 E: 盘。
    """
    target = Path(env_path) if env_path is not None else ENV_PATH
    if not target.exists() and not (
        os.environ.get("XHS_ENV_FILE") or os.environ.get("VIBECODING_ROOT") or env_path is not None
    ):
        raise RuntimeError(
            "首次注册前请先设置 VIBECODING_ROOT（例如 C:\\410_VibeCoding），"
            "或用 XHS_ENV_FILE 明确指定 .env。"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    text = target.read_text(encoding="utf-8", errors="ignore") if target.exists() else ""
    text = _set_key(text, id_key, app_id)
    text = _set_key(text, sec_key, secret)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _credential_failure_summary(result):
    """只输出允许的状态字段和字段名，不回显 SDK 返回体。"""
    if not isinstance(result, dict):
        return f"response_type={type(result).__name__}"
    fields = ",".join(sorted(set(result) & {"client_id", "client_secret", "status", "code"})) or "(empty)"
    status = result.get("status")
    code = result.get("code")
    safe = [f"fields=[{fields}]"]
    if status in ("failed", "pending", "success"):
        safe.append(f"status={status}")
    if isinstance(code, int):
        safe.append(f"code={code}")
    return " ".join(safe)


def _sync_guidance(trusted_same_owner_devices=False):
    if trusted_same_owner_devices:
        return (
            "\n🔄 同一所有者的受信设备：如确需共用这只 bot，再运行 envsync。\n"
            "   它会处理凭据；不要用 Git、聊天或邮件传 .env。"
        )
    return (
        "\n🔒 凭据默认只留在这台电脑。\n"
        "   不同同事、不同所有者之间不复制整份 .env；每人在自己的账号下注册和登录。\n"
        "   只有同一所有者的受信设备才可用 --trusted-same-owner-devices 显示 envsync 指引。"
    )


def _bot_identity(app_id, app_secret):
    """现查 bot/v3/info → (open_id, 飞书显示名)。强制绕代理直连(飞书国内端点)。拿不到回 (None, None)。"""
    import json
    import urllib.request
    base = "https://open.feishu.cn/open-apis"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 强制不走代理
    try:
        r = opener.open(urllib.request.Request(
            f"{base}/auth/v3/tenant_access_token/internal",
            data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
            method="POST", headers={"Content-Type": "application/json"}), timeout=8)
        tok = json.loads(r.read()).get("tenant_access_token")
        r2 = opener.open(urllib.request.Request(
            f"{base}/bot/v3/info", headers={"Authorization": f"Bearer {tok}"}), timeout=8)
        b = json.loads(r2.read()).get("bot", {}) or {}
        return b.get("open_id"), b.get("app_name")
    except Exception:  # noqa: BLE001 — 现查失败不挡注册·open_id 留空待补
        return None, None


def _resolve_machine(slug, name, machines):
    """这条 bot 属于哪台机 —— 数据驱动，**不硬编码机器名**。

    2026-08-17 接第三台 tuf19 时改：老代码是 `"tb24" if 名字像tb24 else "tb25"`，
    等于把「世界上只有两台机」写死进逻辑；第三台注册出来会被默默登记成 tb25，
    而 machine 字段是 repo-sync 跨机路由的依据（registry.py peers --exclude-machine），
    登错 = /push 完通知错人。改成三级判定，加第 N 台机只需往 machines 加一条、不动代码：

      ① bot 名 / slug 前缀命中 machines 的某个 key（惯例 `<machine>-<用途>`，如 tb25-link16 / tuf19-cad）
      ② 命中不了 → 按本机 hostname 反查 machines[*].hostname
      ③ 再不行 → 返回空串 + 让调用方警告，交人补；**绝不再瞎猜一台**
    """
    keys = sorted((machines or {}), key=len, reverse=True)   # 长 key 先比，防 "tb2" 误吃 "tb25"
    lname = (name or "").lower()
    for k in keys:
        if lname.startswith(k.lower() + "-") or slug.startswith(k.upper().replace("-", "_") + "_"):
            return k
    host = socket.gethostname().lower()
    for k, v in (machines or {}).items():
        if str((v or {}).get("hostname") or "").lower() == host:
            return k
    return ""


def append_registry_stub(app_id, app_secret, bot_arg, cli_name, runtime="claude"):
    """建完【自动】往 agent-registry.json 补一条 stub —— 把「登记协议」从『靠人记得回写』变成『脚本自动做』。
    open_id/显示名现查·verified 按是否查到·幂等(已有同名跳过)。repo/machine 让运行的 agent 核对补全(脚本不知道它管哪个仓)。"""
    import json
    # 路径统一走 bridge_env.registry_path()（local→committed→example）——**别在这里自己拼**：
    # 写进 committed、却从 local 读（或反过来）= 注册完查不到。见 PLAN-926 §S1.1。
    from bridge_env import writable_registry_path
    # 自动登记会【写】名册。陌生人首次注册时本机还没有名册，registry_path() 会落到
    # 随仓发布的脱敏样例上 —— 那样会把他的真 open_id 写进仓库样例文件。写入闸负责
    # 改用 profile home 的推荐位置，必要时落一份空骨架。
    reg = writable_registry_path()
    if not reg.exists():
        print("\n⚠️ 没找到 agent 名册（agent-registry.local.json / .json / .example.json 都不在）"
              " → 跳过自动登记（请手动加一条）", flush=True)
        return None
    oid, disp = _bot_identity(app_id, app_secret)
    name = disp or cli_name
    slug = (bot_arg or "").upper().replace("-", "_")
    send_key = slug.lower().replace("_", "-") if slug else (name or "")
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"\n⚠️ agent-registry.json 解析失败({e}) → 跳过自动登记（手动加）", flush=True)
        return None
    machines = data.get("machines", {})
    machine = _resolve_machine(slug, name, machines)
    if not machine:
        print(f"\n⚠️ 判不出 '{name}' 在哪台机：bot 名前缀没命中 machines 的任何 key，"
              f"本机 hostname '{socket.gethostname()}' 也没在名册登记过。"
              f"\n   → machine 先留空，**请手工补**（已知机器：{', '.join(sorted(machines)) or '(空)'}）。"
              f"\n   正解通常是把 bot 起成 `<machine>-<用途>`（如 tuf19-cad），或给本机那条补 hostname。",
              flush=True)
    if any(a.get("name") == name for a in data.get("agents", [])):
        print(f"\n✅ agent-registry.json 已有 '{name}' → 跳过（幂等·没重复加）", flush=True)
        return next(a for a in data.get("agents", []) if a.get("name") == name)
    stub = {"name": name, "machine": machine, "send_key": send_key, "open_id": oid or "",
            "at_name": f"@{name}", "repo": "", "shared": False, "runtime": runtime,
            "role": "", "verified": bool(oid)}
    text = reg.read_text(encoding="utf-8")
    idx = text.rfind("\n  ]")                 # agents 数组闭合行 → 在它前插一条(保原格式·不整文件 reformat)
    if idx < 0:
        print("\n⚠️ agent-registry.json 结构异常(找不到 agents 闭合) → 跳过（手动加）", flush=True)
        return None
    text = text[:idx].rstrip() + ",\n    " + json.dumps(stub, ensure_ascii=False) + text[idx:]
    reg.write_text(text, encoding="utf-8")
    print(f"\n✅ 已【自动】登记进 agent-registry.json：{name}"
          f"（machine={machine} · send_key={send_key} · open_id={oid or '❌现查失败·待补'} · verified={bool(oid)}）", flush=True)
    print("   ⚠️ 运行本脚本的 agent 请核对/补两字段：**repo**(它分管哪个仓·脚本不知道) + 必要时 **machine**；是共享仓则改 shared:true。", flush=True)
    return stub


def _run_device_grant(name, app_id=None, job_id=None):
    """创建新应用，或用 cli_... App ID 续接同一次 Device Grant。"""
    def qr(info):
        on_qr(info)
        if job_id:
            registration_monitor.record_stage(job_id, "oauth_waiting", app_id=app_id)
            for attempt in range(3):
                receipt = registration_monitor.notify_oauth_link(
                    job_id, info.get("url"), info.get("expire_in")
                )
                if receipt.get("ok"):
                    break
                if attempt == 2:
                    raise registration_transport.RegistrationTransportError("oauth_link_delivery_failed")
                time.sleep(2 ** (attempt + 1))

    def report(**values):
        if job_id:
            state = registration_monitor.record_progress(job_id, **values)
            if state.get("status") in registration_monitor.TERMINAL_STATUSES:
                raise registration_transport.RegistrationTransportError("registration_job_ended")

    def status(info):
        value = info.get("status")
        if value in {"polling", "slow_down", "domain_switched"}:
            report(status=value)
        on_status({"status": value if value in {"polling", "slow_down", "domain_switched"} else "unknown"})

    with registration_transport.bounded_http(report):
        return lark.register_app(
            on_qr_code=qr,
            on_status_change=status,
            app_preset={"name": name},
            addons=None,
            # create_only has precedence over app_id in the official launcher.
            create_only=app_id is None,
            app_id=app_id,
        )


def _recovery_app_id(explicit, id_key, bot):
    local = bridge_scope_audit._env_val(id_key)
    previous = registration_monitor.get_job(bot=bot) or {}
    recorded = previous.get("app_id")
    if local and recorded and local != recorded:
        raise ValueError("本机凭据与注册任务的 App ID 不一致；先核对原应用")
    known = local or recorded
    if explicit and known and explicit != known:
        raise ValueError("--app-id 与该 bot 已有 App ID 不一致；不能覆盖原应用")
    chosen = explicit or known
    if chosen and not re.fullmatch(r"cli_[A-Za-z0-9_]+", chosen):
        raise ValueError("App ID 格式无效，须使用 cli_...，不能使用验证码或 secret")
    return chosen


def _validate_group_choice(capabilities, group):
    if "group-a2a" in capabilities and not (group or "").strip():
        raise ValueError(
            "group-a2a 注册必须显式给 --group：新 bot 入群前没有 tenant_key，不能从同机 bot 猜"
        )


def _tenant_kind_from_group(group, registry_file=None):
    """Resolve enterprise/personal from the registry SSOT, never from bot names."""
    if not (group or "").strip():
        return None
    if registry_file is None:
        from bridge_env import registry_path
        registry_file = registry_path()
    try:
        data = json.loads(Path(registry_file).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    needle = group.strip().casefold()
    matches = {
        tenant.get("kind")
        for tenant in data.get("tenants", [])
        if tenant.get("kind")
        and (
            needle == str(tenant.get("group_chat_id") or "").casefold()
            or needle == str(tenant.get("group_name") or "").casefold()
            or needle in str(tenant.get("group_name") or "").casefold()
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _registration_capabilities(requested=None, tenant_kind=None):
    """Apply the enterprise document-consumption baseline without widening personal bots."""
    capabilities = list(bridge_scope_audit.normalize_capabilities(requested))
    if tenant_kind == "enterprise" and "docs-consume" not in capabilities:
        capabilities.append("docs-consume")
    return tuple(capabilities)


def main():
    ap = argparse.ArgumentParser(description="一键创建飞书智能体应用并写凭据进 .env")
    ap.add_argument("--name", default="tb24-xhs-autopilot", help="应用显示名（默认 tb24-xhs-autopilot）")
    ap.add_argument("--bot", default=None,
                    help="bot 标识（如 ws2）→ 写 FEISHU_BRIDGE_<BOT>_APP_ID/SECRET；不给 = 默认键")
    ap.add_argument("--runtime", choices=("claude", "codex"), default=None,
                    help="目标 runtime；不给时由 --profile 推导，无 profile 则兼容默认 claude")
    ap.add_argument("--profile", default=None,
                    help="Link16 agent profile（如 cck/cxp）；不给则取同 runtime 的本机默认")
    ap.add_argument("--cwd", default=None,
                    help="新 bot 的机器本地工作目录；只写入 gitignored local roster")
    ap.add_argument("--app-id", default=None,
                    help="续接已由本次 Device Grant 创建的应用，避免回传中断后重复创建")
    ap.add_argument("--capability", action="append", choices=tuple(bridge_scope_audit.CAPABILITY_SPECS),
                    help="注册能力包，可重复；不给=core + group-a2a，不默认申请 broad Drive")
    ap.add_argument("--notify-bot", default=None,
                    help="人工步骤完成后唤醒哪只 Link16 bot；不给取 FEISHU_BRIDGE_SESSION")
    ap.add_argument("--group", default=None,
                    help="group-a2a 的人工入群验收名片段；新 bot 必须按 Device Grant 选中的组织显式给出")
    ap.add_argument("--tenant-kind", choices=("enterprise", "personal"), default=None,
                    help="飞书租户类型；不给时按 --group 与 agent-registry.json 的 tenants 映射推导")
    ap.add_argument("--no-monitor", action="store_true",
                    help="只用于测试/故障隔离：不启动独立注册监督器")
    ap.add_argument("--background", action="store_true",
                    help="把 Device Grant 轮询放进独立后台进程；授权链接经 Link16 回调，不依赖本轮 tool timeout")
    ap.add_argument("--job-id", help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true",
                    help="只显示 profile/cwd/capability/增量 scopes，不创建应用、不写文件")
    ap.add_argument("--trusted-same-owner-devices", action="store_true",
                    help="仅当目标是同一所有者的受信设备时，显示 envsync 后续指引")
    args = ap.parse_args()

    requested = (args.profile or "").strip().lower()
    if requested:
        selected_profile = agent_runtime.profile_spec(requested)
        runtime = args.runtime or selected_profile.runtime
        if selected_profile.runtime != runtime:
            ap.error(
                f"--profile {selected_profile.name} 是 {selected_profile.runtime}，"
                f"不能配 --runtime {runtime}"
            )
    else:
        runtime = args.runtime or "claude"
        inherited = (os.environ.get(agent_runtime.PROFILE_ENV) or "").strip().lower()
        selected_profile = None
        if inherited:
            inherited_profile = agent_runtime.profile_spec(inherited)
            if inherited_profile.runtime == runtime:
                selected_profile = inherited_profile
        if selected_profile is None:
            selected_profile = agent_runtime.profile_spec(
                agent_runtime.machine_default_profile(runtime)
            )
    doctor = agent_runtime.profile_doctor(selected_profile.name)
    if not doctor["ok"]:
        ap.error(
            f"profile {selected_profile.name} 本机不可用："
            + "；".join(doctor["errors"])
        )

    selected_cwd = None
    if args.cwd:
        selected_cwd_path = Path(args.cwd).expanduser().resolve()
        if not selected_cwd_path.is_dir():
            ap.error(f"--cwd 不是已存在目录：{selected_cwd_path}")
        selected_cwd = str(selected_cwd_path).replace("\\", "/")

    if args.bot:
        b = args.bot.upper().replace("-", "_")   # env key 用下划线(连字符非法/巡检正则[A-Z0-9_]扫不到)·与名册约定一致
        id_key, sec_key = f"FEISHU_BRIDGE_{b}_APP_ID", f"FEISHU_BRIDGE_{b}_APP_SECRET"
    else:
        id_key, sec_key = "FEISHU_BRIDGE_APP_ID", "FEISHU_BRIDGE_APP_SECRET"

    inferred_tenant_kind = _tenant_kind_from_group(args.group)
    if args.tenant_kind and inferred_tenant_kind and args.tenant_kind != inferred_tenant_kind:
        ap.error(
            f"--tenant-kind {args.tenant_kind} 与 --group 对应的 "
            f"{inferred_tenant_kind} 租户冲突"
        )
    tenant_kind = args.tenant_kind or inferred_tenant_kind
    capabilities = _registration_capabilities(args.capability, tenant_kind)
    try:
        _validate_group_choice(capabilities, args.group)
    except ValueError as exc:
        ap.error(str(exc))
    # SPEC-220: a new bot leaves registration with the whole self-serve baseline,
    # not just the scopes its declared capabilities happen to need. Discovering a
    # missing scope months later — mid-task, as a bare "no permission" — was the
    # recurring failure this closes. Approval-gated scopes are never requested;
    # the baseline is built entirely from self-serve ones.
    permission_scopes = tuple(dict.fromkeys(
        list(bridge_scope_audit.requested_scopes(capabilities, for_fix=True))
        + [s for s in bridge_scope_audit.BASELINE_SCOPES if bridge_scope_audit.self_serve(s)]
    ))
    try:
        sdk_version = registration_transport.preflight()
        args.app_id = _recovery_app_id(args.app_id, id_key, args.bot or args.name)
    except (ValueError, registration_transport.RegistrationTransportError) as exc:
        ap.error(str(exc))
    if args.dry_run:
        print("=== register dry-run（零写入）===")
        print(f"bot={args.bot or args.name}")
        print(f"profile={selected_profile.name} runtime={runtime}")
        print(f"cwd={selected_cwd or '(repo default)'}")
        print(f"tenant_kind={tenant_kind or 'unknown'}")
        print(f"capabilities={','.join(capabilities)}")
        print("registration_links=2")
        print(f"sdk_version={sdk_version}")
        print(f"first_link={'existing-app' if args.app_id else 'create-only'};addons=(none)")
        print(f"second_link_scopes={','.join(permission_scopes) or '(none)'}")
        return
    monitor = registration_monitor.get_job(args.job_id) if args.job_id else None
    monitor_bot = args.bot or args.name
    if not monitor and not args.no_monitor:
        monitor = registration_monitor.arm_job(
            monitor_bot,
            capabilities,
            notify_bot=args.notify_bot,
            group=args.group if "group-a2a" in capabilities else None,
            app_id=args.app_id,
            id_env=id_key,
            secret_env=sec_key,
            launch=True,
            context={"name": args.name, "profile": selected_profile.name,
                     "cwd": selected_cwd, "tenant_kind": tenant_kind},
        )
        print(
            f"\n🛰️ 独立注册监督已启动：job={monitor['job_id']} · "
            f"回调目标={monitor.get('notify_bot') or '未绑定（仅持久记录）'}",
            flush=True,
        )

    if args.background:
        if not monitor:
            ap.error("--background 需要注册监督器；不要同时给 --no-monitor")
        child_args = [arg for arg in sys.argv[1:] if arg != "--background"]
        child_args += ["--job-id", monitor["job_id"]]
        pid, started = registration_monitor.start_registration_worker(
            monitor["job_id"], [sys.executable, str(Path(__file__).resolve()), *child_args]
        )
        print(
            f"✅ Device Grant {'已独立后台运行' if started else '复用已有注册任务'}：job={monitor['job_id']} · pid={pid}\n"
            "   授权链接会由 registration-monitor 自动注回发起 session；本命令现在即可退出。",
            flush=True,
        )
        return

    if monitor and not registration_monitor.claim_registration_worker(monitor["job_id"]):
        print(f"复用已有注册任务：job={monitor['job_id']}", flush=True)
        return

    try:
        if monitor:
            registration_monitor.record_progress(monitor["job_id"], sdk_version=sdk_version)
        result = _run_device_grant(
            args.name,
            args.app_id,
            job_id=monitor.get("job_id") if monitor else None,
        )
    except Exception as exc:
        error = registration_transport.safe_error(exc)
        if monitor:
            registration_monitor.record_stage(monitor["job_id"], "failed", error=error)
        raise SystemExit(f"注册未完成：{error}；读取 registration job 后恢复原应用，无需提供 secret") from None
    app_id = result.get("client_id") if isinstance(result, dict) else None
    secret = result.get("client_secret") if isinstance(result, dict) else None
    if not app_id or not secret:
        if monitor:
            registration_monitor.record_stage(
                monitor["job_id"], "failed", error=_credential_failure_summary(result)
            )
        print(f"❌ 没拿到凭据：{_credential_failure_summary(result)}", flush=True)
        sys.exit(1)
    if args.app_id and args.app_id != app_id:
        if monitor:
            registration_monitor.record_stage(monitor["job_id"], "failed", error="returned_app_id_mismatch")
        raise SystemExit("SDK 返回了不同 App ID，已停止写入；请恢复原应用")
    if monitor:
        registration_monitor.record_sdk_result(monitor["job_id"], app_id)
    try:
        _persist_registration(args, selected_profile, selected_cwd, runtime, id_key, sec_key,
                              app_id, secret, monitor)
    except Exception as exc:
        error = registration_transport.safe_error(exc)
        if monitor:
            registration_monitor.record_stage(monitor["job_id"], "failed", error=f"local_registration:{error}")
        raise SystemExit(f"官方已回传，本机登记未完成：{error}；请读取任务状态恢复") from None
    return


def _persist_registration(args, selected_profile, selected_cwd, runtime, id_key, sec_key,
                          app_id, secret, monitor):
    capabilities = _registration_capabilities(args.capability, args.tenant_kind or _tenant_kind_from_group(args.group))
    permission_scopes = bridge_scope_audit.requested_scopes(capabilities, for_fix=True)
    write_env(app_id, secret, id_key, sec_key)
    action = "续接成功" if args.app_id else "创建成功"
    print(f"\n✅ 应用「{args.name}」{action} · App ID = {app_id} · 已写入 .env 的 {id_key} / {sec_key}", flush=True)

    # 自动登记进 agent-registry.json（登记协议自动化·不靠人记得回写）
    stub = append_registry_stub(app_id, secret, args.bot, args.name, runtime)
    runtime_bot_name = (stub or {}).get("send_key") or args.bot or args.name
    at_name = (stub or {}).get("at_name") or f"@{args.name}"
    row = agent_runtime.upsert_runtime_bot(
        runtime_bot_name,
        id_key,
        sec_key,
        at_name,
        selected_profile.name,
        cwd=selected_cwd,
    )
    print(
        f"\n✅ 已自动登记运行时名册：{row['name']} · profile={row['profile']} "
        f"· {agent_runtime.profile_spec(row['profile']).runtime}",
        flush=True,
    )

    if monitor:
        monitor = registration_monitor.record_stage(monitor["job_id"], "registered", app_id=app_id)
        monitor = registration_monitor.request_permission_review(monitor["job_id"])

    # 超长的 q= 串会被飞书整页判「参数不合法」（2026-08-27 tb26-baseball-2 实证：54 条 = 1446 字符挂，
    # 18 条 = 523 字符通）→ 一律走拆链，每条都点得开。SSOT = feishu_docs.AUTH_URL_MAX_CHARS。
    permission_links = bridge_scope_audit.fix_auth_urls(app_id, permission_scopes)
    permission_link = (
        "\n".join(f"   [{i}/{len(permission_links)}] {u}" for i, u in enumerate(permission_links, 1))
        if len(permission_links) > 1
        else (permission_links[0] if permission_links else "   （本次无需增量 scope）")
    )

    labels = [bridge_scope_audit.CAPABILITY_SPECS[name]["label"] for name in capabilities]
    print("\n🔐 第二步：请人工审阅本次能力与权限，并按飞书页面要求创建版本/发布：\n"
          f"   capability: {', '.join(capabilities)}\n"
          f"   能力: {'；'.join(labels)}\n"
          f"   tenant scopes: {', '.join(permission_scopes) or '无（只用官方 preset）'}\n"
          f"{permission_link}\n"
          "   第一条链接只创建应用；本链接不自动发布。如飞书把其中某项标为需审核，以开发者后台"
          "的实时标识为准；监督器会持续复查真实授权状态。",
          flush=True)

    # 🔒 登记协议：建完必回写。§4 见 docs/SOP-120。agent-registry stub 上面已【自动】补·其余照单核对。
    group_step = (f"   ③ 人工把 bot 拉进共享群「{args.group}」（API 加不了）\n"
                  if "group-a2a" in capabilities
                  else "   ③ 本次未选 group-a2a，不需要入共享群\n")
    print("\n📋 建完【必做登记】（④ 已自动 · 完整见 docs/SOP-120 §4）：\n"
          f"   ① ✅ bridge-bots.local.json 已自动登记（profile={selected_profile.name}）—— 运行时 roster·桥靠它 spawn\n"
          "      换运行档案只改 profile（或飞书 `/account <profile>`）；不要再写 agent/home/account 重复字段。\n"
          f"   ② 打开上面的第二条链接，人工核对本次 capability：{', '.join(capabilities)}\n"
          f"{group_step}"
          "   ④ ✅ agent-registry.json 已【自动】补 stub（谁是谁·跨机目录）→ 你只需核对/补 repo + machine（脚本不知道它管哪个仓）\n"
          "   ⑤ 跑  python feishu/bridge_scope_audit.py --all-env  → 刷新 SOP-120 §2.2 能力矩阵\n"
          "   ⑥ 不自动重启整座桥；由监督器验权/认主/入群并回调发起 session\n"
          "   —— 两个名册各司其职：bridge-bots(运行时·跑哪些) + agent-registry(目录·谁是谁·本步已自动)。",
          flush=True)

    # 🚨 认主：全流程最后一道、也是最容易漏的一步（2026-08-02 taoci-7 刷群事故后加·见 ARCH-110 §2.5.3）
    print("\n📋 最后一步：请主人私聊新 bot 一句话，完成认主。\n"
          "   请主人在飞书【私聊】这个新 bot 发任意一句话（『在吗』就行）。\n"
          "   为什么：bot 的主人 = 【第一个私聊它的人】自动认下的。**群里 @ 它不算**——群消息按设计\n"
          "   绝不认主（否则同群的 peer bot 会把主人身份夺走）。而 open_id 是 per-app 的，注册时\n"
          "   根本无从预先写死，只能等主人真发一条 DM 才知道。\n"
          "   未认主时回复没有投递目标，会明确记失败；不会退到其他群或其他机器人。\n"
          "   ✅ 验收：私聊后桥日志出现『自动认主人 owner=ou_…』，且 feishu/_state/bridge-owner-<bot>.json 生成。\n"
          "   （Claude：请把这句话【明确转达给主人】，别默认他知道。）",
          flush=True)

    print(_sync_guidance(args.trusted_same_owner_devices), flush=True)


if __name__ == "__main__":
    main()
