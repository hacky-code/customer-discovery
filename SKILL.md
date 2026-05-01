---
name: customer-discovery
description: Two-mode skill for working with customer-discovery / user-interview transcripts. (1) `/customer-discovery update` harvests new Gemini meeting-notes from Google Drive, classifies, and splits each call into full / unbiased pre-pitch / post-pitch feedback transcripts. (2) `/customer-discovery <question>` answers ad-hoc questions of the existing corpus with cited quotes. Invoke when the user wants to refresh transcripts, ask "what have CPOs said about X", find pitch reactions, ingest a new meeting-notes doc, or run a corpus query.
---

# customer-discovery

Two modes for working with discovery-call transcripts:

- **`/customer-discovery update`** — harvest new calls from your Drive folders, classify, and split each into three artefacts: the full transcript, the unbiased pre-pivot transcript (genuine discovery), and the post-pivot feedback transcript (reactions to your pitch).
- **`/customer-discovery <question>`** — answer ad-hoc questions about your discovery corpus with cited quotes from specific calls. Read-only; no infrastructure beyond `grep` + a subagent.

## Mode dispatch

When the skill is invoked, look at the user's input after `/customer-discovery`:

- **Empty** (just `/customer-discovery` with no further text): ask the user "Update transcripts (run the harvest pipeline) or ask a question of the existing corpus?"
- **Starts with `update`** (case-insensitive, with or without trailing words): run §Mode: update.
- **Anything else**: treat the full input as a question; run §Mode: query.

Both modes operate on the corpus stored under `<output_dir>/` (default `discovery/`).

## Credentials

The harvest mode (`update`) calls `mcp__Google-Drive__read_file_content` to fetch your meeting notes. **Your Google OAuth credential lives entirely inside the Drive MCP server's own config — this skill never sees it, and there are no API keys or tokens stored in this repo.** The query mode is read-only against local transcript files; no credentials involved.

## First-time setup

Five steps. Do these once before invoking `update`. Query mode works as soon as the corpus has at least one transcript in it.

### 1. Connect the Google Drive MCP server

If you don't already have the Drive MCP installed: see `https://docs.claude.com/en/docs/claude-code/mcp` for the Claude Code MCP setup flow, then connect Google Drive via OAuth.

Verify with `/mcp` in Claude Code — you should see a `Google-Drive` (or similarly named) connector listed.

### 2. Set up your Drive folders

The skill reads from one or more Drive folders. Two folder *scopes* are supported:

- **`primary`** — a dedicated folder where every file is a candidate transcript (e.g. you have a "Customer Discovery" folder you drop calls into). Full listing every run; deduped against your local LOG.
- **`secondary`** — a mixed folder (e.g. all Google Meet recordings) where you filter by `createdTime > <last run>` to avoid scanning history.

You need at least one folder. To find a folder ID: open it in Drive, copy the `folders/<ID>` portion of the URL.

### 3. Edit `config.json`

Open `<repo>/discovery/tooling/config.json` and update at minimum:

- `interviewer_name` — your first name (or the shortest unambiguous prefix that matches every spelling Gemini gives you in transcripts).
- `drive_folders` — your folder IDs and scopes.
- `output_dir` — where transcripts and `LOG.md` should be written. Default `discovery` (relative to repo root).

The file has inline `_*_help` keys explaining every option; the loader strips them before use.

### 4. (Optional) Tune pivot patterns to your verbal style

The pivot detector matches phrases YOU use when you transition from asking discovery questions to pitching your own idea ("let me share what I've heard from other CPOs", "I'm going to switch to a demo", etc).

Defaults are calibrated to one user. They'll catch the obvious cases for most people, but you'll get cleaner cuts if you spend ~10 minutes editing `pivot.patterns` in `config.json` to match your actual phrasings. Run the skill on 5-10 of your historical calls, see where the cut lands, and adjust.

`pivot.high_confidence_patterns` are phrases that NEVER occur benignly mid-discovery (e.g. demo openers — "let me show you the product I built"). They bypass the standard intro-skip budget so a follow-up call where you demo within 5 minutes still gets cleanly cut.

`pivot.negative_preambles` invalidates a pivot hit if the same sentence earlier contains a "telegraph" phrase ("one last question and I'll switch to insights" → still a discovery question, not a pivot).

### 5. (Optional) Tune the classifier

The classifier filters out non-discovery calls from your secondary folders (status updates, investor chats, internal syncs). A call passes if at least one `strong_keyword` AND one `supporting_keyword` appear in the first 8k chars.

- Defaults are tuned for product-leader discovery. Re-tune for your audience (founders, eng leaders, designers, marketers, etc).
- Set `classifier.enabled: false` to skip filtering entirely — every transcript with a discoverable speaker structure gets processed.

### Done

