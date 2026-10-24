#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络路由探测，以及消息投递前的有限本地代理恢复。

probe/run 命令只读配置、只给子进程注入环境变量。ensure_agent_proxy 仅在
apply=True 时启动已验证的客户端，或经本地核心接口修复端口并同步匹配的系统代理。
恢复有持久次数限制；不杀进程、不改订阅或节点、不改客户端持久设置。

用法：
    python feishu/network_route.py probe --url https://example.com/file --prefer proxy
    python feishu/network_route.py run --url https://example.com/file --prefer proxy -- pip install ...
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bridge_env import force_utf8_std, resolve_env_path  # noqa: E402

force_utf8_std()

PROXY_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)
NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")
COMMON_MIXED_PORTS = (7897, 7890, 10808, 10809)


@dataclass(frozen=True)
class ProbeResult:
    route: str
    ok: bool
    elapsed_ms: int | None = None
    status: int | None = None
    bytes_read: int = 0
    error: str = ""


@dataclass(frozen=True)
class RouteDecision:
    selected: str
    usable: bool
    reason: str
    prefer: str
    results: tuple[ProbeResult, ...]


@dataclass(frozen=True)
class ProxyCandidate:
    url: str
    source: str


def _dotenv_value(key: str) -> str | None:
    """读一个非敏感配置值；不把整个 .env 加进进程环境。"""
    path = resolve_env_path(HERE)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value or None
    return None


def proxy_url() -> str | None:
    value = (os.environ.get("PROXY_URL") or _dotenv_value("PROXY_URL") or "").strip()
    if value and "://" not in value:
        value = "http://" + value
    return value or None


