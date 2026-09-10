#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan/apply/rollback the two Link16 Windows startup entries.

The installer owns persistent configuration only:

* HKCU Run ``wmux``
* Task Scheduler ``FeishuBridge-Autostart``
* disabling the legacy ``AutopilotWatchdog-Autostart`` task

It never starts/stops production processes.  Apply is bound to the exact plan
digest the user reviewed; rollback is compare-and-swap against the receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATE_DIR = HERE / "_state" / "service-installer"
RECEIPT_DIR = STATE_DIR / "receipts"
BRIDGE_TASK = "FeishuBridge-Autostart"
LEGACY_TASK = "AutopilotWatchdog-Autostart"
RUN_VALUE = "wmux"
SCHEMA = 1
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _public_state(state: dict) -> dict:
    row = {key: value for key, value in state.items() if key not in {"raw_xml"}}
    if state.get("raw_xml"):
        row["raw_xml_sha256"] = hashlib.sha256(state["raw_xml"].encode("utf-8")).hexdigest()
    return row


def _normalized_state(state: dict) -> dict:
    row = {key: value for key, value in state.items() if key != "raw_xml"}
    if "arguments" in row:  # Scheduled task semantic defaults/identity rendering.
        principal = row.pop("principal_user", "")
        row["principal_account"] = row.get("principal_account") or principal
        row["run_level"] = row.get("run_level") or "LeastPrivilege"
    return row


def _state_equal(left: dict, right: dict) -> bool:
    return _normalized_state(left) == _normalized_state(right)


def _quoted_executable(path: Path) -> str:
    return f'"{path}"'


def desired_state(repo: Path, user: str, pythonw: Path, wmux_exe: Path, bot_count: int) -> dict:
    repo = Path(repo).resolve()
    pythonw = Path(pythonw).resolve()
    wmux_exe = Path(wmux_exe).resolve()
    return {
        "wmux_run": {
            "exists": True,
            "value": _quoted_executable(wmux_exe),
            "type": "REG_SZ",
        },
        "bridge_task": {
            "exists": True,
            "enabled": True,
            "execute": str(pythonw),
            "arguments": f'"{repo / "feishu" / "feishu_bridge.py"}" start',
            "working_directory": str(repo),
            "trigger_user": user,
            "trigger_delay": "PT1M",
            "trigger_enabled": True,
            "principal_user": user,
            "principal_account": user,
            "logon_type": "InteractiveToken",
            "run_level": "LeastPrivilege",
            "multiple_instances": "IgnoreNew",
            "execution_time_limit": "PT0S",
            "start_when_available": True,
            "disallow_start_on_batteries": False,
            "stop_if_going_on_batteries": False,
            "description": "登录后延迟 1 分钟启动 Link16 飞书桥。",
        },
        "legacy_watchdog_task": {"exists": False, "enabled": False},
    }


def _looks_managed_wmux(state: dict) -> bool:
    value = str(state.get("value") or "").strip().strip('"').replace("/", "\\").casefold()
    return state.get("exists") and value.endswith("\\wmux.exe") and "\\wmux\\" in value


def _looks_managed_bridge(state: dict) -> bool:
    return state.get("exists") and "feishu_bridge.py" in str(state.get("arguments") or "").casefold()


def materialize_desired(before: dict, desired: dict) -> dict:
    """Keep an existing legacy task recoverable while making it disabled."""
    result = json.loads(json.dumps(desired))
    legacy = before.get("legacy_watchdog_task") or {"exists": False, "enabled": False}
    if legacy.get("exists"):
        result["legacy_watchdog_task"] = {**legacy, "enabled": False}
    return result


def build_plan(before: dict, desired: dict, *, machine: str, user: str, repo: Path,
               pythonw: Path, wmux_exe: Path, bot_count: int) -> dict:
    actions = []
    for key in ("wmux_run", "bridge_task", "legacy_watchdog_task"):
        old, new = before[key], desired[key]
        if key == "legacy_watchdog_task":
            if not old.get("exists") or not old.get("enabled"):
                status, operation = "ok", "none"
            else:
                status, operation = "change", "disable"
        elif _state_equal(old, new):
            status, operation = "ok", "none"
        elif not old.get("exists"):
            status, operation = "change", "create"
        elif key == "wmux_run" and _looks_managed_wmux(old):
            status, operation = "change", "update"
        elif key == "bridge_task" and _looks_managed_bridge(old):
            status, operation = "change", "update"
        else:
            status, operation = "conflict", "none"
        actions.append({
            "key": key,
            "status": status,
            "operation": operation,
            "before": _public_state(old),
            "after": _public_state(new),
        })
    body = {
        "schema": SCHEMA,
        "machine": machine,
        "user": user,
        "repo": str(Path(repo).resolve()),
        "pythonw": str(Path(pythonw).resolve()),
        "wmux_exe": str(Path(wmux_exe).resolve()),
        "bot_count": int(bot_count),
        "actions": actions,
        "maintenance_required": any(
            row["key"] == "legacy_watchdog_task" and row["operation"] == "disable"
            for row in actions
        ),
    }
    body["digest"] = _digest(body)
    return body


