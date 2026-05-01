#!/usr/bin/env python3
"""
Parse a Gemini-generated meeting notes markdown export (from Google Drive's
`read_file_content` MCP tool) into three artifacts:

1. full raw transcript  - the Transcript section only, with the generated
   Notes / Summary / Details / Next steps stripped out.
2. unbiased raw transcript - the full raw transcript truncated at the point
   where the interviewer pivots from listening to pitching their own idea
   ("I've been talking to a bunch of product leaders...", "let me share what
   I've learned", etc).
3. feedback raw transcript - the complement of (2): the post-pivot portion
   where the interviewer shares what they've heard and the interviewee
   reacts. Written only when a pivot was detected.

Configuration (interviewer name, pivot patterns, classifier keywords, output
directories) lives in `config.json` next to this script. See SKILL.md.

Input: JSON produced by the Drive MCP `read_file_content` call (expects a top
level `fileContent` key), OR raw markdown read from stdin / a file path.

Usage:
    python parse_transcript.py <input.json-or-md> <slug> [--full-dir DIR] [--unbiased-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import load as load_config

# Regex to find the Transcript section header (unicode emoji varies)
TRANSCRIPT_HEADER_RE = re.compile(r"\n#\s+[^\n]*Transcript[^\n]*\n", re.IGNORECASE)
# Regex for timestamped blocks: ### HH:MM:SS
TIMESTAMP_RE = re.compile(r"^###\s+(\d{1,2}:\d{2}:\d{2})\s*$", re.MULTILINE)
# Regex for speaker lines: **Name:** text
SPEAKER_LINE_RE = re.compile(r"\*\*([^*]+):\*\*\s*(.*)")
# Alternate transcript format: \[Speaker Name\] on its own line, utterance below.
BRACKET_SPEAKER_RE = re.compile(r"^\\\[([^\]\\]+)\\\]\s*$", re.MULTILINE)


def load_input(path: str) -> str:
    """Return the markdown body from either a JSON envelope or raw markdown."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "fileContent" in data:
                return data["fileContent"]
        except json.JSONDecodeError:
            pass
    return text


def extract_transcript(markdown: str) -> str | None:
    """Return the Transcript section only, with surrounding notes stripped."""
    m = TRANSCRIPT_HEADER_RE.search(markdown)
    if m:
        transcript = markdown[m.start() + 1 :]
        end_marker = re.search(
            r"###\s+Transcription ended[^\n]*\n", transcript, re.IGNORECASE
        )
        if end_marker:
            transcript = transcript[: end_marker.end()]
        return transcript.strip() + "\n"
    if len(BRACKET_SPEAKER_RE.findall(markdown)) >= 10:
        return markdown.strip() + "\n"
    if len(SPEAKER_LINE_RE.findall(markdown)) >= 10:
        return markdown.strip() + "\n"
    return None


def _seconds(ts: str) -> int:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


_SENTENCE_BREAK_RE = re.compile(r"[.!?]+\s+")


def _sentence_span(utter: str, pos: int) -> tuple[int, int]:
    start = 0
    end = len(utter)
    for m in _SENTENCE_BREAK_RE.finditer(utter):
        if m.end() <= pos:
            start = m.end()
        else:
            end = m.start() + 1
            break
    return start, end


def _is_preamble_telegraph(
    utter: str, pivot_match: re.Match, negative_preambles: list[str]
) -> bool:
    """True if a negative-context phrase precedes the pivot hit IN THE SAME
    SENTENCE. Cross-sentence telegraphs are treated as real pivots."""
    sstart, send = _sentence_span(utter, pivot_match.start())
    sentence = utter[sstart:send]
    rel_pivot = pivot_match.start() - sstart
    for neg in negative_preambles:
        nm = re.search(neg, sentence)
        if nm and nm.start() < rel_pivot:
            return True
    return False


def _first_pivot_match(
    utter: str, patterns: list[str], negative_preambles: list[str]
) -> tuple[re.Match, str] | None:
    """Iterate ALL matches per pattern and return the first one that survives
    the preamble guard."""
    for pat in patterns:
        for pm in re.finditer(pat, utter):
            if not _is_preamble_telegraph(utter, pm, negative_preambles):
                return pm, pat
    return None


def _is_interviewer(speaker: str, interviewer_name: str) -> bool:
    return interviewer_name.lower() in speaker.lower()


def _scan_timestamped(
    transcript: str,
    blocks: list[tuple[int, int, int]],
    patterns: list[str],
    min_seconds: int,
    interviewer_name: str,
    negative_preambles: list[str],
) -> tuple[int, str] | None:
    for secs, bstart, bend in blocks:
        if secs < min_seconds:
            continue
        chunk = transcript[bstart:bend]
        for line_m in SPEAKER_LINE_RE.finditer(chunk):
            if not _is_interviewer(line_m.group(1).strip(), interviewer_name):
                continue
            utter = line_m.group(2).lower()
            hit = _first_pivot_match(utter, patterns, negative_preambles)
            if hit is None:
                continue
            abs_offset = bstart + line_m.start()
            prior_nl = transcript.rfind("\n", 0, abs_offset)
            cut = prior_nl + 1 if prior_nl != -1 else abs_offset
            return (cut, hit[1])
    return None


