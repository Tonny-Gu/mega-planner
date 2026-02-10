"""Tests for mega-planner pipeline orchestration.

Verifies 7-stage mega-planner pipeline with a stub runner (no actual LLM calls).
"""

import subprocess
from pathlib import Path
from typing import Callable

import pytest

from mega_planner.pipeline import (
    AGENT_PROMPTS,
    DEFAULT_BACKENDS,
    STAGE_TOOLS,
    STAGE_PERMISSION_MODE,
    run_mega_pipeline,
    extract_feature_name,
)
from mega_planner.cli import (
    _resolve_commit_hash,
    _append_plan_footer,
    _strip_plan_footer,
    _shorten_feature_desc,
    _extract_plan_title,
    _apply_issue_tag,
)

# Re-import Session for monkeypatching in parallel tests
from agentize.workflow.api.session import Session


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Create a temporary output directory for artifacts."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def stub_runner() -> Callable:
    """Create a stub runner that writes output files and records invocations."""
    invocations = []

    def _stub(
        provider: str,
        model: str,
        input_file: str | Path,
        output_file: str | Path,
        *,
        tools: str | None = None,
        permission_mode: str | None = None,
        extra_flags: list[str] | None = None,
        timeout: int = 900,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        invocations.append({
            "provider": provider,
            "model": model,
            "input_file": str(input_file),
            "output_file": str(output_file),
            "tools": tools,
            "permission_mode": permission_mode,
        })

        output_path = Path(output_file)
        if "understander" in str(output_path):
            content = "# Understander Output\n\nContext gathered for feature."
        elif "bold" in str(output_path):
            content = "# Bold Proposal\n\nInnovative approach with code diff drafts."
        elif "paranoia" in str(output_path):
            content = "# Paranoia Proposal\n\nDestructive refactoring approach."
        elif "critique" in str(output_path):
            content = "# Critique\n\nFeasibility analysis of both proposals."
        elif "proposal-reducer" in str(output_path):
            content = "# Proposal Reducer\n\nSimplified both proposals."
        elif "code-reducer" in str(output_path):
            content = "# Code Reducer\n\nCode footprint analysis."
        elif "consensus" in str(output_path):
            content = "# Implementation Plan: Test Feature\n\nBalanced plan."
        else:
            content = f"# Stage Output\n\nOutput for {output_path.name}"

        output_path.write_text(content)

        return subprocess.CompletedProcess(
            args=["stub", str(input_file)],
            returncode=0,
            stdout="",
            stderr="",
        )

    _stub.invocations = invocations
    return _stub


# ============================================================
# Test Pipeline Stage Results
# ============================================================


class TestMegaPipelineStages:
    """Test 7-stage pipeline produces all expected outputs."""

    def test_returns_all_seven_stages(self, tmp_output_dir: Path, stub_runner: Callable):
        """Pipeline returns results for all 7 stages."""
        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
        )
        expected = {
            "understander", "bold", "paranoia",
            "critique", "proposal-reducer", "code-reducer",
            "consensus",
        }
        assert set(results.keys()) == expected

    def test_skip_consensus(self, tmp_output_dir: Path, stub_runner: Callable):
        """skip_consensus=True returns 6 stages without consensus."""
        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            skip_consensus=True,
        )
        assert "consensus" not in results
        assert len(results) == 6

    def test_resolve_mode_skips_debate(self, tmp_output_dir: Path, stub_runner: Callable):
        """Resolve mode uses existing report files, skips debate stages."""
        report_paths = {}
        for stage in ["bold", "paranoia", "critique", "proposal-reducer", "code-reducer"]:
            p = tmp_output_dir / f"test-{stage}-output.md"
            p.write_text(f"existing {stage} output")
            report_paths[stage] = p

        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            report_paths=report_paths,
        )
        assert "consensus" in results
        assert "understander" not in results

    def test_output_artifacts_created(self, tmp_output_dir: Path, stub_runner: Callable):
        """Pipeline creates output files for each stage."""
        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            skip_consensus=True,
        )
        for stage, result in results.items():
            assert result.output_path.exists(), f"Missing output for {stage}"
            assert result.output_path.stat().st_size > 0

    def test_debate_report_saved(self, tmp_output_dir: Path, stub_runner: Callable):
        """Pipeline saves combined debate report."""
        run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
        )
        debate_file = tmp_output_dir / "test-debate.md"
        assert debate_file.exists()
        content = debate_file.read_text()
        assert "Bold Proposer" in content
        assert "Paranoia Proposer" in content


# ============================================================
# Test Execution Order
# ============================================================


