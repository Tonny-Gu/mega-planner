"""Pipeline orchestration for the 7-stage multi-agent debate."""

from __future__ import annotations

import importlib.resources
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from agentize.workflow.api import run_acw
from agentize.workflow.api.session import Session, StageResult

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

# ============================================================
# Constants
# ============================================================

_PROMPTS = importlib.resources.files("mega_planner.prompts")

AGENT_PROMPTS = {
    "understander": "understander.md",
    "bold": "mega-bold-proposer.md",
    "paranoia": "mega-paranoia-proposer.md",
    "critique": "mega-proposal-critique.md",
    "proposal-reducer": "mega-proposal-reducer.md",
    "code-reducer": "mega-code-reducer.md",
}

DEFAULT_BACKENDS = {
    "understander": ("claude", "sonnet"),
    "bold": ("claude", "opus"),
    "paranoia": ("claude", "opus"),
    "critique": ("claude", "opus"),
    "proposal-reducer": ("claude", "opus"),
    "code-reducer": ("claude", "opus"),
    "consensus": ("claude", "opus"),
}

STAGE_TOOLS = {
    "understander": "Read,Grep,Glob",
    "bold": "Read,Grep,Glob,WebSearch,WebFetch",
    "paranoia": "Read,Grep,Glob",
    "critique": "Read,Grep,Glob,Bash",
    "proposal-reducer": "Read,Grep,Glob",
    "code-reducer": "Read,Grep,Glob",
    "consensus": "Read,Grep,Glob",
}

STAGE_PERMISSION_MODE = {
    "bold": "plan",
}


