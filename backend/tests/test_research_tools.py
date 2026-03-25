"""Tests for Deep Research v2 research tools (Phase 2.7 + Phase 4)."""

import importlib
import json
from types import SimpleNamespace

import pytest

research_tools = importlib.import_module("deerflow.tools.builtins.research_tools")


def _make_runtime(workspace_path: str, *, messages=None) -> SimpleNamespace:
    state = {"thread_data": {"workspace_path": workspace_path}}
    if messages is not None:
        state["messages"] = messages
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# _parse_outline
# ---------------------------------------------------------------------------


class TestParseOutline:
    def test_empty_outline(self):
        assert research_tools._parse_outline("") == {}

    def test_only_h2_headings_excluded(self):
        outline = "## 1. Introduction\n## 2. Body\n## 3. Conclusion\n"
        assert research_tools._parse_outline(outline) == {}

    def test_h3_and_h4_included(self):
        outline = (
            "## 1. Introduction\n"
            "### 1.1 Background\n"
            "[sources: 1, 2]\n"
            "#### 1.1.1 Detail\n"
            "[sources: 3]\n"
        )
        parsed = research_tools._parse_outline(outline)
        assert len(parsed) == 2
        assert parsed["### 1.1 Background"] == [1, 2]
        assert parsed["#### 1.1.1 Detail"] == [3]

    def test_subsection_without_sources(self):
        outline = "### 1.1 Background\n### 1.2 Problem\n"
        parsed = research_tools._parse_outline(outline)
        assert len(parsed) == 2
        assert parsed["### 1.1 Background"] == []
        assert parsed["### 1.2 Problem"] == []

    def test_mixed_cited_and_uncited(self):
        outline = (
            "### 1.1 Background\n"
            "[sources: 1, 2]\n"
            "### 1.2 Problem\n"
            "### 1.3 Scope\n"
            "[sources: 5]\n"
        )
        parsed = research_tools._parse_outline(outline)
        assert parsed["### 1.1 Background"] == [1, 2]
        assert parsed["### 1.2 Problem"] == []
        assert parsed["### 1.3 Scope"] == [5]

    def test_single_id(self):
        outline = "### 1.1 Topic\n[sources: 7]\n"
        parsed = research_tools._parse_outline(outline)
        assert parsed["### 1.1 Topic"] == [7]

    def test_intermediate_lines_between_heading_and_sources(self):
        outline = (
            "### 1.1 Background\n"
            "Some description text about this section.\n"
            "[sources: 1, 2]\n"
        )
        parsed = research_tools._parse_outline(outline)
        assert parsed["### 1.1 Background"] == [1, 2]

    def test_blank_lines_between_heading_and_sources(self):
        outline = (
            "### 1.1 Background\n"
            "\n"
            "[sources: 1]\n"
        )
        parsed = research_tools._parse_outline(outline)
        assert parsed["### 1.1 Background"] == [1]

    def test_old_citation_format_not_recognized(self):
        outline = "### 1.1 Background <citation>1, 2</citation>\n"
        parsed = research_tools._parse_outline(outline)
        assert parsed["### 1.1 Background <citation>1, 2</citation>"] == []

    def test_sources_consumed_resets_current_heading(self):
        outline = (
            "### 1.1 A\n"
            "[sources: 1]\n"
            "Some text that should not match\n"
            "### 1.2 B\n"
        )
        parsed = research_tools._parse_outline(outline)
        assert parsed["### 1.1 A"] == [1]
        assert parsed["### 1.2 B"] == []


# ---------------------------------------------------------------------------
# evidence_store_tool
# ---------------------------------------------------------------------------


