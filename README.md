# customer-discovery

A Claude Code skill for working with customer-discovery / user-interview transcripts. Two modes:

- **`/customer-discovery update`** — harvests new Gemini-generated meeting notes from your Google Drive, classifies them, and splits each call into three artefacts: full transcript, unbiased pre-pivot transcript (genuine discovery), and post-pitch feedback transcript (reactions to your pitch).
- **`/customer-discovery <question>`** — answers ad-hoc questions of the corpus with cited quotes. No infrastructure beyond `grep` + a subagent.

The pre-pitch / post-pitch split matters: insight extraction shouldn't train on the biased half, but pitch-response analysis needs only the biased half.

## Prerequisites

- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed.
- [Google Drive MCP server](https://docs.claude.com/en/docs/claude-code/mcp) connected and authenticated. Verify with `/mcp` in Claude Code.
- Python 3 (stdlib only — the tooling has zero pip dependencies).

Your Google OAuth credential lives entirely inside the Drive MCP server's own config. **No API keys or tokens are stored in this skill.** The skill is read-only against your Drive.

## Install

Clone this repo and copy the files into the repository where you want your discovery transcripts to live:

```bash
git clone https://github.com/<you>/customer-discovery /tmp/cd

cd /path/to/your/repo
mkdir -p .claude/skills/customer-discovery discovery/tooling

cp /tmp/cd/SKILL.md .claude/skills/customer-discovery/
cp /tmp/cd/tooling/*.py discovery/tooling/
cp /tmp/cd/tooling/config.example.json discovery/tooling/config.json
```

Now run `/customer-discovery` in Claude Code. On first invocation the skill notices the unconfigured state, asks you for your first name and a Drive folder URL, and writes the real config for you. **You won't need to touch any JSON.** Once setup wraps, it kicks off the harvest.

## Usage

Bootstrap your corpus:

```
/customer-discovery update
```

Ask questions:

```
/customer-discovery what pain points have CPOs mentioned around competitive intel?
/customer-discovery how did people react to my pitch about agentic discovery insights?
/customer-discovery --both has anyone mentioned PMM tooling?
```

Scope auto-routes from the question (pain words → unbiased; reaction words → feedback; otherwise both), with `--pain` / `--pitch` / `--both` overrides.

## What gets stored locally

```
discovery/                              # name configurable via output_dir
├── LOG.md                              # one row per call: classification, pivot offset, paths
├── full raw transcripts/<slug>.md      # full transcript
├── unbiased raw transcripts/<slug>.md  # pre-pitch portion
├── feedback raw transcripts/<slug>.md  # post-pitch portion (only when a pivot is detected)
└── tooling/                            # config + scripts (this repo)
    ├── config.json                     # your edited config (gitignored if you copy from .example)
    ├── config.py                       # config loader
    ├── parse_transcript.py             # classify + extract + split
    ├── process_batch.py                # batch driver
    └── harvest_dumps.py                # MCP dump → raw/<fileId>.json
```

## Schedule a daily refresh (optional)

If you have the [Anthropic Cowork `schedule` skill](https://github.com/anthropics/skills) installed:

```
/schedule customer-discovery update
```

…and pick a cron expression. The routine runs as a remote Claude Code agent, executes `/customer-discovery update`, and commits the results to your branch. Wake up to a fresh `LOG.md`.

## Tuning to your voice

The defaults are calibrated to one specific interviewer's verbal tics ("let me share what I've heard from other CPOs", "I'm gonna switch to a demo", etc). They'll catch the obvious cases out-of-the-box, but you'll get cleaner pivot cuts after a few runs.

If you notice the pivot cutting too early or too late on your calls, just tell the skill: "the pivot is cutting too early on my Brad calls" or "it's missing my demo opener phrasing". The skill will adjust `pivot.patterns` and rewrite the config for you — no regex editing required.

Same for the classifier ("it's labelling internal syncs as discovery — tighten up").

## License

MIT — see [LICENSE](./LICENSE).
