"""Match a fleet registration to the organization shown by Feishu's own console."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from bridge_env import registry_path
from bridge_injection import ProcessFileLock
from registration_transport import RegistrationTransportError


CONSOLE = "https://open.feishu.cn/app"
MACHINE_KIND = {"tb26": "enterprise", "tx26": "enterprise", "tuf19": "personal",
                "tb24": "personal", "tb25": "personal"}


def fleet_target(bot, name, registry=None, hostname=None):
    if registry is None:
        path = registry_path()
        registry = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    prefixes = {re.split(r"[-_]", item.strip().lower(), maxsplit=1)[0]
                for item in (bot, name) if item}
    machines = prefixes.intersection(MACHINE_KIND)
    if not machines:
        host = (hostname or socket.gethostname()).casefold()
        machines = {k.lower() for k, v in registry.get("machines", {}).items()
                    if str(v.get("hostname", "")).casefold() == host and k.lower() in MACHINE_KIND}
        if not machines and host in MACHINE_KIND:
            machines = {host}
    if not machines:
        return None
    if len(machines) != 1:
        raise ValueError("显示名、运行代号或本机登记指向不同机器；核对目标后再注册")
    machine = machines.pop()
    kind = MACHINE_KIND[machine]
    entries = [t for t in registry.get("tenants", []) if t.get("kind") == kind]
    if len(entries) > 1 or (kind == "enterprise" and not entries):
        raise ValueError(f"{machine.upper()} 的目标组织不唯一或未登记，请核对名册 tenants")
    entry = entries[0] if entries else {}
    organization = entry.get("console_name") or ("飞书个人版" if kind == "personal" else entry.get("label"))
    if not organization:
        raise ValueError("名册缺少飞书后台显示的企业名称")
    return {"machine": machine, "kind": kind, "organization": organization}


def browser_pages(**request):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if os.name != "nt" or not shell:
        raise RegistrationTransportError("当前机器无法读取 Windows Chrome/Edge 的飞书组织；创建已停止")
    command = [shell, "-NoProfile", "-NonInteractive", "-File",
               str(Path(__file__).with_name("registration_browser.ps1"))]
    try:
        proc = subprocess.run(command, input=json.dumps(request), capture_output=True,
                              encoding="utf-8", timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    raise RegistrationTransportError("无法读取飞书开发者后台窗口，请在本机 Chrome/Edge 打开开发者后台")


def inspect_target(target, app_id=None):
    path = f"/app/{app_id}/baseinfo" if app_id else "/app"
    actual = sorted({p["organization"] for p in browser_pages() if p["path"] == path and p["organization"]})
    return {"machine": target["machine"], "expected": target["organization"], "observed": actual,
            "app_id": app_id, "matched": target["organization"] in actual}


class RegistrationBrowser:
    def __init__(self, target, check_active):
        self.target = target
        self.check_active = check_active
        self.window = None
        self.launcher_code = None

    def select_console(self):
        org = self.target["organization"]
        opened = False
        picker_shown = False
        while True:
            self.check_active()
            pages = [p for p in browser_pages() if p["path"] == "/app"]
            match = next((p for p in pages if p["organization"] == org), None)
            if match:
                self.window = match["window"]
                return
            if not opened:
                if not pages:
                    webbrowser.open_new(CONSOLE)
                print(f"请在 Chrome/Edge 飞书开发者后台切换到「{org}」，识别到后继续。", flush=True)
                opened = True
            wrong = next((p for p in pages if p["organization"]), None)
            if wrong and not picker_shown:
                browser_pages(window=wrong["window"], show_picker=True,
                              switch_labels=["Switch Account", "Switch Team", "切换账号", "切换帐号", "切换团队"])
                picker_shown = True
            time.sleep(3)

    def open_launcher(self, url):
        uri = urlsplit(url)
        codes = parse_qs(uri.query).get("user_code", [])
        if (uri.scheme, uri.netloc, uri.path) != ("https", "open.feishu.cn", "/page/launcher") or len(codes) != 1:
            raise RegistrationTransportError("SDK 确认页地址无法核实；未打开创建页")
        self.check_active()
        if not browser_pages(window=self.window, from_path="/app", organization=self.target["organization"], url=url):
            raise RegistrationTransportError("打开创建页前组织或窗口已改变，请重新核对开发者后台")
        self.launcher_code = codes[0]
        print(f"已在「{self.target['organization']}」所在窗口打开确认页；切换账号后须重新核组织。", flush=True)

    def window_lock(self):
        # A second registration must not replace an SDK confirmation in this window.
        state = Path(__file__).with_name("_state")
        return ProcessFileLock(state / f"registration-browser-{self.window}.lck", timeout=5)

    def verify_app(self, app_id):
        if not re.fullmatch(r"cli_[A-Za-z0-9_]+", app_id):
            raise RegistrationTransportError("飞书返回的 App ID 格式无效")
        path = f"/app/{app_id}/baseinfo"
        opened = False
        while True:
            self.check_active()
            pages = browser_pages()
            detail = next((p for p in pages if p["path"] == path and p["organization"]), None)
            if detail:
                if detail["organization"] != self.target["organization"]:
                    raise TenantMismatch(f"应用 {app_id} 属于「{detail['organization']}」，"
                                         f"预期「{self.target['organization']}」；已拒绝接入")
                return {"app_id": app_id, "organization": detail["organization"], "source": "console-app-detail"}
            page = next((p for p in pages if p["window"] == self.window), {})
            if not opened:
                detail_url = "https://open.feishu.cn" + path
                source = page.get("path")
                if source == "/app" or (source == "/page/launcher" and self.launcher_code):
                    moved = browser_pages(window=self.window, from_path=source, url=detail_url,
                                          launcher_code=self.launcher_code if source == "/page/launcher" else None)
                else:
                    moved = False
                if not moved:
                    print("等待此应用的飞书组织标识：" + detail_url, flush=True)
                    webbrowser.open_new(detail_url)
                opened = True
            time.sleep(3)


class TenantMismatch(RegistrationTransportError):
    pass
