#!/usr/bin/env python3
"""公开仓的常驻脱敏闸 —— 挡住「以后某次提交又把真东西带进来」。

本仓 2026-09-08 起是公开仓，而且不会再转回私有。所以「这一次清干净了」不够，
必须有个每次跑测试都会拦一道的机械闸。

它按**模式**判断，不按名单：这里绝不能写死维护者的真实 open_id / 主机名，
否则这个文件本身就成了泄漏源。判据是「长得像真值」，占位符按形态放行。

覆盖：飞书 open_id / chat_id / app_id、租户 key、私钥、各家 API token、JWT、
中国手机号、真人邮箱、Windows 用户名绝对路径、内网 IP、真实主机名。

要加新的允许值（例如一个新的示例串），改 ALLOWLIST 的形态规则，
不要把真值加进白名单。
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 占位符/示例的形态特征。命中任一即视为「不是真值」。
ALLOWLIST = re.compile(
    r"0{6,}"                 # ou_0000...、oc_0000...
    r"|x{6,}"                # cli_xxxxxxxx
    r"|example|demo|sample|placeholder|your[_-]|<[^>]*>"
    r"|noreply|users\.noreply|@example\.|git@github\.com"
    r"|%USERPROFILE%|\$HOME|~/"
    r"|YOUR_|TENANT_KEY_|CLOUDFLARE_|D1_DATABASE|LAN_IP|_HOSTNAME",
    re.I,
)

PATTERNS = [
    ("飞书 bot open_id", re.compile(r"ou_[0-9a-f]{28,}")),
    ("飞书群 chat_id", re.compile(r"oc_[0-9a-f]{28,}")),
    ("飞书应用 app_id", re.compile(r"cli_[a-z0-9]{16,}")),
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("OpenAI/Anthropic key", re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}")),
    ("中国手机号", re.compile(r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])")),
    ("真人邮箱", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("Windows 用户名绝对路径", re.compile(r"(?i)C:\\Users\\[A-Za-z0-9_\-]+")),
    ("内网 IP", re.compile(
        r"(?<![0-9.])(?:192\.168\.[0-9]{1,3}\.[0-9]{1,3}"
        r"|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})(?![0-9.])")),
    # 大小写敏感：真实 Windows 主机名是全大写（DESKTOP-XXXXXXX），
    # 小写的 desktop-shortcut 之类是代码里的普通标识符，不该误报。
    ("真实主机名", re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Z0-9]{7,}\b")),
]

# 二进制与自身：本文件写满了模式，扫自己必然自我命中。
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".mp4", ".woff", ".woff2"}
SKIP_FILES = {"tests/test_no_secrets_committed.py"}


def tracked_text_files():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, errors="replace").stdout
    for rel in out.split("\n"):
        rel = rel.strip()
        if not rel or rel in SKIP_FILES:
            continue
        if Path(rel).suffix.lower() in SKIP_SUFFIX:
            continue
        path = ROOT / rel
        try:
            yield rel, path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


class NoSecretsCommitted(unittest.TestCase):
    def test_tracked_files_contain_no_real_identifiers(self):
        hits = []
        for rel, text in tracked_text_files():
            for label, pat in PATTERNS:
                for m in pat.finditer(text):
                    value = m.group(0)
                    if ALLOWLIST.search(value):
                        continue
                    line = text[:m.start()].count("\n") + 1
                    hits.append("%s:%d  [%s]  %s" % (rel, line, label, value))
        self.assertEqual(
            hits, [],
            "\n\n本仓是公开仓：下面这些看起来是【真实身份或凭据】，不能提交。\n"
            "占位符请用形态明确的写法（连续 0、example、YOUR_、%USERPROFILE% 等），\n"
            "真数据放仓外（凭据→.env；舰队名册→profile home，见 README「两本名册」）。\n\n"
            + "\n".join(hits))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