Run `/customer-discovery update` to harvest your first batch. Once you have transcripts, you can also start asking questions: `/customer-discovery what pain points have CPOs mentioned around competitive intel?`

---

## Mode: update

When invoked as `/customer-discovery update` (or just `/customer-discovery update <noise>` — the noise is ignored):

- Derive state from `<output_dir>/LOG.md`: processed fileIds = existing rows; secondary-folder cutoff = `max(createdTime)` in the LOG. First run (no LOG): everything in scope, capped by `first_run_lookback_days` for secondary folders only.
- Delegate every `read_file_content` batch to a subagent (see Token hygiene below).
- After `process_batch.py`, **append** new rows to `LOG.md` — do not rewrite existing ones.
- If a call is misclassified or a pivot is missed, update `config.json` in the same commit and note the example in this SKILL if the pattern generalises.
- Commit and push everything (config tweaks, new transcripts, LOG update) to the current branch.

### Step 1 — Load known fileIds + last-run cutoff

```bash
grep -oE '[A-Za-z0-9_-]{30,}' <output_dir>/LOG.md | sort -u
```

### Step 2 — Scan secondary folders (createdTime > cutoff)

Use the Drive MCP `search_files` tool. Query operators are strict: `parentId =`, `title contains`, `createdTime > 'RFC3339Z'`. NOT `parents`, `parent`, or `name` — those are silently unsupported.

```
parentId = '<folder-id>' and createdTime > '<cutoff>T00:00:00.00Z'
```

The ISO date MUST end in `Z` (UTC). `2026-04-20T00:00:00` alone is rejected.

### Step 3 — Scan primary folders (full listing, dedupe against LOG)

```
parentId = '<folder-id>'
```

`pageSize: 50` covers most folders; paginate if it grows.

### Step 4 — Fetch + persist each new file

For each new fileId, call `mcp__Google-Drive__read_file_content`. The MCP persists results to `~/.claude/projects/<project-slug>/<session>/tool-results/*.txt` under one of two filename patterns:
- `toolu_<id>.txt` — normal
- `mcp-Google-Drive-read_file_content-<ts>.txt` — when the result overflows the assistant-message token budget

Both hold the same JSON envelope: `{"fileContent": "<markdown>"}`.

**Inline-result gotcha:** when the subagent's tool result is small enough to fit in the assistant-message budget, the MCP sometimes does *not* persist a separate `*.txt` — the JSON envelope is embedded directly in the subagent's `<session>/subagents/agent-*.jsonl` stream as the `tool_result.content` string. `harvest_dumps.py` scans both sources (dump files AND subagent jsonls) so either path works; don't assume a missing `*.txt` means the read failed.

**Delegate the batch to a subagent** so the large fileContents never enter the main context. The subagent only fires the tool calls; the disk is the handoff.

Once the batch completes, harvest:

```bash
python3 <output_dir>/tooling/harvest_dumps.py
```

This scans `~/.claude/projects/**/tool-results/*.txt` AND `~/.claude/projects/**/subagents/*.jsonl` and writes every new dump to `<output_dir>/tooling/raw/<fileId>.json`. The canonical fileId comes from pairing each `tool_result` to its originating `tool_use.input.fileId` via `tool_use_id` (built once by walking every jsonl under `~/.claude/projects/`). Idempotent — safe to re-run. Paths derive from `Path.home()` so it works cross-platform (Linux and Windows).

### Step 5 — Classify + extract + write

Build a TSV manifest (`<output_dir>/tooling/manifest.tsv`) with one row per new fileId:

```
fileId<TAB>slug<TAB>dumpPath<TAB>createdTime<TAB>title
```

Slug format: `<YYYY-MM-DD>-<primary-speaker>` (lowercase, hyphens — date leads so filenames sort chronologically). Pipe the manifest into the batch processor:

```bash
python3 <output_dir>/tooling/process_batch.py < <output_dir>/tooling/manifest.tsv > /tmp/process-out.json
```

For each row it loads the JSON envelope, runs the classifier, extracts the transcript, finds the pivot, and writes the three artefacts. Append every manifest row + classification + pivot info to `<output_dir>/LOG.md` (do NOT rewrite existing rows).

### Transcript formats

Gemini emits three shapes; `extract_transcript` + `find_pivot_offset` handle all three:

1. **Headered + timestamped** — `# <emoji> Transcript` header, `### HH:MM:SS` blocks, `**Speaker:** text` lines. Most common.
2. **Bracket-speaker plain markdown** — no header, no timestamps. Each turn is `\[Speaker Name\]` on its own line with the utterance below. Detected via `BRACKET_SPEAKER_RE`.
3. **Inline speaker plain markdown** — no header, no timestamps, no brackets. Speaker labels are inline `**Name:** text` lines. Detected via `SPEAKER_LINE_RE` with a ≥10-match threshold.