class TestEvidenceStore:
    def test_stores_new_entry(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/page1",
            title="AI Diagnosis Accuracy Study",
            summary="Relevant AI paper.",
            evidence="Key finding: AI improves diagnosis accuracy by 30%.",
            goal="AI healthcare applications",
        )
        assert "source 1" in result
        assert "Total sources: 1" in result

        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert mb["url2id"]["https://example.com/page1"] == 1
        assert len(mb["page_info"]) == 1
        assert mb["page_info"][0]["summary"] == "Relevant AI paper."
        assert mb["page_info"][0]["title"] == "AI Diagnosis Accuracy Study"

    def test_rejects_duplicate_url(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/dup",
            title="First Page",
            summary="First entry.",
            evidence="Evidence A.",
            goal="Goal A",
        )
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/dup",
            title="Duplicate Page",
            summary="Duplicate entry.",
            evidence="Evidence B.",
            goal="Goal B",
        )
        assert "already stored" in result
        assert "source 1" in result

        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert len(mb["page_info"]) == 1

    def test_increments_ids(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        for i in range(3):
            research_tools.evidence_store_tool.func(
                runtime=rt,
                url=f"https://example.com/page{i}",
                title=f"Page {i}",
                summary=f"Summary {i}",
                evidence=f"Evidence {i}",
                goal="test",
            )
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert len(mb["page_info"]) == 3
        ids = [p["id"] for p in mb["page_info"]]
        assert ids == [1, 2, 3]

    def test_evidence_bank_does_not_contain_research_iterations(self, tmp_path):
        """Phase 2.7: research_iterations no longer stored in evidence_bank.json."""
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/x",
            title="Test Page",
            summary="s",
            evidence="e",
            goal="g",
        )
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert "research_iterations" not in mb
        assert "reflection_history" not in mb


# ---------------------------------------------------------------------------
# evidence_retrieve_tool
# ---------------------------------------------------------------------------


class TestEvidenceRetrieve:
    def _seed_bank(self, workspace: str):
        rt = _make_runtime(workspace)
        for i in range(3):
            research_tools.evidence_store_tool.func(
                runtime=rt,
                url=f"https://example.com/src{i}",
                title=f"Source Page {i}",
                summary=f"Summary for source {i}",
                evidence=f"Detailed evidence for source {i}",
                goal="test goal",
            )

    def test_retrieves_by_ids(self, tmp_path):
        self._seed_bank(str(tmp_path))
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="1,3")
        assert '<source id="1">' in result
        assert '<source id="3">' in result
        assert "Detailed evidence for source 0" in result
        assert "Detailed evidence for source 2" in result
        assert "Title: Source Page 0" in result
        assert "Title: Source Page 2" in result

    def test_returns_friendly_message_for_missing_ids(self, tmp_path):
        self._seed_bank(str(tmp_path))
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="99")
        assert "No evidence found" in result

    def test_handles_spaces_in_ids(self, tmp_path):
        self._seed_bank(str(tmp_path))
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="1, 2")
        assert '<source id="1">' in result
        assert '<source id="2">' in result


# ---------------------------------------------------------------------------
# outline_update_tool
# ---------------------------------------------------------------------------


