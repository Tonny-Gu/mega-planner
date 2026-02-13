#!/usr/bin/env python3
"""Mega-planner: 7-stage multi-agent debate pipeline.

Single-file executable. Prompts are read from the ``prompts/`` directory
next to this script.

Usage::

    python mega_planner.py "Add dark mode support"
    python mega_planner.py --override 42 "Custom feature description"
    python mega_planner.py -r 42 "1B,2A"
    python mega_planner.py -c 42              # continue from existing outputs
    python mega_planner.py --local "Plan without GitHub issues"
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from agentize.workflow.api import run_acw
from agentize.workflow.api import gh as gh_utils
from agentize.workflow.api.session import Session, StageResult

__version__ = "0.1.0"

# ============================================================
# Constants
# ============================================================

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

AGENT_PROMPTS = {
    "understander": "mega-understander.md",
    "bold": "mega-bold-proposer.md",
    "paranoia": "mega-paranoia-proposer.md",
    "critique": "mega-proposal-critique.md",
    "proposal-reducer": "mega-proposal-reducer.md",
    "code-reducer": "mega-code-reducer.md",
    "synthesizer": "mega-synthesizer.md",
    "resolver": "mega-resolver.md",
}

DEFAULT_BACKENDS = {
    "understander": ("claude", "sonnet"),
    "bold": ("claude", "opus"),
    "paranoia": ("claude", "opus"),
    "critique": ("claude", "opus"),
    "proposal-reducer": ("claude", "opus"),
    "code-reducer": ("claude", "opus"),
    "synthesizer": ("claude", "opus"),
    "resolver": ("claude", "opus"),
}

STAGE_TOOLS = {
    "understander": "Read,Grep,Glob",
    "bold": "Read,Grep,Glob,WebSearch,WebFetch",
    "paranoia": "Read,Grep,Glob,WebSearch,WebFetch",
    "critique": "Read,Grep,Glob,WebSearch,WebFetch",
    "proposal-reducer": "Read,Grep,Glob,WebSearch,WebFetch",
    "code-reducer": "Read,Grep,Glob,WebSearch,WebFetch",
    "synthesizer": "Read,Grep,Glob",
    "resolver": "Read,Grep,Glob",
}

STAGE_PERMISSION_MODE = {}


# ============================================================
# Prompt Rendering
# ============================================================


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from markdown content."""
    return _FRONTMATTER_RE.sub("", content, count=1)


def _read_agent_prompt(stage: str) -> str:
    """Read an agent prompt from the prompts directory."""
    filename = AGENT_PROMPTS[stage]
    raw = (_PROMPTS_DIR / filename).read_text(encoding="utf-8")
    return _strip_frontmatter(raw)


def _render_template(stage: str, replacements: dict[str, str]) -> str:
    """Read an agent prompt template and apply {{PLACEHOLDER}} replacements."""
    result = _read_agent_prompt(stage)
    for key, value in replacements.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def _build_debate_report(
    feature_name: str,
    bold_output: str,
    paranoia_output: str,
    critique_output: str,
    proposal_reducer_output: str,
    code_reducer_output: str,
) -> str:
    """Build the combined 5-agent debate report."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""# Multi-Agent Debate Report (Mega-Planner): {feature_name}

**Generated**: {timestamp}

This document combines five perspectives from the mega-planner dual-proposer debate system:
1. **Bold Proposer**: Innovative, SOTA-driven approach
2. **Paranoia Proposer**: Destructive refactoring approach
3. **Critique**: Feasibility analysis of both proposals
4. **Proposal Reducer**: Simplification of both proposals
5. **Code Reducer**: Code footprint analysis

---

## Part 1: Bold Proposer

{bold_output}

---

## Part 2: Paranoia Proposer

{paranoia_output}

---

## Part 3: Critique

{critique_output}

---

## Part 4: Proposal Reducer

{proposal_reducer_output}

---

## Part 5: Code Reducer

{code_reducer_output}

---
"""