def _scan_bracket(
    transcript: str,
    bracket_turns: list[re.Match],
    patterns: list[str],
    min_interviewer_turns: int,
    interviewer_name: str,
    negative_preambles: list[str],
) -> tuple[int, str] | None:
    interviewer_count = 0
    for i, tm in enumerate(bracket_turns):
        utter_start = tm.end()
        utter_end = (
            bracket_turns[i + 1].start()
            if i + 1 < len(bracket_turns)
            else len(transcript)
        )
        utter = transcript[utter_start:utter_end].lower()
        if not _is_interviewer(tm.group(1).strip(), interviewer_name):
            continue
        interviewer_count += 1
        if interviewer_count < min_interviewer_turns:
            continue
        hit = _first_pivot_match(utter, patterns, negative_preambles)
        if hit is None:
            continue
        abs_offset = tm.start()
        prior_nl = transcript.rfind("\n", 0, abs_offset)
        cut = prior_nl + 1 if prior_nl != -1 else abs_offset
        return (cut, hit[1])
    return None


def _scan_star(
    transcript: str,
    star_turns: list[re.Match],
    patterns: list[str],
    min_interviewer_turns: int,
    interviewer_name: str,
    negative_preambles: list[str],
) -> tuple[int, str] | None:
    interviewer_count = 0
    for line_m in star_turns:
        if not _is_interviewer(line_m.group(1).strip(), interviewer_name):
            continue
        utter = line_m.group(2).lower()
        interviewer_count += 1
        if interviewer_count < min_interviewer_turns:
            continue
        hit = _first_pivot_match(utter, patterns, negative_preambles)
        if hit is None:
            continue
        abs_offset = line_m.start()
        prior_nl = transcript.rfind("\n", 0, abs_offset)
        cut = prior_nl + 1 if prior_nl != -1 else abs_offset
        return (cut, hit[1])
    return None