class TestOutlineUpdate:
    def test_counts_subsections_only(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        outline = (
            "## 1. Introduction\n"
            "### 1.1 Background\n"
            "[sources: 1, 2]\n"
            "### 1.2 Problem\n"
            "## 2. Analysis\n"
            "### 2.1 Methods\n"
            "[sources: 3]\n"
        )
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content=outline
        )
        assert "3 subsections" in result
        assert "2 with sources" in result

    def test_no_coverage_percentage_in_return(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        outline = "### 1.1 Background\n[sources: 1]\n### 1.2 Problem\n"
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content=outline
        )
        assert "%" not in result

    def test_no_iteration_count_in_return(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        outline = "### 1.1 Background\n"
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content=outline
        )
        assert "Iteration:" not in result
        assert "outline_iterations" not in result

    def test_does_not_increment_iterations(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        for i in range(3):
            research_tools.outline_update_tool.func(
                runtime=rt, outline_content=f"### Section {i}\n"
            )
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert "outline_iterations" not in mb
        assert mb.get("research_iterations", 0) == 0

    def test_writes_outline_file(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        content = "## Test Outline\n### Section A\n"
        research_tools.outline_update_tool.func(runtime=rt, outline_content=content)
        assert (tmp_path / "outline.md").read_text() == content

    def test_reminder_references_reflection_task(self, tmp_path):
        """Phase 2.7: reminder must reference task(subagent_type='reflection') not research_reflect."""
        rt = _make_runtime(str(tmp_path))
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content="### Section 1\n",
        )
        assert "REMINDER" in result
        assert "[sources: 1, 2]" in result
        assert "<citation>" not in result
        assert "research_reflect" not in result
        assert "task(subagent_type='reflection')" in result


class TestOutlineUpdateCitationValidation:
    def test_detects_invalid_citation_ids(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt, url="https://ex.com/1", title="t", summary="s", evidence="e", goal="g",
        )
        outline = (
            "### 1.1 Background\n"
            "[sources: 1, 15, 22]\n"
        )
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content=outline,
        )
        assert "Invalid citation IDs" in result
        assert "15" in result
        assert "22" in result

    def test_no_warning_when_all_ids_valid(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        for i in range(3):
            research_tools.evidence_store_tool.func(
                runtime=rt, url=f"https://ex.com/{i}", title="t", summary="s", evidence="e", goal="g",
            )
        outline = (
            "### 1.1 Intro\n"
            "[sources: 1, 2]\n"
            "### 1.2 Body\n"
            "[sources: 3]\n"
        )
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content=outline,
        )
        assert "Invalid citation IDs" not in result


# ---------------------------------------------------------------------------
# evidence_store + evidence_retrieve reminders (Phase 2.7 updates)
# ---------------------------------------------------------------------------


class TestEvidenceStoreReminder:
    def test_return_contains_reminder_and_counts(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/r1",
            title="Test Source Page",
            summary="Test source.",
            evidence="Evidence data.",
            goal="test",
        )
        assert "REMINDER" in result
        assert "update outline" in result
        assert "task(subagent_type='reflection')" in result
        assert "Total sources: 1" in result
        assert "research_reflect" not in result
        assert "Research iterations" not in result


class TestEvidenceRetrieveReminder:
    def test_return_contains_citation_format_reminder(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt, url="https://ex.com/1", title="Example Page", summary="s", evidence="e", goal="g",
        )
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="1")
        assert "[citation:" in result
        assert "[sources: ...]" in result
        assert "outline-only" in result
        assert "Title: Example Page" in result

    def test_no_reminder_when_no_results(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="99")
        assert "No evidence found" in result
        assert "REMINDER" not in result


# ---------------------------------------------------------------------------
# check_query_duplicate_tool (Phase 2.7: M2 disambiguation)
# ---------------------------------------------------------------------------


class TestCheckQueryDuplicate:
    def test_unique_query_recorded(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="AI in healthcare 2026"
        )
        assert "unique" in result.lower()
        assert "1" in result

        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert "AI in healthcare 2026" in mb["executed_queries"]

    def test_detects_duplicate_query(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="AI in healthcare applications 2026"
        )
        result = research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="AI in healthcare applications 2026"
        )
        assert "DUPLICATE" in result

    def test_detects_similar_query(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="artificial intelligence healthcare applications"
        )
        result = research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="artificial intelligence healthcare application"
        )
        assert "DUPLICATE" in result

    def test_allows_different_query(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="AI in healthcare"
        )
        result = research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="quantum computing trends 2026"
        )
        assert "unique" in result.lower()

        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert len(mb["executed_queries"]) == 2

    def test_duplicate_message_includes_disambiguation(self, tmp_path):
        """Phase 2.7 M2: DUPLICATE message must clarify it's NOT a stop signal."""
        rt = _make_runtime(str(tmp_path))
        research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="machine learning material property prediction"
        )
        result = research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="machine learning material property prediction"
        )
        assert "DUPLICATE" in result
        assert "deduplication check ONLY" in result
        assert "Do NOT interpret" in result
        assert "stop researching" in result

    def test_non_duplicate_lacks_disambiguation(self, tmp_path):
        """Non-duplicate queries must NOT contain the stop-signal disclaimer."""
        rt = _make_runtime(str(tmp_path))
        result = research_tools.check_query_duplicate_tool.func(
            runtime=rt, query="some unique query about robots"
        )
        assert "deduplication check ONLY" not in result
        assert "Do NOT interpret" not in result