class WindowsBackend:
    def __init__(self):
        if os.name != "nt":
            raise OSError("service installer 只支持 Windows")

    @staticmethod
    def _powershell(script: str, *, env=None, input_text=None) -> str:
        child = os.environ.copy()
        if env:
            child.update({key: str(value) for key, value in env.items()})
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=child, creationflags=NO_WINDOW, check=False,
        )
        if done.returncode != 0:
            raise OSError((done.stderr or done.stdout or "PowerShell failed").strip())
        return done.stdout.strip()

    def get_run(self) -> dict:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                value, value_type = winreg.QueryValueEx(key, RUN_VALUE)
            label = "REG_EXPAND_SZ" if value_type == winreg.REG_EXPAND_SZ else "REG_SZ"
            return {"exists": True, "value": str(value), "type": label}
        except FileNotFoundError:
            return {"exists": False, "value": "", "type": "REG_SZ"}

    def set_run(self, state: dict) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value_type = winreg.REG_EXPAND_SZ if state.get("type") == "REG_EXPAND_SZ" else winreg.REG_SZ
            winreg.SetValueEx(key, RUN_VALUE, 0, value_type, str(state["value"]))

    def delete_run(self) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            return

    def get_task(self, name: str) -> dict:
        script = r'''
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$task = Get-ScheduledTask -TaskName $env:LINK16_TASK_NAME -ErrorAction SilentlyContinue
if (-not $task) { @{ exists = $false } | ConvertTo-Json -Compress; exit 0 }
$raw = Export-ScheduledTask -TaskName $env:LINK16_TASK_NAME
$xml = [xml]$raw
$ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
$ns.AddNamespace('t', $xml.DocumentElement.NamespaceURI)
function Txt($path) { $n=$xml.SelectSingleNode($path,$ns); if($n){$n.InnerText}else{''} }
function Bool($path,$default=$false) { $v=Txt $path; if($v -eq ''){$default}else{$v -eq 'true'} }
@{
  exists = $true
  enabled = Bool '//t:Settings/t:Enabled' $true
  execute = Txt '//t:Actions/t:Exec/t:Command'
  arguments = Txt '//t:Actions/t:Exec/t:Arguments'
  working_directory = Txt '//t:Actions/t:Exec/t:WorkingDirectory'
  trigger_user = Txt '//t:Triggers/t:LogonTrigger/t:UserId'
  trigger_delay = Txt '//t:Triggers/t:LogonTrigger/t:Delay'
  trigger_enabled = Bool '//t:Triggers/t:LogonTrigger/t:Enabled' $true
  principal_user = Txt '//t:Principals/t:Principal/t:UserId'
  principal_account = $(try { ([System.Security.Principal.SecurityIdentifier](Txt '//t:Principals/t:Principal/t:UserId')).Translate([System.Security.Principal.NTAccount]).Value } catch { Txt '//t:Principals/t:Principal/t:UserId' })
  logon_type = Txt '//t:Principals/t:Principal/t:LogonType'
  run_level = Txt '//t:Principals/t:Principal/t:RunLevel'
  multiple_instances = Txt '//t:Settings/t:MultipleInstancesPolicy'
  execution_time_limit = Txt '//t:Settings/t:ExecutionTimeLimit'
  start_when_available = Bool '//t:Settings/t:StartWhenAvailable'
  disallow_start_on_batteries = Bool '//t:Settings/t:DisallowStartIfOnBatteries'
  stop_if_going_on_batteries = Bool '//t:Settings/t:StopIfGoingOnBatteries'
  description = Txt '//t:RegistrationInfo/t:Description'
  raw_xml = $raw
} | ConvertTo-Json -Compress
'''
        out = self._powershell(script, env={"LINK16_TASK_NAME": name})
        return json.loads(out)

    def set_bridge_task(self, state: dict) -> None:
        script = r'''
$action = New-ScheduledTaskAction -Execute $env:LINK16_EXECUTE -Argument $env:LINK16_ARGUMENTS -WorkingDirectory $env:LINK16_WORKDIR
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:LINK16_USER
$trigger.Delay = $env:LINK16_DELAY
$principal = New-ScheduledTaskPrincipal -UserId $env:LINK16_USER -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $env:LINK16_TASK_NAME -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $env:LINK16_DESCRIPTION -Force | Out-Null
'''
        self._powershell(script, env={
            "LINK16_EXECUTE": state["execute"], "LINK16_ARGUMENTS": state["arguments"],
            "LINK16_WORKDIR": state["working_directory"], "LINK16_USER": state["trigger_user"],
            "LINK16_DELAY": state["trigger_delay"], "LINK16_TASK_NAME": BRIDGE_TASK,
            "LINK16_DESCRIPTION": state["description"],
        })

    def restore_task(self, name: str, raw_xml: str, enabled: bool) -> None:
        script = r'''
$raw = [Console]::In.ReadToEnd()
Register-ScheduledTask -TaskName $env:LINK16_TASK_NAME -Xml $raw -Force | Out-Null
if ($env:LINK16_ENABLED -eq 'true') { Enable-ScheduledTask -TaskName $env:LINK16_TASK_NAME | Out-Null }
else { Disable-ScheduledTask -TaskName $env:LINK16_TASK_NAME | Out-Null }
'''
        self._powershell(script, env={"LINK16_TASK_NAME": name,
                                     "LINK16_ENABLED": str(bool(enabled)).lower()}, input_text=raw_xml)

    def unregister_task(self, name: str) -> None:
        self._powershell(
            "Unregister-ScheduledTask -TaskName $env:LINK16_TASK_NAME -Confirm:$false",
            env={"LINK16_TASK_NAME": name},
        )

    def set_task_enabled(self, name: str, enabled: bool) -> None:
        verb = "Enable" if enabled else "Disable"
        self._powershell(f"{verb}-ScheduledTask -TaskName $env:LINK16_TASK_NAME | Out-Null",
                         env={"LINK16_TASK_NAME": name})


