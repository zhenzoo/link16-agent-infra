#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读本机型号/年份并建议 Link16 机器前缀。

不读序列号、UUID、MAC 或硬盘标识。BIOS 年份只是自动建议的证据，
不是购买年份；用户明确告知的年份永远覆盖它。
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MachineIdentity:
    manufacturer: str
    model: str
    product_name: str
    version: str
    bios_release_date: str
    hostname: str


@dataclass(frozen=True)
class PrefixSuggestion:
    prefix: str
    family: str
    year: str
    year_source: str
    confidence: str
    collision: bool
    evidence: str


def collect_windows_identity() -> MachineIdentity:
    script = (
        "$cs=Get-CimInstance Win32_ComputerSystem;"
        "$csp=Get-CimInstance Win32_ComputerSystemProduct;"
        "$bios=Get-CimInstance Win32_BIOS;"
        "$date=if($bios.ReleaseDate){$bios.ReleaseDate.ToString('yyyy-MM-dd')}else{''};"
        "[pscustomobject]@{Manufacturer=$cs.Manufacturer;Model=$cs.Model;"
        "Name=$csp.Name;Version=$csp.Version;BIOSReleaseDate=$date;"
        "ComputerName=$env:COMPUTERNAME}|ConvertTo-Json -Compress"
    )
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or "Windows 机型查询失败").strip())
    row = json.loads(done.stdout)
    return MachineIdentity(
        str(row.get("Manufacturer") or ""),
        str(row.get("Model") or ""),
        str(row.get("Name") or ""),
        str(row.get("Version") or ""),
        str(row.get("BIOSReleaseDate") or ""),
        str(row.get("ComputerName") or socket.gethostname()),
    )


def _family(identity: MachineIdentity) -> str:
    text = " ".join(asdict(identity).values()).lower()
    if "thinkbook" in text:
        return "tb"
    if "tuf" in text:
        return "tuf"
    if "thinkpad" in text:
        return "tp"
    if "legion" in text or "拯救者" in text:
        return "legion"
    maker = re.sub(r"[^a-z0-9]", "", identity.manufacturer.lower())
    return (maker[:4] or "pc")


def _bios_year(identity: MachineIdentity) -> str:
    match = re.match(r"(20\d{2})", identity.bios_release_date)
    return match.group(1)[-2:] if match else ""


def suggest_prefix(identity: MachineIdentity, *, year: str | None = None,
                   explicit_prefix: str | None = None, existing=None) -> PrefixSuggestion:
    existing_rows = existing or {}

    def collides(prefix):
        if isinstance(existing_rows, dict):
            row = existing_rows.get(prefix)
            if row is None:
                return False
            known_host = str((row or {}).get("hostname") or "").lower()
            return not known_host or known_host != identity.hostname.lower()
        return prefix in set(existing_rows)

    if explicit_prefix:
        prefix = re.sub(r"[^a-z0-9-]", "", explicit_prefix.lower())
        if not prefix:
            raise ValueError("手动前缀必须至少含一个字母或数字")
        family = re.sub(r"\d+$", "", prefix) or prefix
        selected_year = re.search(r"(\d{2})$", prefix)
        return PrefixSuggestion(
            prefix, family, selected_year.group(1) if selected_year else "",
            "user prefix", "high", collides(prefix),
            "用户明确指定的机器前缀",
        )

    family = _family(identity)
    manual_year = re.sub(r"\D", "", year or "")
    if len(manual_year) == 4:
        manual_year = manual_year[-2:]
    if manual_year and len(manual_year) != 2:
        raise ValueError("年份请用 2026 或 26")
    selected_year = manual_year or _bios_year(identity)
    year_source = "user" if manual_year else ("BIOS release year" if selected_year else "unknown")
    confidence = "high" if manual_year else ("medium" if selected_year else "low")
    prefix = family + selected_year if selected_year else family
    evidence = " / ".join(filter(None, [
        identity.manufacturer, identity.version, identity.model,
        f"BIOS {identity.bios_release_date}" if identity.bios_release_date else "",
    ]))
    return PrefixSuggestion(
        prefix, family, selected_year, year_source, confidence, collides(prefix), evidence
    )


def _registry_machines(path: str | None = None):
    try:
        if path:
            registry = Path(path)
        else:
            from bridge_env import registry_path
            registry = registry_path()
        data = json.loads(registry.read_text(encoding="utf-8"))
        return data.get("machines") or {}
    except Exception:
        return {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读检测本机型号并建议 bot 前缀")
    parser.add_argument("--year", help="用户确认的购买/命名年份（2026 或 26）")
    parser.add_argument("--prefix", help="用户明确指定的前缀（最高优先）")
    parser.add_argument("--registry", help="用于碰撞检查的 registry 路径")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    identity = collect_windows_identity()
    suggestion = suggest_prefix(
        identity, year=args.year, explicit_prefix=args.prefix,
        existing=_registry_machines(args.registry),
    )
    payload = {"identity": asdict(identity), "suggestion": asdict(suggestion)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"机型：{identity.manufacturer} {identity.version or identity.model}")
    print(f"证据：{suggestion.evidence or '未读到'}")
    print(f"建议前缀：{suggestion.prefix} · confidence={suggestion.confidence}")
    if suggestion.year_source == "BIOS release year":
        print("提醒：BIOS 年份不等于购买年份；用户明确告知时用 --year 覆盖。")
    if suggestion.collision:
        print("⚠️ 该前缀已在当前 registry 中；若不是同一台机，请用 --prefix 明确取新名。")
        return 1
    print(f"命名示例：{suggestion.prefix}-link16 / {suggestion.prefix}-ccp / {suggestion.prefix}-cxp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