For formats 2 and 3 the classifier haystack expands to the full doc (keywords often land deep, not up top).

### Speaker-pivot detection

`parse_transcript.py::find_pivot_offset` scans the interviewer's utterances (matched by case-insensitive substring against `interviewer_name`) and tests each against `pivot.patterns`.

Intro-skipping heuristic:
- **Format 1 (timestamps present):** skip blocks before `pivot.min_seconds` (default 600s).
- **Formats 2 & 3 (no timestamps):** count interviewer turns and skip the first `min_seconds // 60` — budgets roughly one minute per interviewer turn.

Other rules:

- Patterns that overlap with benign mid-discovery transitions ("let me switch gears") must be paired with an explicit reference to insights / themes / patterns / heard / other folks. Plain "switch gears" alone is NOT a pivot.
- The "switch to ... insights/I've heard" pattern tolerates ~25 chars of noise to handle Gemini mis-transcriptions and filler words.
- **High-confidence patterns** bypass the standard intro-skip budget. Phrases like "switch to a demo" / "show you the product I built" never occur benignly, so they fire after `high_confidence_min_seconds` (300s) instead of 600s. Catches follow-up demo calls.
- **Short-call turn-budget scaling.** When a call has fewer interviewer turns than the default, `_scale_short_call_budget` clamps the budget to `max(2, total_interviewer_turns // 3)`.
- **Speaker-attribution glitches block detection.** The detector only scans interviewer-attributed turns. When Gemini misattributes a pivot to the interviewee, the call falls back to "no pivot detected; unbiased == full". Just log it in `LOG.md`; do NOT add fuzzy-attribution heuristics (too risky).
- **Preamble-telegraph guard.** Interviewers often announce the pivot a question or two early. `pivot.negative_preambles` disqualifies any pivot hit whose same SENTENCE earlier contains a telegraph phrase. The guard is sentence-scoped (not utterance-scoped) — interviewers occasionally telegraph and then immediately reverse inside one utterance, and the second sentence is a real pivot.
- The cut point is the start of the interviewer's pivot line, not the timestamp block header — preserves the interviewee's final unbiased answer.
- No match ⇒ `unbiased == full` with a warning. Spot-check; a non-trivial fraction of calls genuinely have no pivot.

### Classifier tuning

`is_relevant_call` is intentionally permissive (recall > precision). False positives cost a spurious transcript file; false negatives lose a call from the analysis. When a genuine call is rejected, add the missing keyword to `strong_keywords` or `supporting_keywords` in `config.json`. When a clearly-off-topic call slips through, log it but don't tighten the classifier unless the pattern recurs.

Non-transcript docs (templates, meta-notes, agendas) have no `# Transcript` heading and will be skipped automatically with `has_transcript: false`.

### Token hygiene

A full `read_file_content` result can be 30-60 KB (~10k tokens) of markdown. Fetching 30 files inline would consume the working context. Always delegate batched fetches to a subagent (Agent tool, `general-purpose`) with instructions to:

1. Load the tool schema via `ToolSearch(query="select:mcp__Google-Drive__read_file_content")`.
2. Fire the fileIds in parallel batches of ~6 per assistant turn.
3. Never read, summarize, or quote the returned fileContent.
4. End by running `harvest_dumps.py` and reporting only the JSON stdout + success/fail counts.

The main agent then picks up the persisted JSON envelopes from `<output_dir>/tooling/raw/` — the content never enters the main context.

---

## Mode: query

When invoked as `/customer-discovery <anything-not-update>`, treat the args as a question about the discovery corpus and produce a synthesis with citations.

### Scope routing

Default scope is auto-decided from the question wording:

- Words like "pain", "frustration", "challenge", "problem", "need", "looking for", "wish", "struggle", "pain point" → **unbiased only** (pre-pitch — what they said before being led).
- Words like "react", "respond", "feedback", "objection", "interested", "skeptical", "say to my pitch", "say about my [product/idea/pitch/demo]" → **feedback only** (post-pivot reactions).
- Otherwise → **both folders**.

Honor explicit override flags anywhere in the question (strip the flag from the question before processing):
- `--pain` forces unbiased only
- `--pitch` forces feedback only
- `--both` forces both

### Workflow

1. **Read `<output_dir>/LOG.md`** so you have call titles + dates + the list of available slugs as ranking context. (LOG is small; main-context-safe.)

2. **Extract 3-8 keyword candidates** from the question. Prefer noun phrases and concrete terms over filler. For "what pain points have CPOs mentioned around competitive intel?", keywords ≈ `["competitive intelligence", "competitor", "competition", "pain", "frustrat"]`.