def extract_feature_name(feature_desc: str, max_len: int = 80) -> str:
    """Extract a short feature name from description."""
    first_line = feature_desc.strip().split("\n")[0]
    normalized = " ".join(first_line.split())
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[:max_len]}..."


# ============================================================
# Pipeline Orchestration
# ============================================================


def run_mega_pipeline(
    feature_desc: str,
    *,
    output_dir: str | Path = ".tmp",
    backends: dict[str, tuple[str, str]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = run_acw,
    prefix: str | None = None,
    output_suffix: str = "-output.md",
    continue_mode: bool = False,
) -> dict[str, StageResult]:
    """Execute the 7-stage mega-planner pipeline.

    Runs all stages: understander → bold+paranoia → critique+reducers → synthesizer.

    If continue_mode is True, skip any stage whose output file
    already exists and is non-empty.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if prefix is None:
        prefix = datetime.now().strftime("%Y%m%d-%H%M%S")

    stage_backends = {**DEFAULT_BACKENDS}
    if backends:
        stage_backends.update(backends)

    session = Session(
        output_dir=output_path,
        prefix=prefix,
        runner=runner,
        output_suffix=output_suffix,
    )

    def _log(msg: str) -> None:
        session._log(msg)

    def _backend_label(stage: str) -> str:
        p, m = stage_backends[stage]
        return f"{p}:{m}"

    results: dict[str, StageResult] = {}

    def _should_skip(stage: str) -> bool:
        """In continue mode, check if stage output exists and is non-empty."""
        if not continue_mode:
            return False
        p = output_path / f"{prefix}-{stage}{output_suffix}"
        return p.exists() and p.stat().st_size > 0

    def _skip_result(stage: str) -> StageResult:
        """Create a StageResult for a skipped stage."""
        out = output_path / f"{prefix}-{stage}{output_suffix}"
        inp = output_path / f"{prefix}-{stage}-input.md"
        _log(f"  [continue] Skipping {stage} (output exists: {out})")
        return StageResult(
            stage=stage,
            input_path=inp,
            output_path=out,
            process=subprocess.CompletedProcess(args=[], returncode=0),
        )

    # --- Tier 1: Understander ---
    if _should_skip("understander"):
        results["understander"] = _skip_result("understander")
    else:
        _log(f"Stage 1/7: Running understander ({_backend_label('understander')})")
        understander_prompt = _render_template("understander", {
            "FEATURE_DESCRIPTION": feature_desc,
        })
        results["understander"] = session.run_prompt(
            "understander",
            understander_prompt,
            stage_backends["understander"],
            tools=STAGE_TOOLS.get("understander"),
            permission_mode=STAGE_PERMISSION_MODE.get("understander"),
        )
    understander_output = results["understander"].text()

    # --- Tier 2: Bold + Paranoia in parallel ---
    _log(
        f"Stage 2-3/7: Running bold + paranoia in parallel "
        f"({_backend_label('bold')}, {_backend_label('paranoia')})"
    )
    bold_prompt = _render_template("bold", {
        "FEATURE_DESCRIPTION": feature_desc,
        "UNDERSTANDER_OUTPUT": understander_output,
    })
    paranoia_prompt = _render_template("paranoia", {
        "FEATURE_DESCRIPTION": feature_desc,
        "UNDERSTANDER_OUTPUT": understander_output,
    })

    tier2_to_run = []
    for name, prompt in [("bold", bold_prompt), ("paranoia", paranoia_prompt)]:
        if _should_skip(name):
            results[name] = _skip_result(name)
        else:
            tier2_to_run.append(
                session.stage(name, prompt, stage_backends[name],
                              tools=STAGE_TOOLS.get(name),
                              permission_mode=STAGE_PERMISSION_MODE.get(name))
            )
    if tier2_to_run:
        results.update(session.run_parallel(tier2_to_run, max_workers=len(tier2_to_run)))
    bold_output = results["bold"].text()
    paranoia_output = results["paranoia"].text()

    # --- Tier 3: Critique + Proposal Reducer + Code Reducer in parallel ---
    _log(
        f"Stage 4-6/7: Running critique + reducers in parallel "
        f"({_backend_label('critique')}, {_backend_label('proposal-reducer')}, "
        f"{_backend_label('code-reducer')})"
    )
    dual_replacements = {
        "FEATURE_DESCRIPTION": feature_desc,
        "BOLD_PROPOSAL": bold_output,
        "PARANOIA_PROPOSAL": paranoia_output,
    }
    critique_prompt = _render_template("critique", dual_replacements)
    proposal_reducer_prompt = _render_template("proposal-reducer", dual_replacements)
    code_reducer_prompt = _render_template("code-reducer", dual_replacements)

    tier3_stages = [
        ("critique", critique_prompt),
        ("proposal-reducer", proposal_reducer_prompt),
        ("code-reducer", code_reducer_prompt),
    ]
    tier3_to_run = []
    for name, prompt in tier3_stages:
        if _should_skip(name):
            results[name] = _skip_result(name)
        else:
            tier3_to_run.append(
                session.stage(name, prompt, stage_backends[name],
                              tools=STAGE_TOOLS.get(name),
                              permission_mode=STAGE_PERMISSION_MODE.get(name))
            )
    if tier3_to_run:
        results.update(session.run_parallel(tier3_to_run, max_workers=len(tier3_to_run)))
    critique_output = results["critique"].text()
    proposal_reducer_output = results["proposal-reducer"].text()
    code_reducer_output = results["code-reducer"].text()

    # --- Tier 4: Synthesizer ---
    if _should_skip("synthesizer"):
        results["synthesizer"] = _skip_result("synthesizer")
    else:
        feature_name = extract_feature_name(feature_desc)
        debate_report = _build_debate_report(
            feature_name,
            bold_output, paranoia_output,
            critique_output, proposal_reducer_output, code_reducer_output,
        )

        # Save debate report
        debate_file = output_path / f"{prefix}-debate.md"
        debate_file.write_text(debate_report)

        def _write_synthesizer_prompt(path: Path) -> str:
            rendered = _render_template("synthesizer", {
                "FEATURE_NAME": feature_name,
                "FEATURE_DESCRIPTION": feature_desc,
                "COMBINED_REPORT": debate_report,
            })
            path.write_text(rendered, encoding="utf-8")
            return rendered

        _log(f"Stage 7/7: Running synthesizer ({_backend_label('synthesizer')})")
        results["synthesizer"] = session.run_prompt(
            "synthesizer",
            _write_synthesizer_prompt,
            stage_backends["synthesizer"],
            tools=STAGE_TOOLS.get("synthesizer"),
            permission_mode=STAGE_PERMISSION_MODE.get("synthesizer"),
        )

    return results


def run_resolve_pipeline(
    feature_desc: str,
    selections: str,
    *,
    output_dir: str | Path = ".tmp",
    backends: dict[str, tuple[str, str]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = run_acw,
    prefix: str,
    output_suffix: str = "-output.md",
    report_paths: dict[str, Path],
    synthesizer_path: Path,
) -> dict[str, StageResult]:
    """Execute the resolve pipeline.

    Loads existing debate stage outputs and the previous synthesizer plan,
    then runs the resolver agent to apply user selections.
    """
    _required = {"bold", "paranoia", "critique", "proposal-reducer", "code-reducer"}
    missing = _required - report_paths.keys()
    if missing:
        raise ValueError(f"report_paths missing required stages: {missing}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    stage_backends = {**DEFAULT_BACKENDS}
    if backends:
        stage_backends.update(backends)

    session = Session(
        output_dir=output_path,
        prefix=prefix,
        runner=runner,
        output_suffix=output_suffix,
    )

    def _log(msg: str) -> None:
        session._log(msg)

    feature_name = extract_feature_name(feature_desc)
    debate_report = _build_debate_report(
        feature_name,
        report_paths["bold"].read_text(),
        report_paths["paranoia"].read_text(),
        report_paths["critique"].read_text(),
        report_paths["proposal-reducer"].read_text(),
        report_paths["code-reducer"].read_text(),
    )

    # Save debate report
    debate_file = output_path / f"{prefix}-debate.md"
    debate_file.write_text(debate_report)

    prev_plan = synthesizer_path.read_text()

    def _write_resolver_prompt(path: Path) -> str:
        rendered = _render_template("resolver", {
            "FEATURE_NAME": feature_name,
            "FEATURE_DESCRIPTION": feature_desc,
            "USER_SELECTIONS": selections,
            "PREVIOUS_PLAN": prev_plan,
            "COMBINED_REPORT": debate_report,
        })
        path.write_text(rendered, encoding="utf-8")
        return rendered

    p, m = stage_backends["resolver"]
    _log(f"Running resolver ({p}:{m})")
    result = session.run_prompt(
        "resolver",
        _write_resolver_prompt,
        stage_backends["resolver"],
        tools=STAGE_TOOLS.get("resolver"),
        permission_mode=STAGE_PERMISSION_MODE.get("resolver"),
    )

    return {"resolver": result}


# ============================================================
# Plan Utilities
# ============================================================

_PLAN_HEADER_RE = re.compile(r"^#\s*(Implementation|Consensus) Plan:\s*(.+)$")
_PLAN_FOOTER_RE = re.compile(r"^Plan based on commit (?:[0-9a-f]+|unknown)$")


def _resolve_commit_hash() -> str:
    """Resolve the current git commit hash for provenance."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        if message:
            print(f"Warning: Failed to resolve git commit: {message}", file=sys.stderr)
        else:
            print("Warning: Failed to resolve git commit", file=sys.stderr)
        return "unknown"

    commit_hash = result.stdout.strip().lower()
    if not commit_hash or not re.fullmatch(r"[0-9a-f]+", commit_hash):
        print("Warning: Unable to parse git commit hash, using 'unknown'", file=sys.stderr)
        return "unknown"
    return commit_hash


def _append_plan_footer(path: Path, commit_hash: str) -> None:
    """Append the commit provenance footer to a plan file."""
    footer_line = f"Plan based on commit {commit_hash}"
    try:
        content = path.read_text()
    except FileNotFoundError:
        print(f"Warning: Plan file missing, cannot append footer: {path}", file=sys.stderr)
        return
    trimmed = content.rstrip("\n")
    if trimmed.endswith(footer_line):
        return
    with path.open("a") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(f"{footer_line}\n")


def _strip_plan_footer(text: str) -> str:
    """Strip the trailing commit provenance footer from a plan body."""
    if not text:
        return text
    lines = text.splitlines()
    had_trailing_newline = text.endswith("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    if not _PLAN_FOOTER_RE.match(lines[-1].strip()):
        return text
    lines.pop()
    result = "\n".join(lines)
    if had_trailing_newline and result:
        result += "\n"
    return result


def _shorten_feature_desc(desc: str, max_len: int = 50) -> str:
    """Truncate feature description for use in titles."""
    return extract_feature_name(desc, max_len=max_len)


def _extract_plan_title(plan_path: Path) -> str:
    """Extract plan title from synthesizer/resolver output file."""
    try:
        for line in plan_path.read_text().splitlines():
            match = _PLAN_HEADER_RE.match(line.strip())
            if match:
                return match.group(2).strip()
    except FileNotFoundError:
        return ""
    return ""


def _apply_issue_tag(plan_title: str, issue_number: str) -> str:
    """Prepend issue tag to plan title."""
    issue_tag = f"[#{issue_number}]"
    if plan_title.startswith(issue_tag):
        return plan_title
    if plan_title.startswith(f"{issue_tag} "):
        return plan_title
    if plan_title:
        return f"{issue_tag} {plan_title}"
    return issue_tag


# ============================================================
# CLI Main
# ============================================================


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Positional args serve as feature desc or selections."""
    parser = argparse.ArgumentParser(description="Mega-planner 7-stage pipeline")
    parser.add_argument("words", nargs="*", default=[], help="Feature description or selections")
    parser.add_argument("--override", default="", metavar="ISSUE", help="Plan for existing issue using positional args as description")
    parser.add_argument("-r", "--resolve", default="", metavar="ISSUE", help="Resolve disagreements in issue")
    parser.add_argument("-c", "--continue", dest="continue_issue", default="", metavar="ISSUE", help="Continue pipeline, skipping stages with existing output")
    parser.add_argument("--output-dir", default=".tmp")
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--local", action="store_true", help="Disable GitHub issue creation")
    args = parser.parse_args(argv)

    positional = " ".join(args.words)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    issue_mode = not args.local

    issue_number: str | None = None
    issue_url: str | None = None
    feature_desc = ""
    prefix: str

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    # --- Resolve mode ---
    if args.resolve:
        issue_number = args.resolve
        prefix = f"issue-{issue_number}"
        report_paths = {}
        for stage in ["bold", "paranoia", "critique", "proposal-reducer", "code-reducer"]:
            p = output_dir / f"{prefix}-{stage}-output.md"
            if not p.exists():
                _log(f"Error: Report file not found: {p}")
                return 1
            report_paths[stage] = p

        synthesizer_path = output_dir / f"{prefix}-synthesizer-output.md"

        feature_desc = gh_utils.issue_body(issue_number)
        feature_desc = _strip_plan_footer(feature_desc)

    # --- Continue mode ---
    elif args.continue_issue:
        issue_number = args.continue_issue
        prefix = f"issue-{issue_number}"
        feature_desc = gh_utils.issue_body(issue_number)
        feature_desc = _strip_plan_footer(feature_desc)

    # --- Override mode ---
    elif args.override:
        if not positional:
            _log("Error: feature description required (pass as positional argument)")
            return 1
        issue_number = args.override
        issue_url = gh_utils.issue_url(issue_number)
        prefix = f"issue-{issue_number}"
        feature_desc = positional

    # --- Default mode ---
    else:
        if not positional:
            _log("Error: feature description required (pass as positional argument)")
            return 1
        feature_desc = positional
        prefix = args.prefix or datetime.now().strftime("%Y%m%d-%H%M%S")
        if issue_mode:
            short_desc = _shorten_feature_desc(feature_desc, max_len=50)
            issue_number, issue_url = gh_utils.issue_create(
                f"[plan] placeholder: {short_desc}",
                feature_desc,
            )
            if not issue_number:
                _log(f"Warning: Could not parse issue number from URL: {issue_url}")
            if issue_number:
                prefix = f"issue-{issue_number}"
                _log(f"Created placeholder issue #{issue_number}")
            else:
                _log("Warning: Issue creation failed, falling back to timestamp artifacts")

    _log(f"Feature: {extract_feature_name(feature_desc)}")
    _log(f"Artifacts prefix: {prefix}")

    if args.resolve:
        _log("Starting resolve pipeline...")
        results = run_resolve_pipeline(
            feature_desc,
            positional,
            output_dir=output_dir,
            prefix=prefix,
            report_paths=report_paths,
            synthesizer_path=synthesizer_path,
        )
    else:
        _log("Starting mega-planner 7-stage debate pipeline...")
        results = run_mega_pipeline(
            feature_desc,
            output_dir=output_dir,
            prefix=prefix,
            continue_mode=bool(args.continue_issue),
        )

    plan_result = results.get("resolver") or results.get("synthesizer")
    if plan_result:
        commit_hash = _resolve_commit_hash()
        _append_plan_footer(plan_result.output_path, commit_hash)

        if issue_mode and issue_number:
            _log(f"Publishing plan to issue #{issue_number}...")
            plan_title = _extract_plan_title(plan_result.output_path)
            if not plan_title:
                plan_title = _shorten_feature_desc(feature_desc, max_len=50)
            plan_title = _apply_issue_tag(plan_title, issue_number)
            gh_utils.issue_edit(
                issue_number,
                title=f"[plan] {plan_title}",
                body_file=plan_result.output_path,
            )
            gh_utils.label_add(issue_number, ["agentize:plan"])
            if issue_url:
                _log(f"See the full plan at: {issue_url}")

        _log(f"See the full plan locally at: {plan_result.output_path}")
        print(str(plan_result.output_path))

    _log("Pipeline complete!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