# ---------------------------------------------------------------------------
# Reflection Subagent registration (Phase 2.7)
# ---------------------------------------------------------------------------


class TestReflectionSubagentRegistration:
    def test_reflection_agent_config_has_correct_tools(self):
        import importlib.util
        import sys
        from pathlib import Path

        backend_dir = Path(__file__).parent.parent
        config_path = backend_dir / "packages/harness/deerflow/subagents/config.py"
        agent_path = backend_dir / "packages/harness/deerflow/subagents/builtins/reflection_agent.py"

        spec = importlib.util.spec_from_file_location("subagents_config", config_path)
        config_mod = importlib.util.module_from_spec(spec)
        sys.modules["deerflow.subagents.config"] = config_mod
        spec.loader.exec_module(config_mod)

        spec2 = importlib.util.spec_from_file_location("reflection_agent", agent_path)
        refl_mod = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(refl_mod)

        c = refl_mod.REFLECTION_AGENT_CONFIG
        assert c.name == "reflection"
        assert c.tools == ["read_file", "write_file", "ls"]
        assert "task" in c.disallowed_tools
        assert "ask_clarification" in c.disallowed_tools
        assert "present_files" in c.disallowed_tools
        assert c.max_turns == 15
        assert c.timeout_seconds == 120
        assert c.model == "inherit"

    def test_reflection_agent_system_prompt_has_key_sections(self):
        import importlib.util
        import sys
        from pathlib import Path

        backend_dir = Path(__file__).parent.parent
        config_path = backend_dir / "packages/harness/deerflow/subagents/config.py"
        agent_path = backend_dir / "packages/harness/deerflow/subagents/builtins/reflection_agent.py"

        spec = importlib.util.spec_from_file_location("subagents_config_2", config_path)
        config_mod = importlib.util.module_from_spec(spec)
        sys.modules["deerflow.subagents.config"] = config_mod
        spec.loader.exec_module(config_mod)

        spec2 = importlib.util.spec_from_file_location("reflection_agent_2", agent_path)
        refl_mod = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(refl_mod)

        prompt = refl_mod.REFLECTION_SYSTEM_PROMPT
        assert "research_state.json" in prompt
        assert "research_iterations" in prompt
        assert "research_complete" in prompt
        assert "suggested_queries" in prompt
        assert "outline_evolution" in prompt
        assert "EVALUATION_FRAMEWORK" in prompt
        assert "PROGRESSIVE_RESEARCH_STRATEGY" in prompt
        assert "GAP_PRIORITIZATION" in prompt
        assert "WORKFLOW" in prompt
        assert "OUTPUT_FORMAT" in prompt
        assert "CALIBRATION_EXAMPLES" in prompt


# ---------------------------------------------------------------------------
# _extract_heading_key (Phase 4)
# ---------------------------------------------------------------------------


class TestExtractHeadingKey:
    @pytest.mark.parametrize("heading,expected", [
        ("### 2.1 Architecture Overview", "Architecture Overview"),
        ("## Performance Analysis", "Performance Analysis"),
        ("#### 3.1.2 Sub-topic", "Sub-topic"),
        ("# Introduction", "Introduction"),
        ("## 1. Background", "Background"),
        ("### 1.1.1 Deep Dive", "Deep Dive"),
    ])
    def test_strips_prefix(self, heading, expected):
        assert research_tools._extract_heading_key(heading) == expected


