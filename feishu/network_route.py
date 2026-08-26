#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为一次下载/安装探测并选择 direct 或 PROXY_URL。

它只读配置、只给被执行的子进程注入环境变量：不改 v2rayN、不改 Windows
系统代理，也不维护网络名/地区域名表。判断依据永远是调用方给出的实际 URL。

用法：
    python feishu/network_route.py probe --url https://example.com/file --prefer proxy
    python feishu/network_route.py run --url https://example.com/file --prefer proxy -- pip install ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
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
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    proxy = proxy_url()
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
