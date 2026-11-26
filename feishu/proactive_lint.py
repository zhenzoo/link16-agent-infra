"""Validate a workbench entry against its registered round before publishing."""
import argparse
import json
from pathlib import Path

from proactive_business_contract import validate_html


def validate_file(entry, round_json=None):
    plan=json.loads(round_json.read_text("utf-8-sig")) if round_json else None
    validate_html(entry.read_text("utf-8-sig"),plan)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html",type=Path,required=True)
    parser.add_argument("--round-json",type=Path,required=True)
    args=parser.parse_args()
    try:
        validate_file(args.html,args.round_json)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        parser.exit(1,f"工作台校验失败：{exc}\n")
    print("工作台校验通过")