# ============================================================
# Prompt Rendering
# ============================================================


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from markdown content."""
    return _FRONTMATTER_RE.sub("", content, count=1)


def _read_agent_prompt(stage: str) -> str:
    """Read an agent prompt from package data."""
    filename = AGENT_PROMPTS[stage]
    raw = (_PROMPTS / filename).read_text(encoding="utf-8")
    return _strip_frontmatter(raw)


def _render_stage_prompt(
    stage: str,
    feature_desc: str,
    previous_output: str | None = None,
) -> str:
    """Render the input prompt for a single-input stage."""
    parts = [_read_agent_prompt(stage)]

    parts.append("\n---\n")
    parts.append("# Feature Request\n")
    parts.append(feature_desc)

    if previous_output:
        parts.append("\n---\n")
        parts.append("# Previous Stage Output\n")
        parts.append(previous_output)

    return "\n".join(parts)


def _render_dual_input_prompt(
    stage: str,
    feature_desc: str,
    bold_output: str,
    paranoia_output: str,
) -> str:
    """Render input for stages that receive both proposals."""
    parts = [_read_agent_prompt(stage)]

    parts.append("\n---\n")
    parts.append("# Feature Request\n")
    parts.append(feature_desc)
    parts.append("\n---\n")
    parts.append("# Bold Proposal\n")
    parts.append(bold_output)
    parts.append("\n---\n")
    parts.append("# Paranoia Proposal\n")
    parts.append(paranoia_output)

    return "\n".join(parts)


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


def _render_consensus_prompt(
    feature_name: str,
    feature_desc: str,
    debate_report: str,
    dest_path: Path,
) -> str:
    """Render the external-synthesize prompt template and write to dest_path."""
    raw = (_PROMPTS / "external-synthesize-prompt.md").read_text(encoding="utf-8")
    template = _strip_frontmatter(raw)
    rendered = (
        template.replace("{{FEATURE_NAME}}", feature_name)
        .replace("{{FEATURE_DESCRIPTION}}", feature_desc)
        .replace("{{COMBINED_REPORT}}", debate_report)
    )
    dest_path.write_text(rendered, encoding="utf-8")
    return rendered


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
    skip_consensus: bool = False,
    report_paths: dict[str, Path] | None = None,
    consensus_path: Path | None = None,
    history_path: Path | None = None,
) -> dict[str, StageResult]:
    """Execute the 7-stage mega-planner pipeline.

    If report_paths is provided, skip the debate stages and use
    existing report files for consensus (resolve mode).
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

    # --- Resolve mode: skip debate, load existing reports ---
    if report_paths is not None:
        _required = {"bold", "paranoia", "critique", "proposal-reducer", "code-reducer"}
        missing = _required - report_paths.keys()
        if missing:
            raise ValueError(f"report_paths missing required stages: {missing}")
        bold_output = report_paths["bold"].read_text()
        paranoia_output = report_paths["paranoia"].read_text()
        critique_output = report_paths["critique"].read_text()
        proposal_reducer_output = report_paths["proposal-reducer"].read_text()
        code_reducer_output = report_paths["code-reducer"].read_text()
    else:
        # --- Tier 1: Understander ---
        _log(f"Stage 1/7: Running understander ({_backend_label('understander')})")
        understander_prompt = _render_stage_prompt("understander", feature_desc)
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
        bold_prompt = _render_stage_prompt("bold", feature_desc, understander_output)
        paranoia_prompt = _render_stage_prompt("paranoia", feature_desc, understander_output)

        parallel_2 = session.run_parallel(
            [
                session.stage("bold", bold_prompt, stage_backends["bold"],
                              tools=STAGE_TOOLS.get("bold"),
                              permission_mode=STAGE_PERMISSION_MODE.get("bold")),
                session.stage("paranoia", paranoia_prompt, stage_backends["paranoia"],
                              tools=STAGE_TOOLS.get("paranoia"),
                              permission_mode=STAGE_PERMISSION_MODE.get("paranoia")),
            ],
            max_workers=2,
        )
        results.update(parallel_2)
        bold_output = results["bold"].text()
        paranoia_output = results["paranoia"].text()

        # --- Tier 3: Critique + Proposal Reducer + Code Reducer in parallel ---
        _log(
            f"Stage 4-6/7: Running critique + reducers in parallel "
            f"({_backend_label('critique')}, {_backend_label('proposal-reducer')}, "
            f"{_backend_label('code-reducer')})"
        )
        critique_prompt = _render_dual_input_prompt(
            "critique", feature_desc, bold_output, paranoia_output
        )
        proposal_reducer_prompt = _render_dual_input_prompt(
            "proposal-reducer", feature_desc, bold_output, paranoia_output
        )
        code_reducer_prompt = _render_dual_input_prompt(
            "code-reducer", feature_desc, bold_output, paranoia_output
        )

        parallel_3 = session.run_parallel(
            [
                session.stage("critique", critique_prompt, stage_backends["critique"],
                              tools=STAGE_TOOLS.get("critique"),
                              permission_mode=STAGE_PERMISSION_MODE.get("critique")),
                session.stage("proposal-reducer", proposal_reducer_prompt,
                              stage_backends["proposal-reducer"],
                              tools=STAGE_TOOLS.get("proposal-reducer"),
                              permission_mode=STAGE_PERMISSION_MODE.get("proposal-reducer")),
                session.stage("code-reducer", code_reducer_prompt,
                              stage_backends["code-reducer"],
                              tools=STAGE_TOOLS.get("code-reducer"),
                              permission_mode=STAGE_PERMISSION_MODE.get("code-reducer")),
            ],
            max_workers=3,
        )
        results.update(parallel_3)
        critique_output = results["critique"].text()
        proposal_reducer_output = results["proposal-reducer"].text()
        code_reducer_output = results["code-reducer"].text()

    if skip_consensus:
        return results

    # --- Tier 4: Consensus via external AI ---
    feature_name = extract_feature_name(feature_desc)
    debate_report = _build_debate_report(
        feature_name,
        bold_output, paranoia_output,
        critique_output, proposal_reducer_output, code_reducer_output,
    )

    # Append resolve/refine context if provided
    if consensus_path and consensus_path.exists():
        prev_plan = consensus_path.read_text()
        debate_report += (
            f"\n## Part 6: Previous Consensus Plan\n\n"
            f"The following is the previous consensus plan being refined:\n\n"
            f"{prev_plan}\n\n---\n"
        )
    if history_path and history_path.exists():
        history_content = history_path.read_text()
        debate_report += (
            f"\n## Part 7: Selection & Refine History\n\n"
            f"**IMPORTANT**: The last row of the table below contains the current task requirement.\n"
            f"Apply the current task to the previous consensus plan to generate the updated plan.\n\n"
            f"{history_content}\n\n---\n"
        )

    # Save debate report
    debate_file = output_path / f"{prefix}-debate.md"
    debate_file.write_text(debate_report)

    def _write_consensus_prompt(path: Path) -> str:
        return _render_consensus_prompt(feature_name, feature_desc, debate_report, path)

    _log(f"Stage 7/7: Running consensus ({_backend_label('consensus')})")
    results["consensus"] = session.run_prompt(
        "consensus",
        _write_consensus_prompt,
        stage_backends["consensus"],
        tools=STAGE_TOOLS.get("consensus"),
        permission_mode=STAGE_PERMISSION_MODE.get("consensus"),
    )

    return results
