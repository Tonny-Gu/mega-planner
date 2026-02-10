# Mega-Planner

7-stage multi-agent debate pipeline for implementation planning. Two opposing proposers (bold + paranoia) generate competing approaches, three reviewers (critique, proposal reducer, code reducer) analyze them in parallel, and a final consensus stage synthesizes everything into a single implementation plan.

## Prerequisites

- Python 3.10+
- [`agentize`](https://github.com/anthropics/agentize) installed in the environment

## Usage

```bash
# Plan from a free-text description (creates a GitHub issue automatically)
python mega_planner.py "Add dark mode support"

# Plan from an existing GitHub issue
python mega_planner.py -f 42

# Resolve disagreements in a previous debate (re-runs consensus only)
python mega_planner.py -r 42 "1B,2A"

# Local-only mode (no GitHub issue creation)
python mega_planner.py --local "Plan without GitHub issues"
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `-f`, `--from` | Plan from an existing GitHub issue number |
| `-r`, `--resolve` | Re-run consensus for an existing debate, applying new selections |
| `--local` | Skip GitHub issue creation/update |
| `--output-dir` | Artifact output directory (default: `.tmp`) |
| `--prefix` | Custom artifact filename prefix |

## Pipeline Architecture

The pipeline executes in 4 tiers with parallel stages where possible:

```
Tier 1:  [Understander]
              |
Tier 2:  [Bold] + [Paranoia]        (parallel)
              |
Tier 3:  [Critique] + [Proposal Reducer] + [Code Reducer]  (parallel)
              |
Tier 4:  [Consensus]
```

| Stage | Model | Description |
|-------|-------|-------------|
| Understander | Sonnet | Gathers codebase context, constraints, and architectural patterns |
| Bold Proposer | Opus | Innovative, SOTA-driven approach with code diff drafts |
| Paranoia Proposer | Opus | Destructive refactoring approach — deletes aggressively, rewrites for simplicity |
| Critique | Opus | Feasibility analysis validating assumptions of both proposals |
| Proposal Reducer | Opus | Simplifies both proposals following "less is more" philosophy |
| Code Reducer | Opus | Analyzes code footprint to limit unreasonable growth |
| Consensus | Opus | Synthesizes all 5 perspectives into a final implementation plan |

## Project Structure

```
mega_planner.py          # Single-file pipeline orchestrator + CLI
prompts/                 # Agent prompt templates (markdown)
  understander.md
  mega-bold-proposer.md
  mega-paranoia-proposer.md
  mega-proposal-critique.md
  mega-proposal-reducer.md
  mega-code-reducer.md
  external-synthesize-prompt.md
tests/
  test_pipeline.py       # Pipeline tests with stub runner (no LLM calls)
docs/
  git-msg-tags.md        # Commit message tag conventions
.tmp/                    # Generated artifacts (gitignored)
```

## Artifacts

Each run produces intermediate files in the output directory (`.tmp/` by default):

```
{prefix}-understander-input.md / output.md
{prefix}-bold-input.md / output.md
{prefix}-paranoia-input.md / output.md
{prefix}-critique-input.md / output.md
{prefix}-proposal-reducer-input.md / output.md
{prefix}-code-reducer-input.md / output.md
{prefix}-debate.md              # Combined 5-agent debate report
{prefix}-consensus-input.md / output.md   # Final plan
```

When using `-f` or default mode, the prefix is `issue-{N}`. In local mode without `--prefix`, it defaults to a timestamp.

## Development

```bash
pytest
```
