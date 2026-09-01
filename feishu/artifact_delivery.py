#!/usr/bin/env python3
"""Machine-wide policy for reviewable artifact delivery.

The policy controls only whether Link16 may create an online Feishu copy.
Local-path display, original-file attachment authority, and GUI opening are
separate decisions owned by the calling workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


POLICY_VERSION = 1
POLICY_PATH = Path(__file__).resolve().with_name("artifact-delivery.local.json")


class ArtifactDeliveryPolicyError(ValueError):
    """Raised when the local policy is unreadable or violates its schema."""


class OnlineArtifactDeliveryDisabled(RuntimeError):
    """Raised before network work when online publication is not authorized."""


@dataclass(frozen=True)
class ArtifactDeliveryDecision:
    publish_online: bool
    show_local_path: bool
    attachment_mode: str
    source: str
    config_path: str
    reason: str


def _validate(raw: object, *, source: Path) -> bool:
    if not isinstance(raw, dict):
        raise ArtifactDeliveryPolicyError(f"交付策略必须是 JSON object：{source}")
    if raw.get("version") != POLICY_VERSION:
        raise ArtifactDeliveryPolicyError(
            f"交付策略 version 必须是 {POLICY_VERSION}：{source}"
        )
    enabled = raw.get("online_artifacts")
    if not isinstance(enabled, bool):
        raise ArtifactDeliveryPolicyError(
            f"online_artifacts 必须是 true/false：{source}"
        )
    return enabled


def load_online_setting(path: Path | str = POLICY_PATH) -> tuple[bool, str]:
    target = Path(path).resolve()
    if not target.exists():
        return False, "built-in-default"
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactDeliveryPolicyError(f"交付策略读取失败：{target}：{exc}") from exc
    return _validate(raw, source=target), str(target)


def set_online_setting(enabled: bool, path: Path | str = POLICY_PATH) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": POLICY_VERSION, "online_artifacts": bool(enabled)}
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)
    return target


def resolve_artifact_delivery(
    explicit: str = "auto", path: Path | str = POLICY_PATH
) -> ArtifactDeliveryDecision:
    if explicit not in {"auto", "online", "local"}:
        raise ArtifactDeliveryPolicyError(
            "explicit 必须是 auto、online 或 local"
        )
    enabled, source = load_online_setting(path)
    if explicit == "online":
        publish_online, reason = True, "explicit-user-online-request"
    elif explicit == "local":
        publish_online, reason = False, "explicit-user-local-request"
    else:
        publish_online = enabled
        reason = "machine-policy-on" if enabled else "machine-policy-off"
    return ArtifactDeliveryDecision(
        publish_online=publish_online,
        show_local_path=True,
        attachment_mode="explicit-request-only",
        source=source,
        config_path=str(Path(path).resolve()),
        reason=reason,
    )


def require_online_publication(
    *, explicit_online: bool = False, path: Path | str = POLICY_PATH
) -> ArtifactDeliveryDecision:
    decision = resolve_artifact_delivery(
        "online" if explicit_online else "auto", path=path
    )
    if not decision.publish_online:
        raise OnlineArtifactDeliveryDisabled(
            "飞书在线产物全局开关为 off：本轮只显示本地绝对路径；"
            "全局开启请运行 `python feishu/artifact_delivery.py set-online on`；"
            "用户仅在当前轮明确要求在线副本时，发送工具加 `--explicit-online`。"
        )
    return decision


def _payload(decision: ArtifactDeliveryDecision) -> dict[str, object]:
    return asdict(decision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Link16 本机全局产物交付策略（只控制是否创建飞书在线副本）"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="查看当前全局策略")
    status.add_argument("--json", action="store_true")
    setter = sub.add_parser("set-online", help="全局开启或关闭在线副本")
    setter.add_argument("value", choices=["on", "off"])
    setter.add_argument("--json", action="store_true")
    decide = sub.add_parser("decide", help="解析一次产物交付决策")
    decide.add_argument("--explicit", choices=["auto", "online", "local"], default="auto")
    decide.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "set-online":
        set_online_setting(args.value == "on")
    explicit = args.explicit if args.command == "decide" else "auto"
    decision = resolve_artifact_delivery(explicit)
    if args.json:
        print(json.dumps(_payload(decision), ensure_ascii=False))
    else:
        state = "on" if decision.publish_online else "off"
        print(f"online_artifacts={state}")
        print(f"source={decision.source}")
        print(f"config={decision.config_path}")
        print(f"reason={decision.reason}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactDeliveryPolicyError, OnlineArtifactDeliveryDisabled) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
