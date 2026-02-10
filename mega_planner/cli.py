"""CLI entry point for mega-planner."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agentize.workflow.api import gh as gh_utils

from mega_planner.pipeline import (
    run_mega_pipeline,
    _extract_feature_name,
)

# ============================================================
# Plan Utilities
# ============================================================

_PLAN_HEADER_RE = re.compile(r"^#\s*(Implementation|Consensus) Plan:\s*(.+)$")
_PLAN_HEADER_HINT_RE = re.compile(r"(Implementation Plan:|Consensus Plan:)", re.IGNORECASE)
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
    """Append the commit provenance footer to a consensus plan file."""
    footer_line = f"Plan based on commit {commit_hash}"
    try:
        content = path.read_text()
    except FileNotFoundError:
        print(f"Warning: Consensus plan missing, cannot append footer: {path}", file=sys.stderr)
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
    normalized = " ".join(desc.split())
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[:max_len]}..."


def _extract_plan_title(consensus_path: Path) -> str:
    """Extract plan title from consensus output file."""
    try:
        for line in consensus_path.read_text().splitlines():
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
    """Main CLI entry point with 4 modes: default, from-issue, refine-issue, resolve-issue."""
    parser = argparse.ArgumentParser(description="Mega-planner 7-stage pipeline")
    parser.add_argument("--feature-desc", default="", help="Feature description")
    parser.add_argument("--from-issue", default="", help="Plan from existing issue number")
    parser.add_argument("--refine-issue", default="", help="Refine existing plan issue")
    parser.add_argument("--resolve-issue", default="", help="Resolve disagreements in issue")
    parser.add_argument("--selections", default="", help="Option selections for resolve mode (e.g. 1B,2A)")
    parser.add_argument("--output-dir", default=".tmp")
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-consensus", action="store_true")
    parser.add_argument("--issue-mode", default="true", choices=["true", "false"])
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    issue_mode = args.issue_mode == "true"

    issue_number: str | None = None
    issue_url: str | None = None
    feature_desc = args.feature_desc
    report_paths = None
    consensus_path = None
    history_path = None
    prefix: str

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    def _log_verbose(msg: str) -> None:
        if args.verbose:
            _log(msg)

    # --- Resolve mode ---
    if args.resolve_issue:
        issue_number = args.resolve_issue
        prefix = f"issue-{issue_number}"
        report_paths = {}
        for stage in ["bold", "paranoia", "critique", "proposal-reducer", "code-reducer"]:
            p = output_dir / f"{prefix}-{stage}-output.md"
            if not p.exists():
                _log(f"Error: Report file not found: {p}")
                return 1
            report_paths[stage] = p

        consensus_path = output_dir / f"{prefix}-consensus-output.md"
        history_path = output_dir / f"{prefix}-history.md"
        if not history_path.exists():
            history_path.write_text(
                "# Selection & Refine History\n\n"
                "| Timestamp | Type | Content |\n"
                "|-----------|------|---------|\n"
            )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with history_path.open("a") as f:
            f.write(f"| {ts} | resolve | {args.selections} |\n")

        feature_desc = gh_utils.issue_body(issue_number)
        feature_desc = _strip_plan_footer(feature_desc)

    # --- Refine mode ---
    elif args.refine_issue:
        issue_number = args.refine_issue
        issue_url = gh_utils.issue_url(issue_number)
        prefix = f"issue-{issue_number}"
        issue_body = gh_utils.issue_body(issue_number)
        issue_body = _strip_plan_footer(issue_body)
        if not _PLAN_HEADER_HINT_RE.search(issue_body):
            _log(
                f"Warning: Issue #{issue_number} does not look like a plan "
                "(missing Implementation/Consensus Plan headers)"
            )
        feature_desc = issue_body
        if args.feature_desc:
            feature_desc = f"{feature_desc}\n\nRefinement focus:\n{args.feature_desc}"
        history_path = output_dir / f"{prefix}-history.md"
        if not history_path.exists():
            history_path.write_text(
                "# Selection & Refine History\n\n"
                "| Timestamp | Type | Content |\n"
                "|-----------|------|---------|\n"
            )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        summary = (args.feature_desc or "general refinement")[:80].replace("\n", " ")
        with history_path.open("a") as f:
            f.write(f"| {ts} | refine | {summary} |\n")

    # --- From-issue mode ---
    elif args.from_issue:
        issue_number = args.from_issue
        issue_url = gh_utils.issue_url(issue_number)
        prefix = f"issue-{issue_number}"
        feature_desc = gh_utils.issue_body(issue_number)

    # --- Default mode ---
    else:
        if not feature_desc:
            _log("Error: --feature-desc is required in default mode")
            return 1
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

    _log("Starting mega-planner 7-stage debate pipeline...")
    _log(f"Feature: {_extract_feature_name(feature_desc)}")
    _log_verbose(f"Artifacts prefix: {prefix}")

    try:
        results = run_mega_pipeline(
            feature_desc,
            output_dir=output_dir,
            prefix=prefix,
            skip_consensus=args.skip_consensus,
            report_paths=report_paths,
            consensus_path=consensus_path,
            history_path=history_path,
        )
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as exc:
        _log(f"Error: {exc}")
        return 2

    consensus_result = results.get("consensus")
    if consensus_result:
        commit_hash = _resolve_commit_hash()
        _append_plan_footer(consensus_result.output_path, commit_hash)

        if issue_mode and issue_number:
            _log(f"Publishing plan to issue #{issue_number}...")
            plan_title = _extract_plan_title(consensus_result.output_path)
            if not plan_title:
                plan_title = _shorten_feature_desc(feature_desc, max_len=50)
            plan_title = _apply_issue_tag(plan_title, issue_number)
            gh_utils.issue_edit(
                issue_number,
                title=f"[plan] {plan_title}",
                body_file=consensus_result.output_path,
            )
            gh_utils.label_add(issue_number, ["agentize:plan"])
            if issue_url:
                _log(f"See the full plan at: {issue_url}")

        _log(f"See the full plan locally at: {consensus_result.output_path}")
        print(str(consensus_result.output_path))

    _log("Pipeline complete!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