# ---------------------------------------------------------------------------
# _parse_report_sections (Phase 4)
# ---------------------------------------------------------------------------


class TestParseReportSections:
    def test_empty_report(self):
        assert research_tools._parse_report_sections("") == []

    def test_single_section(self):
        report = "## Introduction\nSome text here.\n"
        sections = research_tools._parse_report_sections(report)
        assert len(sections) == 1
        assert sections[0][0] == "## Introduction"
        assert "Some text here." in sections[0][1]

    def test_multiple_sections(self):
        report = (
            "## Introduction\nIntro content.\n"
            "## Background\nBackground content.\n"
            "## Conclusion\nConclusion content.\n"
        )
        sections = research_tools._parse_report_sections(report)
        assert len(sections) == 3
        assert sections[0][0] == "## Introduction"
        assert sections[1][0] == "## Background"
        assert sections[2][0] == "## Conclusion"


# ---------------------------------------------------------------------------
# _is_content_section (Phase 4)
# ---------------------------------------------------------------------------


class TestIsContentSection:
    @pytest.mark.parametrize("key,expected", [
        ("Introduction", False),
        ("Conclusion", False),
        ("Sources", False),
        ("References", False),
        ("Architecture Overview", True),
        ("Performance Analysis", True),
        ("Key Findings", True),
        ("Background and introduction to X", False),
    ])
    def test_classification(self, key, expected):
        assert research_tools._is_content_section(key) == expected


# ---------------------------------------------------------------------------
# report_validate_tool (Phase 4)
# ---------------------------------------------------------------------------

VALID_REPORT = """\
## Introduction

This report covers the key findings about AI in healthcare, examining architecture,
performance, adoption patterns, design decisions, and comparative outcomes across
multiple deployment contexts.

## 1. Background and Problem

### 1.1 Background

Healthcare AI systems have evolved significantly over the past decade.
[citation:AI Study](https://ai.example.com)
Modern deployments leverage large language models and specialized diagnostic networks
to augment clinical decision-making at scale.

### 1.2 Problem Statement

The primary challenge is integrating AI predictions into existing clinical workflows
without increasing cognitive load on practitioners. [citation:Benchmark Paper](https://bench.example.com)
Interoperability with legacy electronic health record systems remains a key barrier.

## 2. Architecture

### 2.1 Architecture Overview

The system uses transformer-based models with domain-specific pre-training.
[citation:AI Study](https://ai.example.com)
Key architecture decisions were driven by scalability needs and latency requirements.
A microservices design ensures that individual components can be updated independently.

### 2.2 Design Decisions

Trade-offs between model accuracy and inference speed guided the architectural choices.
[citation:Benchmark Paper](https://bench.example.com)
Quantization and distillation techniques reduced model size by 40% with less than 2%
accuracy loss, enabling deployment on edge hardware in resource-constrained settings.

## 3. Performance

### 3.1 Performance Analysis

The model achieved 94% accuracy on standard benchmarks. [citation:Benchmark Paper](https://bench.example.com)
Cross-dataset evaluation confirmed robustness across three independent hospital cohorts.
F1 scores remained above 0.88 across all tested demographic subgroups.

### 3.2 Comparative Results

Comparative analysis against baseline methods showed consistent improvements.
[citation:AI Study](https://ai.example.com)
The proposed approach outperformed prior state-of-the-art by 12% on recall metrics.

## Conclusion

AI in healthcare shows strong promise based on the evidence gathered. Continued
investment in explainability and regulatory alignment will be key to broad adoption.

## Sources

- [AI Study](https://ai.example.com) - Overview of AI accuracy improvements
- [Benchmark Paper](https://bench.example.com) - Cross-domain benchmark results
"""