class TestMegaPipelineExecutionOrder:
    """Tests for correct stage execution order."""

    def test_understander_runs_before_proposers(self, tmp_output_dir: Path, stub_runner: Callable):
        """Understander always runs before bold and paranoia."""
        run_mega_pipeline(
            "Test feature",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            skip_consensus=True,
        )

        invocations = stub_runner.invocations
        understander_idx = None
        bold_idx = None
        paranoia_idx = None

        for idx, inv in enumerate(invocations):
            if "understander" in inv["output_file"] and understander_idx is None:
                understander_idx = idx
            if "bold" in inv["output_file"] and bold_idx is None:
                bold_idx = idx
            if "paranoia" in inv["output_file"] and paranoia_idx is None:
                paranoia_idx = idx

        assert understander_idx is not None
        assert bold_idx is not None
        assert paranoia_idx is not None
        assert understander_idx < bold_idx
        assert understander_idx < paranoia_idx

    def test_bold_paranoia_parallel(self, tmp_output_dir: Path, stub_runner: Callable, monkeypatch):
        """Bold and paranoia are dispatched through the parallel runner."""
        recorded = {}

        def _run_parallel(self, calls, *, max_workers=2, retry=0, retry_delay=0.0):
            call_list = list(calls)
            stages = [c.stage for c in call_list]
            recorded.setdefault("parallel_calls", []).append(sorted(stages))
            results = {}
            for call in call_list:
                results[call.stage] = self.run_prompt(
                    call.stage, call.prompt, call.backend, **call.options,
                )
            return results

        monkeypatch.setattr(Session, "run_parallel", _run_parallel)

        run_mega_pipeline(
            "Test feature",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            skip_consensus=True,
        )

        assert recorded.get("parallel_calls") is not None
        assert ["bold", "paranoia"] in recorded["parallel_calls"]
        assert ["code-reducer", "critique", "proposal-reducer"] in recorded["parallel_calls"]


# ============================================================
# Test Prompt Rendering
# ============================================================


class TestMegaPipelinePromptRendering:
    """Tests for correct prompt rendering."""

    def test_feature_description_in_prompts(self, tmp_output_dir: Path, stub_runner: Callable):
        """Feature description appears in rendered input prompts."""
        feature_desc = "Implement mega-planner as standalone Python script"

        results = run_mega_pipeline(
            feature_desc,
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            skip_consensus=True,
        )

        understander_input = results["understander"].input_path.read_text()
        assert feature_desc in understander_input

    def test_dual_input_stages_have_both_proposals(self, tmp_output_dir: Path, stub_runner: Callable):
        """Critique and reducer stages receive both bold and paranoia outputs."""
        results = run_mega_pipeline(
            "Test feature",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            skip_consensus=True,
        )

        critique_input = results["critique"].input_path.read_text()
        assert "Bold Proposal" in critique_input
        assert "Paranoia Proposal" in critique_input


# ============================================================
# Test Feature Name Extraction
# ============================================================


class TestExtractFeatureName:
    """Test feature name extraction."""

    def test_short_description(self):
        assert extract_feature_name("Add dark mode") == "Add dark mode"

    def test_long_description_truncated(self):
        long_desc = "A" * 100
        result = extract_feature_name(long_desc, max_len=80)
        assert len(result) <= 84  # 80 + "..."
        assert result.endswith("...")

    def test_multiline_uses_first_line(self):
        result = extract_feature_name("First line\nSecond line\nThird")
        assert result == "First line"


# ============================================================
# Test CLI Helpers
# ============================================================


class TestStripPlanFooter:
    """Test _strip_plan_footer utility."""

    def test_strips_footer(self):
        text = "# Plan\n\nContent here\n\nPlan based on commit abc12345\n"
        result = _strip_plan_footer(text)
        assert "Plan based on commit" not in result
        assert "Content here" in result

    def test_no_footer_unchanged(self):
        text = "# Plan\n\nContent here\n"
        assert _strip_plan_footer(text) == text

    def test_empty_string(self):
        assert _strip_plan_footer("") == ""


class TestAppendPlanFooter:
    """Test _append_plan_footer utility."""

    def test_appends_footer(self, tmp_path: Path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n\nContent\n")
        _append_plan_footer(plan_file, "abc12345")
        content = plan_file.read_text()
        assert "Plan based on commit abc12345" in content

    def test_no_duplicate_footer(self, tmp_path: Path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n\nContent\nPlan based on commit abc12345")
        _append_plan_footer(plan_file, "abc12345")
        content = plan_file.read_text()
        assert content.count("Plan based on commit") == 1


class TestApplyIssueTag:
    """Test _apply_issue_tag utility."""

    def test_prepends_tag(self):
        assert _apply_issue_tag("My Plan", "42") == "[#42] My Plan"

    def test_no_duplicate_tag(self):
        assert _apply_issue_tag("[#42] My Plan", "42") == "[#42] My Plan"

    def test_empty_title(self):
        assert _apply_issue_tag("", "42") == "[#42]"


class TestShortenFeatureDesc:
    """Test _shorten_feature_desc utility."""

    def test_short_unchanged(self):
        assert _shorten_feature_desc("short desc") == "short desc"

    def test_long_truncated(self):
        long_desc = "A" * 100
        result = _shorten_feature_desc(long_desc, max_len=50)
        assert len(result) <= 54  # 50 + "..."
        assert result.endswith("...")


class TestExtractPlanTitle:
    """Test _extract_plan_title utility."""

    def test_extracts_implementation_plan_title(self, tmp_path: Path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Implementation Plan: My Feature\n\nContent")
        result = _extract_plan_title(plan_file)
        assert result == "My Feature"

    def test_extracts_consensus_plan_title(self, tmp_path: Path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Consensus Plan: Another Feature\n\nContent")
        result = _extract_plan_title(plan_file)
        assert result == "Another Feature"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        plan_file = tmp_path / "nonexistent.md"
        assert _extract_plan_title(plan_file) == ""


class TestResolveCommitHash:
    """Test _resolve_commit_hash utility."""

    def test_returns_hash_string(self):
        result = _resolve_commit_hash()
        # Should return a hex string or "unknown"
        assert isinstance(result, str)
        assert len(result) > 0
