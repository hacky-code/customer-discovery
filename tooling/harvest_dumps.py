#!/usr/bin/env python3
"""
Scan the Claude Code MCP tool-results for Drive `read_file_content` dumps and
copy each into `discovery/tooling/raw/<fileId>.json` for stable addressing.

Two sources are scanned:
  1. Per-session `tool-results/*.txt` envelopes (direct on-disk dumps).
  2. Subagent `subagents/*.jsonl` streams (inline tool_result blocks —
     used when the MCP returns results inline rather than persisting).

The canonical fileId comes from the `tool_use.input.fileId` field paired
to each `tool_result` via `tool_use_id` — built by walking every jsonl
under `~/.claude/projects/`. Falling back to a `/document/d/<id>` regex
inside `fileContent` is unreliable: Gemini-authored transcripts sometimes
contain no self-link at all (silent drop), and others lead with a link to
a sibling "Notes by Gemini" sub-doc (saved under the wrong id). The
regex remains as a last-resort fallback for orphan dumps with no
matching tool_use record.

Paths are derived from `~/.claude/projects/` so the script is portable
across Linux (`/root/...`, `/home/user/...`) and Windows (`C:\\Users\\...`).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOL_RESULTS_ROOT = Path.home() / ".claude" / "projects"
RAW_DIR = Path(__file__).resolve().parent / "raw"
FILE_ID_RE = re.compile(r"/document/d/([A-Za-z0-9_-]{20,})")
DRIVE_TOOL_SUFFIX = "__read_file_content"
PERSISTED_PATH_RE = re.compile(r"Full output saved to:\s*(\S+)")


def _iter_jsonl_lines(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _iter_message_blocks(record: dict):
    msg = record.get("message") if isinstance(record, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict):
            yield block


def _build_jsonl_indexes() -> tuple[dict[str, str], dict[str, str]]:
    """Return (toolu_id -> fileId, persisted_path -> toolu_id)."""
    toolu_to_fileid: dict[str, str] = {}
    persisted_to_toolu: dict[str, str] = {}
    for jsonl in TOOL_RESULTS_ROOT.glob("**/*.jsonl"):
        for rec in _iter_jsonl_lines(jsonl):
            for block in _iter_message_blocks(rec):
                bt = block.get("type")
                tu_name = block.get("name") if bt == "tool_use" else None
                if isinstance(tu_name, str) and tu_name.endswith(DRIVE_TOOL_SUFFIX) and tu_name.startswith("mcp__"):
                    inp = block.get("input") or {}
                    fid = inp.get("fileId")
                    tid = block.get("id")
                    if isinstance(fid, str) and isinstance(tid, str):
                        toolu_to_fileid[tid] = fid
                elif bt == "tool_result":
                    tid = block.get("tool_use_id")
                    body = block.get("content")
                    text_parts: list[str] = []
                    if isinstance(body, str):
                        text_parts.append(body)
                    elif isinstance(body, list):
                        for b in body:
                            if isinstance(b, dict) and b.get("type") == "text":
                                t = b.get("text")
                                if isinstance(t, str):
                                    text_parts.append(t)
                    for txt in text_parts:
                        m = PERSISTED_PATH_RE.search(txt)
                        if m and isinstance(tid, str):
                            persisted_to_toolu[m.group(1)] = tid
    return toolu_to_fileid, persisted_to_toolu


def _persist_envelope(
    data: dict,
    harvested: list[str],
    existing_ids: set[str],
    canonical_file_id: str | None = None,
) -> None:
    content = data.get("fileContent") if isinstance(data, dict) else None
    if not isinstance(content, str):
        return
    file_id = canonical_file_id
    if not file_id:
        m = FILE_ID_RE.search(content)
        if not m:
            return
        file_id = m.group(1)
    if file_id in existing_ids:
        return
    dest = RAW_DIR / f"{file_id}.json"
    dest.write_text(json.dumps(data), encoding="utf-8")
    existing_ids.add(file_id)
    harvested.append(file_id)


def _scan_dump_files(
    harvested: list[str],
    existing_ids: set[str],
    toolu_to_fileid: dict[str, str],
    persisted_to_toolu: dict[str, str],
) -> None:
    patterns = [
        "**/tool-results/toolu_*.txt",
        "**/tool-results/mcp-Google-Drive-read_file_content-*.txt",
    ]
    dumps: list[Path] = []
    for pat in patterns:
        dumps.extend(TOOL_RESULTS_ROOT.glob(pat))
    for dump in sorted(dumps, key=lambda p: p.stat().st_mtime):
        try:
            text = dump.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        stripped = text.lstrip()
        if not stripped.startswith("{"):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        canonical: str | None = None
        name = dump.name
        if name.startswith("toolu_") and name.endswith(".txt"):
            canonical = toolu_to_fileid.get(name[: -len(".txt")])
        if canonical is None:
            tid = persisted_to_toolu.get(str(dump))
            if tid:
                canonical = toolu_to_fileid.get(tid)
        _persist_envelope(data, harvested, existing_ids, canonical)


def _scan_subagent_jsonls(
    harvested: list[str],
    existing_ids: set[str],
    toolu_to_fileid: dict[str, str],
) -> None:
    for jsonl in TOOL_RESULTS_ROOT.glob("**/subagents/*.jsonl"):
        try:
            lines = jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            if "fileContent" not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for block in _iter_message_blocks(record):
                if block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id")
                canonical = toolu_to_fileid.get(tid) if isinstance(tid, str) else None
                body = block.get("content")
                text_parts: list[str] = []
                if isinstance(body, str):
                    text_parts.append(body)
                elif isinstance(body, list):
                    for b in body:
                        if isinstance(b, dict) and b.get("type") == "text":
                            t = b.get("text")
                            if isinstance(t, str):
                                text_parts.append(t)
                for txt in text_parts:
                    stripped = txt.lstrip()
                    if not stripped.startswith("{"):
                        continue
                    try:
                        data = json.loads(txt)
                    except json.JSONDecodeError:
                        continue
                    _persist_envelope(data, harvested, existing_ids, canonical)


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {p.stem for p in RAW_DIR.glob("*.json")}
    harvested: list[str] = []
    toolu_to_fileid, persisted_to_toolu = _build_jsonl_indexes()
    _scan_dump_files(harvested, existing_ids, toolu_to_fileid, persisted_to_toolu)
    _scan_subagent_jsonls(harvested, existing_ids, toolu_to_fileid)
    print(json.dumps({"harvested": harvested, "total_raw": len(list(RAW_DIR.glob('*.json')))}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