def inspect_state(backend) -> dict:
    return {
        "wmux_run": backend.get_run(),
        "bridge_task": backend.get_task(BRIDGE_TASK),
        "legacy_watchdog_task": backend.get_task(LEGACY_TASK),
    }


def _restore_item(backend, key: str, before: dict) -> None:
    if key == "wmux_run":
        backend.set_run(before) if before.get("exists") else backend.delete_run()
    elif key == "bridge_task":
        if before.get("exists"):
            backend.restore_task(BRIDGE_TASK, before["raw_xml"], bool(before.get("enabled", True)))
        else:
            backend.unregister_task(BRIDGE_TASK)
    elif key == "legacy_watchdog_task" and before.get("exists"):
        backend.set_task_enabled(LEGACY_TASK, bool(before.get("enabled")))


def apply_plan(plan: dict, before: dict, desired: dict, backend) -> tuple[str, list[dict]]:
    if any(row["status"] == "conflict" for row in plan["actions"]):
        raise ValueError("计划含 conflict，不能 apply")
    completed = []
    try:
        for row in plan["actions"]:
            key, operation = row["key"], row["operation"]
            if operation == "none":
                continue
            current = inspect_state(backend)[key]
            if not _state_equal(current, before[key]):
                raise RuntimeError(f"{key} 在 plan 后发生漂移，拒绝写入")
            if key == "wmux_run":
                backend.set_run(desired[key])
            elif key == "bridge_task":
                backend.set_bridge_task(desired[key])
            elif key == "legacy_watchdog_task":
                backend.set_task_enabled(LEGACY_TASK, False)
            after = inspect_state(backend)[key]
            if not _state_equal(after, desired[key]):
                raise RuntimeError(f"{key} 写后回读不一致")
            completed.append({"key": key, "before": before[key], "after": after})
        return "applied", completed
    except Exception:
        rollback_errors = []
        for item in reversed(completed):
            try:
                _restore_item(backend, item["key"], item["before"])
            except Exception as exc:  # noqa: BLE001
                rollback_errors.append(f"{item['key']}: {exc}")
        if rollback_errors:
            raise RuntimeError("apply 失败且自动回滚不完整：" + "; ".join(rollback_errors))
        raise


def rollback_receipt(receipt: dict, backend) -> list[str]:
    restored = []
    for item in reversed(receipt.get("items") or []):
        key = item["key"]
        current = inspect_state(backend)[key]
        if not _state_equal(current, item["after"]):
            raise RuntimeError(f"rollback-conflict：{key} 已在 apply 后被修改")
        _restore_item(backend, key, item["before"])
        restored.append(key)
    return restored