VALID_OUTLINE = """\
## 1. Introduction

### 1.1 Background
[sources: 1]

### 1.2 Problem Statement
[sources: 2]

## 2. Architecture

### 2.1 Architecture Overview
[sources: 1, 2]

### 2.2 Design Decisions
[sources: 3]

## 3. Performance

### 3.1 Performance Analysis
[sources: 2, 3]

### 3.2 Comparative Results
[sources: 1, 3]
"""


class TestReportValidateTool:
    def _make_runtime(self, workspace_path: str) -> SimpleNamespace:
        return SimpleNamespace(
            state={"thread_data": {"workspace_path": workspace_path}},
            context={"thread_id": "thread-test"},
        )

    @pytest.fixture(autouse=True)
    def _patch_virtual_path_resolver(self, monkeypatch, tmp_path):
        class _FakePaths:
            def resolve_virtual_path(self, thread_id: str, virtual_path: str):
                assert thread_id == "thread-test"
                prefix = "/mnt/user-data/"
                assert virtual_path.startswith(prefix)
                relative = virtual_path[len(prefix):]
                return (tmp_path / relative).resolve()

        monkeypatch.setattr(research_tools, "get_paths", lambda: _FakePaths())

    @staticmethod
    def _virtual_report_path(filename: str = "report.md") -> str:
        return f"/mnt/user-data/outputs/{filename}"

    @staticmethod
    def _write_report(tmp_path, content: str, filename: str = "report.md"):
        report_path = tmp_path / "outputs" / filename
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content)
        return report_path

    def test_pass_on_valid_report(self, tmp_path):
        (tmp_path / "outline.md").write_text(VALID_OUTLINE)
        self._write_report(tmp_path, VALID_REPORT)

        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result == "PASS — proceed to report_reviewer"

    def test_fail_on_missing_file(self, tmp_path):
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path("nonexistent.md")
        )
        assert result.startswith("FAIL")
        assert "does not exist" in result

    def test_fail_on_word_count_below_100(self, tmp_path):
        self._write_report(tmp_path, "## Introduction\n\nToo short.\n\n## Sources\n\n- [x](y)\n")
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result.startswith("FAIL")
        assert "insufficient content" in result

    def test_fail_on_missing_sources_section(self, tmp_path):
        self._write_report(
            tmp_path,
            "## Introduction\n\n" + ("word " * 110) + "\n\n"
            "## Architecture\n\n" + ("word " * 50) + "\n"
        )
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result.startswith("FAIL")
        assert "Sources" in result

    def test_fail_on_empty_body_section(self, tmp_path):
        self._write_report(
            tmp_path,
            "## Introduction\n\n" + ("word " * 50) + "\n\n"
            "### Architecture\n\n"
            "## Conclusion\n\n" + ("word " * 30) + "\n\n"
            "## Sources\n\n- [Title](https://example.com)\n"
        )
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result.startswith("FAIL")
        assert "empty body" in result

    def test_fail_on_sources_marker_in_report(self, tmp_path):
        report_with_marker = VALID_REPORT + "\n### Extra Section\n[sources: 4, 5]\nSome content here.\n"
        self._write_report(tmp_path, report_with_marker)
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result.startswith("FAIL")
        assert "[sources:" in result

    def test_fail_on_section_count_below_threshold(self, tmp_path):
        # Outline has 6 leaf sections, report only has 1 content section → < 70%
        big_outline = "\n".join(
            f"### {i}.1 Section {i}\n[sources: 1]\n" for i in range(1, 7)
        )
        (tmp_path / "outline.md").write_text(big_outline)
        self._write_report(
            tmp_path,
            "## Introduction\n\n" + ("word " * 50) + "\n\n"
            "## Only One Section\n\n" + ("word " * 100) + "\n\n"
            "## Conclusion\n\n" + ("word " * 30) + "\n\n"
            "## Sources\n\n- [T](https://x.com)\n"
        )
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result.startswith("FAIL")
        assert "missing" in result.lower() or "content section" in result.lower()

    def test_no_section_count_check_for_small_outline(self, tmp_path):
        # Outline has 4 leaf sections (< 5), section count check must not trigger
        small_outline = "\n".join(
            f"### {i}.1 Section {i}\n[sources: 1]\n" for i in range(1, 5)
        )
        (tmp_path / "outline.md").write_text(small_outline)
        # Report only has 1 content section — would trigger check if N >= 5
        self._write_report(
            tmp_path,
            "## Introduction\n\n" + ("word " * 50) + "\n\n"
            "## One Section\n\n" + ("word " * 100) + "\n\n"
            "## Conclusion\n\n" + ("word " * 30) + "\n\n"
            "## Sources\n\n- [T](https://x.com)\n"
        )
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        # Should pass — small outline does not trigger section count check
        assert result == "PASS — proceed to report_reviewer"

    def test_pass_message_is_single_line(self, tmp_path):
        (tmp_path / "outline.md").write_text(VALID_OUTLINE)
        self._write_report(tmp_path, VALID_REPORT)
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert "\n" not in result.strip()

    def test_pass_when_heading_names_differ_but_count_sufficient(self, tmp_path):
        # Outline has 5 sections, report has 5 content sections with different names
        outline = "\n".join(
            f"### {i}.1 Original Name {i}\n[sources: 1]\n" for i in range(1, 6)
        )
        (tmp_path / "outline.md").write_text(outline)

        sections_text = "".join(
            f"### Renamed Section {i}\n\n" + ("word " * 40) + "\n\n"
            for i in range(1, 6)
        )
        self._write_report(
            tmp_path,
            "## Introduction\n\n" + ("word " * 50) + "\n\n"
            + sections_text
            + "## Conclusion\n\n" + ("word " * 30) + "\n\n"
            "## Sources\n\n- [T](https://x.com)\n"
        )
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result == "PASS — proceed to report_reviewer"

    def test_informational_only_in_fail_message(self, tmp_path):
        # Section count triggers FAIL → informational should appear
        big_outline = "\n".join(
            f"### {i}.1 Unique Section Name {i}\n[sources: 1]\n" for i in range(1, 7)
        )
        (tmp_path / "outline.md").write_text(big_outline)
        self._write_report(
            tmp_path,
            "## Introduction\n\n" + ("word " * 50) + "\n\n"
            "## One Content Section\n\n" + ("word " * 100) + "\n\n"
            "## Sources\n\n- [T](https://x.com)\n"
        )
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path=self._virtual_report_path()
        )
        assert result.startswith("FAIL")
        assert "[informational]" in result

    def test_resolves_virtual_path(self, tmp_path):
        """report_validate resolves /mnt/user-data/... virtual paths to host paths."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "outline.md").write_text(VALID_OUTLINE)
        self._write_report(tmp_path, VALID_REPORT)

        rt = self._make_runtime(str(workspace))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path="/mnt/user-data/outputs/report.md"
        )
        assert result == "PASS — proceed to report_reviewer"

    def test_rejects_non_outputs_virtual_path(self, tmp_path):
        (tmp_path / "outline.md").write_text(VALID_OUTLINE)
        rt = self._make_runtime(str(tmp_path))
        result = research_tools.report_validate_tool.func(
            runtime=rt, file_path="/mnt/user-data/workspace/report.md"
        )
        assert result.startswith("FAIL")
        assert "only accepts file paths under /mnt/user-data/outputs/" in result


# ---------------------------------------------------------------------------
# _get_workspace_path fallback
# ---------------------------------------------------------------------------


class TestWorkspacePath:
    def test_fallback_when_no_runtime(self):
        path = research_tools._get_workspace_path(None)
        assert str(path) == "/mnt/user-data/workspace"

    def test_fallback_when_no_thread_data(self):
        rt = SimpleNamespace(state={})
        path = research_tools._get_workspace_path(rt)
        assert str(path) == "/mnt/user-data/workspace"

    def test_uses_runtime_workspace(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        path = research_tools._get_workspace_path(rt)
        assert path == tmp_path
