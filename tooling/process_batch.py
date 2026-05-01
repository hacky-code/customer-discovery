#!/usr/bin/env python3
"""
Batch driver for discovery transcript processing.

Given a TSV manifest on stdin (fileId \\t slug \\t dumpPath \\t createdTime
\\t title), classify + extract + write the three transcript artifacts and
emit a JSON results array on stdout. Output directories come from
`config.json` (output_dir + the three subfolder names).

This script does NOT call Google Drive — the agent supplies the raw dumps
via MCP. Pure-Python and reproducible.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import load as load_config
from parse_transcript import (
    extract_transcript,
    find_pivot_offset,
    is_relevant_call,
    load_input,
)


def process_one(
    cfg: dict,
    file_id: str,
    slug: str,
    dump_path: str,
    created_iso: str,
    title: str,
) -> dict:
    output_dir = Path(cfg["output_dir"])
    full_dir = output_dir / "full raw transcripts"
    unbiased_dir = output_dir / "unbiased raw transcripts"
    feedback_dir = output_dir / "feedback raw transcripts"

    p = Path(dump_path)
    if not p.exists():
        alt = p.with_suffix(".md") if p.suffix == ".json" else p.with_suffix(".json")
        if alt.exists():
            dump_path = str(alt)
    md = load_input(dump_path)
    relevant = is_relevant_call(md, cfg)
    transcript = extract_transcript(md)

    out: dict = {
        "fileId": file_id,
        "title": title,
        "createdTime": created_iso,
        "slug": slug,
        "relevant": relevant,
        "has_transcript": transcript is not None,
    }

    if transcript is None:
        out["note"] = "no Transcript section found"
        return out

    out["transcript_chars"] = len(transcript)

    if not relevant:
        out["note"] = "classified as not relevant"
        return out

    pivot = find_pivot_offset(transcript, cfg)
    if pivot is None:
        unbiased = transcript
        feedback = None
        out["pivot_matched"] = None
        out["warning"] = "no pivot detected; unbiased == full; no feedback section"
    else:
        unbiased = transcript[: pivot[0]].rstrip() + "\n"
        feedback = transcript[pivot[0] :]
        out["pivot_matched"] = pivot[1]
        out["pivot_offset"] = pivot[0]

    out["unbiased_chars"] = len(unbiased)
    if feedback is not None:
        out["feedback_chars"] = len(feedback)

    full_dir.mkdir(parents=True, exist_ok=True)
    unbiased_dir.mkdir(parents=True, exist_ok=True)
    full_path = full_dir / f"{slug}.md"
    unbiased_path = unbiased_dir / f"{slug}.md"
    full_path.write_text(transcript, encoding="utf-8")
    unbiased_path.write_text(unbiased, encoding="utf-8")

    out["full_path"] = str(full_path)
    out["unbiased_path"] = str(unbiased_path)

    if feedback is not None:
        feedback_dir.mkdir(parents=True, exist_ok=True)
        feedback_path = feedback_dir / f"{slug}.md"
        feedback_path.write_text(feedback, encoding="utf-8")
        out["feedback_path"] = str(feedback_path)
    return out


def main() -> int:
    cfg = load_config()
    results = []
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        file_id, slug, dump_path, created_iso, title = parts[:5]
        results.append(process_one(cfg, file_id, slug, dump_path, created_iso, title))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