def _identity(repo: Path):
    machine = os.environ.get("COMPUTERNAME") or "unknown-machine"
    domain = os.environ.get("USERDOMAIN") or machine
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"
    user = f"{domain}\\{username}"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        found = shutil.which("pythonw")
        pythonw = Path(found) if found else pythonw
    try:
        from windows_bootstrap import wmux_executable
        wmux = wmux_executable()
    except Exception:  # noqa: BLE001
        wmux = None
    roster = HERE / "bridge-bots.local.json"
    try:
        bot_count = len(json.loads(roster.read_text(encoding="utf-8")).get("bots") or [])
    except (OSError, ValueError):
        bot_count = 0
    return machine, user, pythonw, wmux, bot_count


def make_plan(backend, repo=REPO):
    machine, user, pythonw, wmux, bot_count = _identity(Path(repo))
    if not pythonw.is_file() or not wmux or not Path(wmux).is_file():
        missing = []
        if not pythonw.is_file():
            missing.append("pythonw")
        if not wmux or not Path(wmux).is_file():
            missing.append("wmux stable executable")
        raise OSError("缺少 service installer 前置：" + ", ".join(missing))
    before = inspect_state(backend)
    desired = materialize_desired(
        before, desired_state(Path(repo), user, pythonw, Path(wmux), bot_count)
    )
    plan = build_plan(before, desired, machine=machine, user=user, repo=Path(repo),
                      pythonw=pythonw, wmux_exe=Path(wmux), bot_count=bot_count)
    return plan, before, desired


def _print_plan(plan: dict) -> None:
    print(f"Link16 启动项计划 · digest={plan['digest']}")
    print(f"  Windows 用户：{plan['user']}")
    print(f"  仓库：{plan['repo']}")
    print(f"  Python：{plan['pythonw']}")
    print(f"  本机 bot：{plan['bot_count']} 只")
    labels = {"ok": "已验证", "change": "需要人工确认", "conflict": "冲突"}
    for row in plan["actions"]:
        print(f"  [{labels[row['status']]:^8}] {row['key']} · {row['operation']}")
        if row["operation"] != "none" or row["status"] == "conflict":
            print(f"      before={json.dumps(row['before'], ensure_ascii=False, sort_keys=True)}")
            print(f"      after ={json.dumps(row['after'], ensure_ascii=False, sort_keys=True)}")
    if plan["maintenance_required"]:
        print("  ⚠ 持久配置后仍需另约维护窗口：停止 legacy 进程、重启整体桥、验唯一 watchdog。")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Link16 Windows 启动项 plan/apply/rollback")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_p = sub.add_parser("plan")
    plan_p.add_argument("--json", action="store_true")
    apply_p = sub.add_parser("apply")
    apply_p.add_argument("--yes", action="store_true")
    apply_p.add_argument("--expect", "--digest", dest="expect", required=True, help="用户审阅过的 plan digest（--digest 是别名）")
    apply_p.add_argument("--json", action="store_true")
    rollback_p = sub.add_parser("rollback")
    rollback_p.add_argument("--receipt", required=True)
    rollback_p.add_argument("--yes", action="store_true")
    rollback_p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        backend = WindowsBackend()
        if args.command == "rollback":
            if not args.yes:
                parser.error("rollback 需要 --yes")
            receipt_path = Path(args.receipt).resolve()
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            restored = rollback_receipt(receipt, backend)
            result = {"status": "rolled_back", "receipt": str(receipt_path), "restored": restored}
        else:
            plan, before, desired = make_plan(backend)
            if args.command == "plan":
                result = {"status": "planned", "plan": plan}
            else:
                if not args.yes:
                    parser.error("apply 需要 --yes")
                if args.expect != plan["digest"]:
                    raise ValueError("plan digest 已变化；重新 plan 并让用户审阅")
                status, items = apply_plan(plan, before, desired, backend)
                receipt = {
                    "schema": SCHEMA, "id": str(uuid.uuid4()), "created_at": int(time.time()),
                    "plan_digest": plan["digest"], "machine": plan["machine"],
                    "user": plan["user"], "repo": plan["repo"], "status": status,
                    "items": items,
                }
                receipt_path = RECEIPT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{receipt['id'][:8]}.json"
                _atomic_json(receipt_path, receipt)
                result = {"status": status, "receipt": str(receipt_path), "plan": plan}
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "plan":
            _print_plan(result["plan"])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"service installer 失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
