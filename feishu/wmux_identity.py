"""Link16 在 wmux 里的客户端身份登记（wmux#1111 之后的硬依赖）。

wmux 从「2026-09-30 之后的第一个版本」（v3.64.0）起拒绝不带 `clientName` 的 RPC。
wmux/wmux-rpc.js 每次请求都报 `CLIENT_NAME`；这个名字要登记进本机 `~/.wmux/config.json`
的 `mcp.firstPartyClients`（wmux 官方扩展点，issue #636，v3.40 起支持），wmux 才按第一方
客户端放行它要用的面板/终端方法。wmux 只在启动时读这一项，登记后要重启 wmux 才生效。

  python feishu/wmux_identity.py            # 只读：查看本机是否已登记
  python feishu/wmux_identity.py --apply    # 登记（保留 config.json 其他内容），然后重启 wmux
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

CLIENT_NAME = "link16-agent-infra"   # 与 wmux/wmux-rpc.js 的 CLIENT_NAME 必须一致（测试锁定）
TASK = "wmux-client-identity"


def config_path(home=None) -> Path:
    return Path(home or Path.home()) / ".wmux" / "config.json"


def _registered(data) -> bool:
    mcp = data.get("mcp") if isinstance(data, dict) else None
    names = mcp.get("firstPartyClients") if isinstance(mcp, dict) else None
    return isinstance(names, list) and any(isinstance(n, str) and n.strip() == CLIENT_NAME for n in names)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure(*, apply=False, home=None) -> dict:
    """查看/登记本机 wmux 对 Link16 的第一方识别；返回 windows_bootstrap 收尾任务同形的结果。"""
    path = config_path(home)
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"task": TASK, "status": "blocked", "detail": f"无法安全解析 {path}: {exc}"}
        if not isinstance(data, dict):
            return {"task": TASK, "status": "blocked", "detail": f"{path} 顶层不是 JSON 对象"}
    if _registered(data):
        return {"task": TASK, "status": "ok", "detail": f"{path} → mcp.firstPartyClients 含 {CLIENT_NAME}"}
    mcp = data.get("mcp")
    if mcp is not None and not isinstance(mcp, dict):
        return {"task": TASK, "status": "blocked", "detail": f"{path} 的 mcp 不是对象，不自动改写"}
    names = (mcp or {}).get("firstPartyClients")
    if names is not None and not isinstance(names, list):
        return {"task": TASK, "status": "blocked", "detail": f"{path} 的 mcp.firstPartyClients 不是数组，不自动改写"}
    if apply:
        data.setdefault("mcp", {})["firstPartyClients"] = [*(names or []), CLIENT_NAME]
        _write(path, data)
        return {"task": TASK, "status": "applied",
                "detail": f"{path} → 已加入 {CLIENT_NAME}；重启 wmux 后生效"}
    return {"task": TASK, "status": "missing",
            "detail": f"{path} 未登记 {CLIENT_NAME}；运行 python feishu/wmux_identity.py --apply 后重启 wmux"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="登记 Link16 为本机 wmux 的第一方客户端")
    parser.add_argument("--apply", action="store_true", help="写入 ~/.wmux/config.json（保留其他内容）")
    args = parser.parse_args(argv)
    row = ensure(apply=args.apply)
    print(f"[{row['status']}] {row['detail']}")
    return 0 if row["status"] in {"ok", "applied"} else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
