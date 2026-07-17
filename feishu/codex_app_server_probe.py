#!/usr/bin/env python3
"""Read-only PLAN-915 probe for Codex app-server event ordering.

The probe starts an ephemeral app-server thread, drives one representative
turn, and prints only event metadata plus short synthetic probe markers.  It
never writes bridge outbox records and is not used by production startup.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


DEFAULT_PROMPT = (
    "This is a read-only protocol probe. First send a commentary message that "
    "contains PROBE_COMMENTARY. Then call update_plan with two steps: read the "
    "time and report the conclusion. Run the read-only PowerShell command "
    "Get-Date -Format o. End with exactly PROBE_FINAL. Do not edit files and "
    "do not use the network."
)


class AppServerProbe:
    def __init__(self, codex: str, codex_home: Path):
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        # JSON-RPC stdio must attach directly to the native executable on
        # Windows.  A bash/node wrapper can swallow or re-encode the pipe.
        command = [codex, "app-server", "--stdio"]
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self.messages: queue.Queue[dict] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                self.messages.put({"raw": line.rstrip()})

    def _read_stderr(self):
        assert self.proc.stderr
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def send(self, payload: dict):
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        if not method:
            return None
        params = message.get("params") or {}
        item = params.get("item") or {}
        summary = {"method": method}
        for key in ("threadId", "turnId"):
            if params.get(key):
                summary[key] = params[key]
        if item:
            summary["item"] = {
                key: item.get(key)
                for key in ("id", "type", "phase", "status", "text", "tool")
                if item.get(key) is not None
            }
        if method == "turn/plan/updated":
            summary["plan"] = params.get("plan")
        if method == "turn/completed":
            summary["status"] = (params.get("turn") or {}).get("status")
        if "id" in message:
            if method.endswith("requestApproval"):
                self.send({"id": message["id"], "result": {"decision": "decline"}})
            else:
                self.send({
                    "id": message["id"],
                    "error": {"code": -32601, "message": "probe does not implement request"},
                })
        return summary

    def wait_response(self, request_id: int, timeout: float = 60) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if message.get("id") == request_id:
                return message
            summary = self.handle(message)
            if summary:
                print(json.dumps(summary, ensure_ascii=False), flush=True)
        raise TimeoutError(f"request {request_id} timed out")

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run(args) -> int:
    probe = AppServerProbe(args.codex, Path(args.codex_home).expanduser())
    try:
        probe.send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "plan915-probe", "title": "PLAN-915 probe", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
        })
        init = probe.wait_response(1)
        if init.get("error"):
            raise RuntimeError(init["error"])
        probe.send({"method": "initialized"})
        probe.send({
            "id": 2,
            "method": "thread/start",
            "params": {
                "cwd": str(Path(args.cwd).resolve()),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "ephemeral": True,
            },
        })
        started = probe.wait_response(2)
        thread_id = started["result"]["thread"]["id"]
        print(json.dumps({"thread": thread_id, "model": started["result"].get("model")}), flush=True)
        probe.send({
            "id": 3,
            "method": "turn/start",
            "params": {"threadId": thread_id, "input": [{"type": "text", "text": args.prompt}]},
        })
        turn = probe.wait_response(3)
        turn_id = turn["result"]["turn"]["id"]
        deadline = time.time() + args.timeout
        completed = False
        while time.time() < deadline and not completed:
            try:
                message = probe.messages.get(timeout=1)
            except queue.Empty:
                continue
            summary = probe.handle(message)
            if summary:
                print(json.dumps(summary, ensure_ascii=False), flush=True)
            completed = (
                message.get("method") == "turn/completed"
                and (
                    (message.get("params") or {}).get("turnId")
                    or ((message.get("params") or {}).get("turn") or {}).get("id")
                ) == turn_id
            )
        result = {"completed": completed, "turn": turn_id, "stderr_tail": probe.stderr[-5:]}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if completed else 2
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex",
        default=(
            str(Path.home() / "AppData/Roaming/npm/node_modules/@openai/codex/"
                "node_modules/@openai/codex-win32-x64/vendor/"
                "x86_64-pc-windows-msvc/bin/codex.exe")
        ),
    )
    parser.add_argument("--codex-home", default="~/.codex-personal")
    parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=150)
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