def _normalize_proxy(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        if not parsed.hostname or not parsed.port:
            return None
    except ValueError:
        return None
    return raw


def _is_loopback_proxy(value: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _proxy_display(value: str) -> str:
    """不显示 URL 里可能携带的用户名/密码。"""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or "?"
        return f"{parsed.scheme or 'http'}://{host}:{parsed.port}"
    except ValueError:
        return "<invalid proxy>"


def discover_proxy_candidates(configured: str | None = None, system=None,
                              common_ports=COMMON_MIXED_PORTS) -> tuple[ProxyCandidate, ...]:
    """收集可能的本地 mixed port；只读、不改 v2rayN 或系统代理。"""
    rows: list[ProxyCandidate] = []
    seen: set[str] = set()

    def add(value, source, *, loopback_only=False):
        normalized = _normalize_proxy(value)
        if not normalized or (loopback_only and not _is_loopback_proxy(normalized)):
            return
        key = _proxy_display(normalized).lower()
        if key in seen:
            return
        seen.add(key)
        rows.append(ProxyCandidate(normalized, source))

    add(configured if configured is not None else proxy_url(), "PROXY_URL")
    proxies = urllib.request.getproxies() if system is None else system
    for scheme in ("https", "http", "all"):
        add((proxies or {}).get(scheme), f"Windows/{scheme}", loopback_only=True)
    if system is None and os.name == "nt":
        # getproxies() lets stale shell variables shadow the live Windows proxy.
        for scheme, value in urllib.request.getproxies_registry().items():
            add(value, f"WinInet/{scheme}", loopback_only=True)
    for port in common_ports:
        add(f"http://127.0.0.1:{int(port)}", "common mixed port")
    return tuple(rows)


class AgentProxyUnavailable(RuntimeError):
    """Pre-submission local dependency failure; the durable inbox retains the message."""
    def __init__(self, message):
        self.user_message = message
        super().__init__(message)


def _proxy_processes():
    import bridge_process
    script = """
$ErrorActionPreference = 'Stop'
try {
  $session = (Get-Process -Id $PID).SessionId
  $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
  $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.SessionId -eq $session -and $_.Name -match '^(CATVPN(Core)?|clash(-meta|-verge|-win64)?|verge-mihomo(-alpha)?|mihomo(-windows-amd64)?|v2rayN|sing-box)\\.exe$'
  } | ForEach-Object {
    $row = $_
    [pscustomobject]@{ProcessId=$row.ProcessId; ParentProcessId=$row.ParentProcessId;
      Name=$row.Name; ExecutablePath=$row.ExecutablePath; CommandLine=$row.CommandLine;
      CreationDate=$row.CreationDate;
      ListenPorts=@($listeners | Where-Object {$_.OwningProcess -eq $row.ProcessId} | Select-Object -ExpandProperty LocalPort)}
  })
  @{ok=$true; session=$session; processes=$rows} | ConvertTo-Json -Depth 4 -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
    value = bridge_process._envelope(bridge_process._powershell(script, 10))
    if value.get("session") == 0 or not isinstance(value.get("processes"), list):
        raise AgentProxyUnavailable("无法确认交互桌面上的代理进程；未启动或重启任何程序。")
    return value["processes"]


def _windows_argv(command):
    import ctypes
    from ctypes import wintypes
    argc = ctypes.c_int()
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    pointer = split(command, ctypes.byref(argc))
    if not pointer:
        raise ValueError("cannot parse proxy command line")
    try:
        return [pointer[i] for i in range(argc.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(pointer)


def _core_config(process):
    """Read only the config named by the running core, never a guessed subscription."""
    import yaml
    args = _windows_argv(process.get("CommandLine") or "")
    config = None
    for flag in ("-f", "--config"):
        if flag in args and args.index(flag) + 1 < len(args):
            config = Path(args[args.index(flag) + 1])
            break
    if config is None or not config.is_absolute() or not config.is_file():
        return None
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    address = _normalize_proxy(data.get("external-controller"))
    if (not address or not _is_loopback_proxy(address)
            or urllib.parse.urlsplit(address).port not in process.get("ListenPorts", [])):
        return None
    return {"url": address, "secret": str(data.get("secret") or "")}


def _core_request(core, method="GET", payload=None):
    # The credential stays in memory and is never included in a receipt or error.
    request = urllib.request.Request(
        core["url"] + "/configs", method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + core["secret"], "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
        raw = response.read(65536)
        return json.loads(raw) if raw else {}


def _listening(proxy):
    parsed = urllib.parse.urlsplit(proxy)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=1):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as exc:
        # Unknown networking state is not proof that a listener is absent.
        raise AgentProxyUnavailable("本机代理端口状态无法确认，未执行端口修改。") from exc


def _remember_client(rows, port):
    owners = [r for r in rows if port in r.get("ListenPorts", [])]
    if len(owners) != 1:
        return None
    owner = owners[0]
    clients = [r for r in rows if str(r.get("Name", "")).lower() in
               {"catvpn.exe", "clash-verge.exe", "v2rayn.exe"}
               and (r["ProcessId"] == owner.get("ParentProcessId") or
                    r["ProcessId"] == owner["ProcessId"])
               and r.get("ExecutablePath") and Path(r["ExecutablePath"]).is_file()]
    return clients[0]["ExecutablePath"] if len(clients) == 1 else None


def _sync_system_proxy(old_port, new_port):
    """CAS only the simple loopback proxy belonging to the repaired core."""
    import winreg
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            current = winreg.QueryValueEx(key, "ProxyServer")[0]
        except FileNotFoundError:
            return
        if current in (f"127.0.0.1:{old_port}", f"localhost:{old_port}"):
            updated = current.rsplit(":", 1)[0] + f":{new_port}"
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, updated)
            import ctypes
            for option in (39, 37):  # SETTINGS_CHANGED, REFRESH
                ctypes.windll.wininet.InternetSetOptionW(None, option, None, 0)


def ensure_agent_proxy(*, apply=False, expected=None, probe_url="https://www.gstatic.com/generate_204"):
    """Bounded repair of the configured local terminal proxy, without model access.

    Healthy listeners are left alone. An absent client may be launched once per
    outage; a live compatible core may have its port aligned once per process.
    No process is killed, no subscription/node is changed, and no app settings
    file is edited behind its GUI. Failed verification rolls the runtime port back.
    """
    expected = _normalize_proxy(expected or proxy_url())
    if os.name != "nt" or not expected or not _is_loopback_proxy(expected):
        return {"status": "unmanaged"}
    parsed = urllib.parse.urlsplit(expected)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        return {"status": "unmanaged"}
    import bridge_process
    import wmux_session
    from bridge_injection import atomic_write_json
    state_path = bridge_process.LOCK_DIR / "agent-proxy-recovery.json"
    with bridge_process.service_lock("agent-proxy-recovery", control=True, timeout=12):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {}
        if not isinstance(state, dict):
            raise AgentProxyUnavailable("代理恢复记录无法读取，未执行自动操作。")
        if _listening(expected):
            # Remember the actual running client for a later absent-process recovery.
            if apply and (not state.get("client") or state.get("launch_attempts")
                          or time.time() - state.get("observed_at", 0) >= 300):
                client = _remember_client(_proxy_processes(), parsed.port)
                if client:
                    state["client"] = client
                else:
                    # A new/unrecognized owner must not leave the old VPN eligible
                    # for a later automatic launch.
                    state.pop("client", None)
                state["observed_at"] = time.time()
                atomic_write_json(state_path, state)
            if apply and state.get("launch_attempts"):
                state["launch_attempts"] = 0
                atomic_write_json(state_path, state)
            return {"status": "listening", "proxy": _proxy_display(expected)}
        rows = _proxy_processes()
        if not rows:
            client = state.get("client")
            if not client or not Path(client).is_file():
                raise AgentProxyUnavailable("终端代理端口未开启，且没有已验证的代理客户端可启动；原消息保留。")
            if state.get("launch_attempts", 0) >= 1:
                raise AgentProxyUnavailable("已尝试启动代理客户端 1 次，端口仍未恢复；停止自动启动，原消息保留。")
            if not apply:
                return {"status": "would_start", "client": Path(client).name}
            if wmux_session._windows_shutting_down():
                raise AgentProxyUnavailable("Windows 正在关机，保留消息，暂不启动代理客户端。")
            state["launch_attempts"] = 1
            atomic_write_json(state_path, state)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            subprocess.Popen([client], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, startupinfo=startup,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            raise AgentProxyUnavailable("代理客户端未运行，已发起一次启动；等待自动连接后继续原消息。")
        cores = []
        for row in rows:
            try:
                core = _core_config(row)
                if not core:
                    continue
                config = _core_request(core)
                port = config.get("mixed-port")
                if (type(port) is int and 0 < port < 65536 and not config.get("allow-lan")
                        and not config.get("authentication") and port in row.get("ListenPorts", [])):
                    cores.append((row, core, port))
            except (OSError, ValueError, urllib.error.URLError):
                continue
        if len(cores) != 1:
            raise AgentProxyUnavailable("终端代理端口不可用，无法唯一确认可控制的代理核心；未改端口或重启程序。")
        row, core, old_port = cores[0]
        current = f"http://127.0.0.1:{old_port}"
        test = probe_route(probe_url, "proxy", current, timeout=4, sample_bytes=128)
        if not test.ok:
            raise AgentProxyUnavailable("代理程序在运行，但出口验证失败；端口修改无法证明能解决，保留消息等网络恢复。")
        identity = f"{row['ProcessId']}:{row.get('CreationDate')}:{parsed.port}"
        if identity in state.get("port_attempts", []):
            raise AgentProxyUnavailable("此代理进程已尝试过端口修复，停止重复修改；请核对客户端持久端口设置。")
        detail = f"代理运行在 {old_port}，终端配置为 {parsed.port}"
        if not apply:
            return {"status": "would_align", "detail": detail}
        if wmux_session._windows_shutting_down():
            raise AgentProxyUnavailable("Windows 正在关机，暂不修改代理端口。")
        state["port_attempts"] = (state.get("port_attempts", []) + [identity])[-20:]
        client = _remember_client(rows, old_port)
        if client:
            state["client"] = client
        atomic_write_json(state_path, state)
        # A concurrent owner may already have started a listener. Never take its port.
        if _listening(expected):
            return {"status": "listening", "proxy": _proxy_display(expected)}
        try:
            _core_request(core, "PATCH", {"mixed-port": parsed.port})
            after = _core_request(core)
            verified = probe_route(probe_url, "proxy", expected, timeout=4, sample_bytes=128)
            if after.get("mixed-port") != parsed.port or not verified.ok:
                raise AgentProxyUnavailable("端口修改后的出口验证失败")
            _sync_system_proxy(old_port, parsed.port)
        except Exception as exc:
            try:
                # Roll back only if the live core still has our value.
                if _core_request(core).get("mixed-port") == parsed.port:
                    _core_request(core, "PATCH", {"mixed-port": old_port})
            except Exception:
                raise AgentProxyUnavailable("代理端口修复失败，回退结果也未确认；已停止修改，需要检查客户端。") from exc
            raise AgentProxyUnavailable("代理端口修复未通过，已结束本次尝试并回退本次端口修改。") from exc
        return {"status": "recovered", "detail": detail + "；已热更新运行端口并验证出口，客户端持久设置未改。"}


def diagnose_proxy_candidates(url: str, prefer: str, candidates, *, timeout: float = 5.0,
                              sample_bytes: int = 64 * 1024, min_gain: float = 0.15,
                              probe_fn=None):
    """用真实 URL 验证每个候选；端口能连不等于代理能用。"""
    probe_fn = probe_fn or probe_route
    candidates = tuple(candidates)
    routes = [("direct", None)] + [
        (f"proxy-{idx}", row.url) for idx, row in enumerate(candidates, start=1)
    ]
    with ThreadPoolExecutor(max_workers=len(routes)) as pool:
        futures = [
            pool.submit(probe_fn, url, route, proxy, timeout=timeout,
                        sample_bytes=sample_bytes)
            for route, proxy in routes
        ]
        results = [future.result() for future in futures]
    preferred_route = "direct"
    if prefer == "proxy" and candidates:
        preferred_route = "proxy-1"
    decision = choose_route(results, preferred_route, min_gain=min_gain)
    route_map = {f"proxy-{idx}": row for idx, row in enumerate(candidates, start=1)}
    return decision, route_map


def _opener(route: str, proxy: str | None):
    if route == "direct":
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if not proxy:
        raise ValueError(".env / 环境变量里没有 PROXY_URL")
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def probe_route(url: str, route: str, proxy: str | None, *, timeout: float = 8.0,
                sample_bytes: int = 256 * 1024) -> ProbeResult:
    started = time.perf_counter()
    try:
        opener = _opener(route, proxy)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Link16-Network-Route/1.0",
                "Range": f"bytes=0-{max(0, sample_bytes - 1)}",
                "Accept-Encoding": "identity",
            },
        )
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            payload = response.read(sample_bytes)
        elapsed = max(1, round((time.perf_counter() - started) * 1000))
        ok = 200 <= status < 400
        return ProbeResult(route, ok, elapsed, status, len(payload),
                           "" if ok else f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001 — 探测必须把单路失败变成数据
        elapsed = max(1, round((time.perf_counter() - started) * 1000))
        return ProbeResult(route, False, elapsed_ms=elapsed,
                           error=f"{type(exc).__name__}: {exc}")


def choose_route(results, prefer: str, *, min_gain: float = 0.15) -> RouteDecision:
    """成功优先；双通时另一线路至少快 min_gain 才覆盖预设，避免抖动来回切。"""
    rows = tuple(results)
    by_route = {row.route: row for row in rows}
    successful = [row for row in rows if row.ok and row.elapsed_ms is not None]
    if not successful:
        return RouteDecision(prefer, False, "两条线路都不可用；仅报告预设，不执行命令", prefer, rows)
    if len(successful) == 1:
        row = successful[0]
        return RouteDecision(row.route, True, f"只有 {row.route} 可用", prefer, rows)

    fastest = min(successful, key=lambda row: row.elapsed_ms)
    preferred = by_route.get(prefer)
    if preferred and preferred.ok and preferred.elapsed_ms is not None:
        threshold = preferred.elapsed_ms * (1.0 - min_gain)
        if fastest.route != prefer and fastest.elapsed_ms >= threshold:
            return RouteDecision(prefer, True, f"差距小于 {round(min_gain * 100)}%，沿用预设", prefer, rows)
    return RouteDecision(fastest.route, True, f"{fastest.route} 实测更快", prefer, rows)


def detect(url: str, prefer: str, proxy: str | None, *, timeout: float = 8.0,
           sample_bytes: int = 256 * 1024, min_gain: float = 0.15,
           probe_fn=probe_route) -> RouteDecision:
    routes = ["direct"] + (["proxy"] if proxy else [])
    with ThreadPoolExecutor(max_workers=len(routes)) as pool:
        futures = [
            pool.submit(probe_fn, url, route, proxy, timeout=timeout,
                        sample_bytes=sample_bytes)
            for route in routes
        ]
        results = [future.result() for future in futures]
    effective_prefer = prefer if prefer in routes else "direct"
    return choose_route(results, effective_prefer, min_gain=min_gain)


def child_environment(route: str, proxy: str | None, base=None):
    env = dict(os.environ if base is None else base)
    for key in (*PROXY_KEYS, *NO_PROXY_KEYS):
        env.pop(key, None)
    if route == "proxy":
        if not proxy:
            raise ValueError("选择 proxy 时缺 PROXY_URL")
        for key in PROXY_KEYS:
            env[key] = proxy
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1"
    else:
        env["NO_PROXY"] = env["no_proxy"] = "*"
    return env


def _print_decision(decision: RouteDecision, as_json: bool):
    payload = {
        "selected": decision.selected,
        "usable": decision.usable,
        "reason": decision.reason,
        "prefer": decision.prefer,
        "results": [asdict(row) for row in decision.results],
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for row in decision.results:
        if row.ok:
            print(f"  [ OK ] {row.route:<6} {row.elapsed_ms} ms · HTTP {row.status} · {row.bytes_read} B")
        else:
            print(f"  [FAIL] {row.route:<6} {row.elapsed_ms} ms · {row.error}")
    mark = "✅" if decision.usable else "❌"
    print(f"{mark} selected={decision.selected} · {decision.reason}")


def _parser():
    parser = argparse.ArgumentParser(description="按实际 URL 比较直连与 PROXY_URL")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("probe", "run"):
        item = sub.add_parser(name)
        item.add_argument("--url", required=True, help="与真实下载同源的探测 URL")
        item.add_argument("--prefer", choices=("direct", "proxy"), default=None,
                          help="差距不显著或测不出时的预设；默认：有 PROXY_URL 则 proxy")
        item.add_argument("--timeout", type=float, default=8.0, help="每条线路超时秒数")
        item.add_argument("--sample-kib", type=int, default=256, help="最多读取多少 KiB")
        item.add_argument("--min-gain", type=float, default=0.15,
                          help="覆盖预设所需的最小提速比例（默认 0.15）")
        item.add_argument("--json", action="store_true")
        if name == "run":
            item.add_argument("command", nargs=argparse.REMAINDER,
                              help="放在 -- 后面的命令；不用 shell 拼接")
    doctor = sub.add_parser("proxy-doctor", help="自动发现并验证本地 mixed port")
    doctor.add_argument("--url", default="https://github.com/",
                        help="用于功能验证的真实下载站点")
    doctor.add_argument("--prefer", choices=("direct", "proxy"), default="proxy",
                        help="速度差不显著时的默认预设")
    doctor.add_argument("--timeout", type=float, default=5.0)
    doctor.add_argument("--sample-kib", type=int, default=64)
    doctor.add_argument("--min-gain", type=float, default=0.15)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    proxy = proxy_url()
    if args.action == "proxy-doctor":
        candidates = discover_proxy_candidates(proxy)
        decision, route_map = diagnose_proxy_candidates(
            args.url, args.prefer, candidates, timeout=args.timeout,
            sample_bytes=max(1, args.sample_kib) * 1024,
            min_gain=max(0.0, args.min_gain),
        )
        for row in decision.results:
            if row.route == "direct":
                label = "direct"
            else:
                candidate = route_map[row.route]
                label = f"{_proxy_display(candidate.url)} ({candidate.source})"
            if row.ok:
                print(f"  [ OK ] {label} · {row.elapsed_ms} ms · HTTP {row.status}")
            else:
                print(f"  [FAIL] {label} · {row.error}")
        if not decision.usable:
            print("❌ 直连和所有候选代理都不可用；请先打开 v2rayN，再查「本地混合端口」。")
            return 2
        if decision.selected == "direct":
            print(f"✅ 建议本次直连 · {decision.reason}")
        else:
            selected = route_map[decision.selected]
            safe_url = _proxy_display(selected.url)
            print(f"✅ 建议 PROXY_URL={safe_url} · {decision.reason}")
            print(f"   PowerShell 用户级：[Environment]::SetEnvironmentVariable('PROXY_URL','{safe_url}','User')")
        return 0
    configured = (os.environ.get("LINK16_ROUTE_DEFAULT") or "").strip().lower()
    prefer = args.prefer or (configured if configured in {"direct", "proxy"} else None)
    prefer = prefer or ("proxy" if proxy else "direct")
    decision = detect(
        args.url, prefer, proxy, timeout=args.timeout,
        sample_bytes=max(1, args.sample_kib) * 1024,
        min_gain=max(0.0, args.min_gain),
    )
    _print_decision(decision, args.json)
    sys.stdout.flush()  # 管道/计划任务下也要先让人看到选路证据，再出现子进程输出
    if not decision.usable:
        return 2
    if args.action == "probe":
        return 0
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("❌ run 需要在 -- 后给出命令", file=sys.stderr)
        return 2
    completed = subprocess.run(
        command,
        shell=False,
        env=child_environment(decision.selected, proxy),
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
