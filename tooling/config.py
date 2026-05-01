"""Config loader for the customer-discovery skill.

Reads `config.json` next to this file. Stripped of `_*_help` keys so the
loaded config dict is clean for direct use. All scripts import from here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _strip_help_keys(obj):
    if isinstance(obj, dict):
        return {k: _strip_help_keys(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_help_keys(v) for v in obj]
    return obj


def load() -> dict:
    if not CONFIG_PATH.exists():
        sys.stderr.write(
            f"ERROR: config not initialised ({CONFIG_PATH} missing).\n"
            "Run /customer-discovery in Claude Code — the skill will ask you for your "
            "name + Drive folder and write the config for you.\n"
        )
        sys.exit(2)
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = _strip_help_keys(raw)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    required = ["interviewer_name", "drive_folders", "output_dir", "pivot", "classifier"]
    missing = [k for k in required if k not in cfg]
    if missing:
        sys.stderr.write(f"ERROR: config.json missing required keys: {missing}\n")
        sys.exit(2)
    if not cfg["interviewer_name"].strip():
        sys.stderr.write("ERROR: config.json interviewer_name must be non-empty.\n")
        sys.exit(2)
    if cfg["interviewer_name"] == "YourName":
        sys.stderr.write(
            "ERROR: config not initialised (interviewer_name is still the placeholder 'YourName').\n"
            "Run /customer-discovery in Claude Code — the skill will finish setup for you.\n"
        )
        sys.exit(2)
    if not cfg["drive_folders"]:
        sys.stderr.write("ERROR: config.json drive_folders must be a non-empty list.\n")
        sys.exit(2)
    for f in cfg["drive_folders"]:
        if not isinstance(f, dict) or "id" not in f or "scope" not in f:
            sys.stderr.write(f"ERROR: drive_folders entry malformed: {f}\n")
            sys.exit(2)
        if f["scope"] not in ("primary", "secondary"):
            sys.stderr.write(
                f"ERROR: drive_folders[{f.get('name','?')}] scope must be 'primary' or 'secondary' (got {f['scope']!r}).\n"
            )
            sys.exit(2)
        if f["id"].startswith("REPLACE-") or not f["id"].strip():
            sys.stderr.write(
                f"ERROR: config not initialised (drive_folders[{f.get('name','?')}].id is still a placeholder).\n"
                "Run /customer-discovery in Claude Code — the skill will finish setup for you.\n"
            )
            sys.exit(2)
