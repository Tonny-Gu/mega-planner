"""Tests for mega-planner pipeline orchestration.

Verifies 7-stage mega-planner pipeline with a stub runner (no actual LLM calls).
"""

import subprocess
from pathlib import Path
from typing import Callable

import pytest

from mega_planner import (
    AGENT_PROMPTS,
    DEFAULT_BACKENDS,
    STAGE_TOOLS,
    STAGE_PERMISSION_MODE,
    main,
    run_mega_pipeline,
    run_resolve_pipeline,
    extract_feature_name,
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
        elif "synthesizer" in str(output_path):
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
            "synthesizer",
        }
        assert set(results.keys()) == expected

    def test_continue_mode_skips_existing(self, tmp_output_dir: Path, stub_runner: Callable):
        """continue_mode=True skips stages with existing non-empty output."""
        # Pre-populate some outputs
        for stage in ["understander", "bold", "paranoia"]:
            (tmp_output_dir / f"test-{stage}-output.md").write_text(f"# Existing {stage}")

        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            continue_mode=True,
        )
        expected = {
            "understander", "bold", "paranoia",
            "critique", "proposal-reducer", "code-reducer",
            "synthesizer",
        }
        assert set(results.keys()) == expected
        # Skipped stages should have dummy process (no real args)
        assert results["understander"].process.args == []
        assert results["bold"].process.args == []
        assert results["paranoia"].process.args == []
        # Non-skipped stages should have real process (stub args)
        assert results["critique"].process.args != []
        assert results["synthesizer"].process.args != []

    def test_continue_mode_all_cached(self, tmp_output_dir: Path, stub_runner: Callable):
        """continue_mode=True with all outputs cached runs nothing."""
        all_stages = [
            "understander", "bold", "paranoia",
            "critique", "proposal-reducer", "code-reducer", "synthesizer",
        ]
        for stage in all_stages:
            (tmp_output_dir / f"test-{stage}-output.md").write_text(f"# Existing {stage}")

        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            continue_mode=True,
        )
        assert set(results.keys()) == set(all_stages)
        assert len(stub_runner.invocations) == 0

    def test_continue_mode_empty_file_not_skipped(self, tmp_output_dir: Path, stub_runner: Callable):
        """continue_mode=True does not skip stages with empty output files."""
        (tmp_output_dir / "test-understander-output.md").write_text("")

        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            continue_mode=True,
        )
        # Understander had empty file, so it should have been re-run
        assert results["understander"].process.args != []

    def test_resolve_mode_skips_debate(self, tmp_output_dir: Path, stub_runner: Callable):
        """Resolve pipeline uses existing report files, skips debate stages."""
        report_paths = {}
        for stage in ["bold", "paranoia", "critique", "proposal-reducer", "code-reducer"]:
            p = tmp_output_dir / f"test-{stage}-output.md"
            p.write_text(f"existing {stage} output")
            report_paths[stage] = p

        synthesizer_path = tmp_output_dir / "test-synthesizer-output.md"
        synthesizer_path.write_text("# Previous Plan\n\nExisting plan content.")

        results = run_resolve_pipeline(
            "Test feature description",
            "1B,2A",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
            report_paths=report_paths,
            synthesizer_path=synthesizer_path,
        )
        assert "resolver" in results
        assert "understander" not in results

    def test_output_artifacts_created(self, tmp_output_dir: Path, stub_runner: Callable):
        """Pipeline creates output files for each stage."""
        results = run_mega_pipeline(
            "Test feature description",
            output_dir=tmp_output_dir,
            runner=stub_runner,
            prefix="test",
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

    def test_extracts_plan_title_with_legacy_consensus_prefix(self, tmp_path: Path):
        """Backward compat: old outputs may use '# Consensus Plan:'."""
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


# ============================================================
# Test CLI Argument Parsing
# ============================================================


class TestCLIArgumentParsing:
    """Test CLI argument parsing after positional-args refactor."""

    def test_default_mode_positional(self, tmp_output_dir, stub_runner, monkeypatch):
        """Positional arg becomes feature description in default mode."""
        monkeypatch.setattr("mega_planner.run_mega_pipeline", lambda *a, **kw: {})
        monkeypatch.setattr("mega_planner.gh_utils.issue_create", lambda *a, **kw: (None, None))
        result = main(["Add", "dark", "mode", "--output-dir", str(tmp_output_dir), "--local"])
        assert result == 0

    def test_default_mode_no_args_fails(self):
        """No positional arg in default mode exits with error."""
        result = main(["--local"])
        assert result != 0

    def test_resolve_short_flag(self, tmp_output_dir, stub_runner, monkeypatch):
        """-r is shorthand for --resolve and calls run_resolve_pipeline."""
        captured = {}

        def fake_resolve(desc, selections, **kw):
            captured["desc"] = desc
            captured["selections"] = selections
            return {}

        monkeypatch.setattr("mega_planner.run_resolve_pipeline", fake_resolve)
        monkeypatch.setattr("mega_planner.gh_utils.issue_body", lambda n: "issue body")
        for stage in ["bold", "paranoia", "critique", "proposal-reducer", "code-reducer"]:
            (tmp_output_dir / f"issue-42-{stage}-output.md").write_text("stub")
        result = main(["-r", "42", "1B,2A", "--output-dir", str(tmp_output_dir)])
        assert result == 0
        assert captured["selections"] == "1B,2A"

    def test_local_flag_skips_issue_creation(self, tmp_output_dir, stub_runner, monkeypatch):
        """--local prevents GitHub issue creation."""
        create_called = []

        def fake_create(*a, **kw):
            create_called.append(1)
            return (None, None)

        monkeypatch.setattr("mega_planner.run_mega_pipeline", lambda *a, **kw: {})
        monkeypatch.setattr("mega_planner.gh_utils.issue_create", fake_create)
        result = main(["Test", "feature", "--output-dir", str(tmp_output_dir), "--local"])
        assert result == 0
        assert len(create_called) == 0

    def test_override_mode_uses_positional_desc(self, tmp_output_dir, stub_runner, monkeypatch):
        """--override takes issue number but feature desc comes from positional args."""
        captured = {}

        def fake_pipeline(desc, **kw):
            captured["desc"] = desc
            captured["prefix"] = kw.get("prefix")
            return {}

        monkeypatch.setattr("mega_planner.run_mega_pipeline", fake_pipeline)
        monkeypatch.setattr("mega_planner.gh_utils.issue_url", lambda n: f"https://github.com/test/issues/{n}")
        result = main(["--override", "42", "Custom", "feature", "desc", "--output-dir", str(tmp_output_dir)])
        assert result == 0
        assert captured["desc"] == "Custom feature desc"
        assert captured["prefix"] == "issue-42"

    def test_override_mode_no_positional_fails(self, tmp_output_dir, monkeypatch):
        """--override without positional args fails."""
        result = main(["--override", "42", "--output-dir", str(tmp_output_dir)])
        assert result != 0