3. **Delegate to a `general-purpose` subagent** (token hygiene — full transcripts must never enter main context). Brief: question, scope (one or both folder names), keyword list, repo root path, and these instructions:
   - `cd` to the repo root.
   - For each keyword, run `grep -ric '<keyword>' '<output_dir>/<scope-folder>'` to count matches per file. Combine across keywords for a per-transcript score.
   - Read the top 5-8 transcripts in full with the `Read` tool. Each is ~10-20k chars — well within a subagent's budget at this fan-out.
   - Synthesize a 2-4 paragraph answer that quotes specific calls. Each quote MUST be wrapped in quotation marks and followed by a markdown link `[<slug>](<output_dir>/<scope-folder>/<slug>.md)`.
   - List 2-3 nearby calls that didn't quite match but are worth a manual look (just slugs + one-line "why" each).
   - Return ONLY the synthesis + the link list. Do NOT include raw transcript content beyond the in-prose quotes.

4. **Show the subagent's output to the user verbatim.** Do NOT re-read the cited transcripts in main context — trust the subagent's synthesis. If the user wants to dig further, they can `Read` a specific slug directly.

5. **No matches:** report "No transcripts found mentioning [keywords]. Try broader keywords, `--both` to widen the scope, or `/customer-discovery update` to ensure the latest calls have been ingested."

### Examples

```
/customer-discovery what pain points have CPOs mentioned around competitive intelligence?
→ scope: unbiased; keywords: competitive intelligence, competitor, competition, pain, frustrat

/customer-discovery how did people react to the agent-based discovery insights pitch?
→ scope: feedback; keywords: agent, insight, discovery, react, interest, skeptical

/customer-discovery --both has anyone mentioned PMM tooling?
→ scope: both; keywords: PMM, marketing, positioning, messaging
```

### Why subagent-only

A full unbiased transcript is 10-20k chars (~5k tokens). Reading 6-8 of them in main context = 30-40k tokens of raw transcript. The synthesis is the productive output; the raw text is throwaway. Delegating keeps the main thread clean.

If the question genuinely needs close reading of one specific call, look up the slug in `LOG.md` and `Read` that file directly in main context — that's a targeted operation, not a corpus search.

---

## File layout

```
<output_dir>/
├── LOG.md                              # fileId → classification + output paths (also: corpus index for query mode)
├── full raw transcripts/<slug>.md      # Transcript section, full
├── unbiased raw transcripts/<slug>.md  # Transcript truncated at the pivot
├── feedback raw transcripts/<slug>.md  # Transcript from the pivot onward (only when detected)
└── tooling/
    ├── config.json                     # all skill configuration (folders, name, patterns, classifier)
    ├── config.py                       # config loader (used by the three scripts)
    ├── parse_transcript.py             # single-file CLI: classify + extract + write
    ├── process_batch.py                # TSV stdin → JSON stdout + writes
    ├── harvest_dumps.py                # MCP dump directory → raw/<fileId>.json
    ├── manifest.tsv                    # per-run input to process_batch.py
    └── raw/<fileId>.json               # stable copies of MCP read_file_content dumps
```

`raw/` and `__pycache__/` are gitignored.

## Editing the tooling

- **Pivot patterns / classifier keywords / output paths** all live in `config.json`. Don't hardcode them in the scripts.
- When adding a pivot pattern, verify it does NOT match benign discovery-mode phrases. Require pairing with an explicit insights/heard/themes anchor.
- `TRANSCRIPT_HEADER_RE` (in `parse_transcript.py`) tolerates Gemini emoji variance — don't anchor it to a specific glyph.
- `harvest_dumps.py` has two scanners (`_scan_dump_files` and `_scan_subagent_jsonls`) — they read the same envelope shape but from different sources. The jsonl scanner's per-line filter is a plain `"fileContent" in line` substring check (not `"\"fileContent\""`) because in the jsonl the envelope is JSON-inside-JSON and the quotes arrive escaped as `\"fileContent\"`.
- All scripts are pure-Python, stdlib only.

## Pro-tip: schedule a daily refresh

Once setup is done and the harvest is producing clean output, schedule it to run on its own. Two options:

**Option A — Anthropic Cowork routines (recommended).** If you have the `schedule` skill installed:

```
/schedule customer-discovery update
```

…and follow the prompts to set a cron expression (e.g. `0 8 * * *` for 8 AM daily). The routine runs as a remote Claude Code agent, executes `/customer-discovery update`, and commits the results to your branch.

**Option B — local cron.** Add a crontab entry:

```cron
0 8 * * * cd /path/to/repo && claude code --message "/customer-discovery update" >> /tmp/customer-discovery.log 2>&1
```

This requires Claude Code installed locally and your Drive MCP credential cached. The remote routine option is simpler if available.

Either way: discovery transcripts ingest themselves overnight, and you wake up to a fresh `LOG.md` ready for `/customer-discovery <question>` queries.