def _scale_short_call_budget(
    turns: list[re.Match],
    min_interviewer_turns: int,
    hc_min_interviewer_turns: int,
    interviewer_name: str,
) -> tuple[int, int]:
    """Scale the intro-skip turn budget down for short calls.

    Default budget assumes ~min_seconds//60 interviewer turns of intro before
    the pivot is plausible. On long calls that's a small fraction. On short
    calls (e.g. 5 interviewer turns total) the default budget would block the
    entire call. Only kicks in when the call is shorter than the default;
    long-call calibration preserved.
    """
    total = sum(
        1 for tm in turns if _is_interviewer(tm.group(1).strip(), interviewer_name)
    )
    if total == 0 or total >= min_interviewer_turns:
        return min_interviewer_turns, hc_min_interviewer_turns
    scaled = max(2, total // 3)
    return scaled, min(hc_min_interviewer_turns, scaled)


def find_pivot_offset(
    transcript: str,
    cfg: dict | None = None,
    min_seconds: int | None = None,
) -> tuple[int, str] | None:
    """Return (char_offset, matched_pattern) of the pivot line, or None.

    Two-pass scan per format: first the standard `pivot.patterns` with the
    full intro-skip budget; if nothing matches, retry with
    `pivot.high_confidence_patterns` and a halved budget — those phrases
    never occur benignly, so opening up the early window is safe and catches
    follow-up demo calls that pitch within minutes.
    """
    if cfg is None:
        cfg = load_config()
    pivot_cfg = cfg["pivot"]
    interviewer_name = cfg["interviewer_name"]
    patterns = pivot_cfg["patterns"]
    hc_patterns = pivot_cfg["high_confidence_patterns"]
    negative_preambles = pivot_cfg["negative_preambles"]

    if min_seconds is None:
        min_seconds = pivot_cfg["min_seconds"]
    hc_min_seconds = min(pivot_cfg["high_confidence_min_seconds"], min_seconds)
    min_interviewer_turns = max(1, min_seconds // 60)
    hc_min_interviewer_turns = max(
        1,
        min(pivot_cfg["high_confidence_min_interviewer_turns"], min_interviewer_turns),
    )

    positions = [(m.group(1), m.start(), m.end()) for m in TIMESTAMP_RE.finditer(transcript)]
    if positions:
        blocks: list[tuple[int, int, int]] = []
        for i, (ts, start, end) in enumerate(positions):
            block_start = end
            block_end = positions[i + 1][1] if i + 1 < len(positions) else len(transcript)
            blocks.append((_seconds(ts), block_start, block_end))
        hit = _scan_timestamped(
            transcript, blocks, patterns, min_seconds, interviewer_name, negative_preambles
        )
        if hit is None:
            hit = _scan_timestamped(
                transcript,
                blocks,
                hc_patterns,
                hc_min_seconds,
                interviewer_name,
                negative_preambles,
            )
        return hit

    bracket_turns = list(BRACKET_SPEAKER_RE.finditer(transcript))
    if bracket_turns:
        b_min, b_hc = _scale_short_call_budget(
            bracket_turns, min_interviewer_turns, hc_min_interviewer_turns, interviewer_name
        )
        hit = _scan_bracket(
            transcript, bracket_turns, patterns, b_min, interviewer_name, negative_preambles
        )
        if hit is None:
            hit = _scan_bracket(
                transcript,
                bracket_turns,
                hc_patterns,
                b_hc,
                interviewer_name,
                negative_preambles,
            )
        return hit

    star_turns = list(SPEAKER_LINE_RE.finditer(transcript))
    if star_turns:
        s_min, s_hc = _scale_short_call_budget(
            star_turns, min_interviewer_turns, hc_min_interviewer_turns, interviewer_name
        )
        hit = _scan_star(
            transcript, star_turns, patterns, s_min, interviewer_name, negative_preambles
        )
        if hit is None:
            hit = _scan_star(
                transcript,
                star_turns,
                hc_patterns,
                s_hc,
                interviewer_name,
                negative_preambles,
            )
        return hit
    return None


def is_relevant_call(markdown: str, cfg: dict | None = None) -> bool:
    """Heuristic: does this call match the configured classifier?

    Returns True when classifier is disabled. Otherwise: at least one
    strong_keyword AND one supporting_keyword must appear in the first 8k
    chars (or the full doc when there's no Transcript header). False
    positives cost a spurious file; false negatives lose a call. Tune to
    favor recall.
    """
    if cfg is None:
        cfg = load_config()
    classifier = cfg["classifier"]
    if not classifier.get("enabled", True):
        return True
    if TRANSCRIPT_HEADER_RE.search(markdown):
        haystack = markdown[:8000].lower()
    else:
        haystack = markdown.lower()
    has_strong = any(k.lower() in haystack for k in classifier["strong_keywords"])
    has_support = any(k.lower() in haystack for k in classifier["supporting_keywords"])
    return has_strong and has_support


# Backwards-compat alias for any external callers.
is_product_leader_discovery = is_relevant_call


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def main() -> int:
    cfg = load_config()
    output_dir = Path(cfg["output_dir"])
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="JSON envelope or raw markdown file")
    ap.add_argument("slug", help="Output filename slug (no extension)")
    ap.add_argument(
        "--full-dir", default=str(output_dir / "full raw transcripts"),
    )
    ap.add_argument(
        "--unbiased-dir", default=str(output_dir / "unbiased raw transcripts"),
    )
    ap.add_argument(
        "--feedback-dir", default=str(output_dir / "feedback raw transcripts"),
    )
    ap.add_argument(
        "--min-seconds", type=int, default=cfg["pivot"]["min_seconds"]
    )
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--classify-only", action="store_true")
    args = ap.parse_args()

    md = load_input(args.input)
    relevant = is_relevant_call(md, cfg)
    transcript = extract_transcript(md)

    if transcript is None:
        print(json.dumps({
            "relevant": relevant,
            "has_transcript": False,
            "error": "no Transcript section found",
        }))
        return 0

    pivot = find_pivot_offset(transcript, cfg, min_seconds=args.min_seconds)
    unbiased = transcript if pivot is None else transcript[: pivot[0]].rstrip() + "\n"
    feedback = transcript[pivot[0] :] if pivot else None

    result = {
        "relevant": relevant,
        "has_transcript": True,
        "transcript_chars": len(transcript),
        "unbiased_chars": len(unbiased),
        "feedback_chars": len(feedback) if feedback is not None else None,
        "pivot_matched": pivot[1] if pivot else None,
        "pivot_offset": pivot[0] if pivot else None,
    }

    if args.classify_only:
        print(json.dumps(result, indent=2))
        return 0

    if not relevant and not args.force:
        print(json.dumps({**result, "skipped": "not relevant"}))
        return 0

    full_path = Path(args.full_dir) / f"{args.slug}.md"
    unbiased_path = Path(args.unbiased_dir) / f"{args.slug}.md"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    unbiased_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(transcript, encoding="utf-8")
    unbiased_path.write_text(unbiased, encoding="utf-8")
    result["full_path"] = str(full_path)
    result["unbiased_path"] = str(unbiased_path)

    if feedback is not None:
        feedback_path = Path(args.feedback_dir) / f"{args.slug}.md"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text(feedback, encoding="utf-8")
        result["feedback_path"] = str(feedback_path)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
