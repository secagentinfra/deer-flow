"""Tests for Deep Research v2 research tools (Phase 2.6: LLM Reflection + coverage refactor)."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_bare_json(self):
        text = '{"research_complete": true, "section_gaps": {}}'
        result = research_tools._extract_json(text)
        assert result == {"research_complete": True, "section_gaps": {}}

    def test_json_in_code_fence(self):
        text = '```json\n{"research_complete": false}\n```'
        result = research_tools._extract_json(text)
        assert result == {"research_complete": False}

    def test_json_in_answer_tags(self):
        text = '<answer>{"research_complete": true}</answer>'
        result = research_tools._extract_json(text)
        assert result == {"research_complete": True}

    def test_json_with_surrounding_text(self):
        text = 'Here is my analysis:\n{"research_complete": false, "reasoning": "gaps remain"}\nEnd.'
        result = research_tools._extract_json(text)
        assert result is not None
        assert result["research_complete"] is False

    def test_invalid_content_returns_none(self):
        assert research_tools._extract_json("This is not JSON at all") is None

    def test_empty_string_returns_none(self):
        assert research_tools._extract_json("") is None


# ---------------------------------------------------------------------------
# _extract_user_query
# ---------------------------------------------------------------------------


class TestExtractUserQuery:
    def test_extracts_human_message(self):
        msg = SimpleNamespace(type="human", content="Research quantum computing trends")
        rt = SimpleNamespace(state={"messages": [msg]})
        assert research_tools._extract_user_query(rt) == "Research quantum computing trends"

    def test_truncates_long_query(self):
        msg = SimpleNamespace(type="human", content="x" * 600)
        rt = SimpleNamespace(state={"messages": [msg]})
        result = research_tools._extract_user_query(rt)
        assert len(result) == 500

    def test_returns_fallback_when_no_messages(self):
        rt = SimpleNamespace(state={})
        assert "unknown" in research_tools._extract_user_query(rt)

    def test_returns_fallback_when_no_human_message(self):
        msg = SimpleNamespace(type="ai", content="Hello")
        rt = SimpleNamespace(state={"messages": [msg]})
        assert "unknown" in research_tools._extract_user_query(rt)

    def test_returns_fallback_when_none(self):
        assert "unknown" in research_tools._extract_user_query(None)


# ---------------------------------------------------------------------------
# evidence_store_tool
# ---------------------------------------------------------------------------


class TestEvidenceStore:
    def test_stores_new_entry(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/page1",
            summary="Relevant AI paper.",
            evidence="Key finding: AI improves diagnosis accuracy by 30%.",
            goal="AI healthcare applications",
        )
        assert "id_1" in result
        assert "Total sources: 1" in result

        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert mb["url2id"]["https://example.com/page1"] == 1
        assert len(mb["page_info"]) == 1
        assert mb["page_info"][0]["summary"] == "Relevant AI paper."

    def test_rejects_duplicate_url(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/dup",
            summary="First entry.",
            evidence="Evidence A.",
            goal="Goal A",
        )
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/dup",
            summary="Duplicate entry.",
            evidence="Evidence B.",
            goal="Goal B",
        )
        assert "already stored" in result
        assert "id_1" in result

        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert len(mb["page_info"]) == 1

    def test_increments_ids(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        for i in range(3):
            research_tools.evidence_store_tool.func(
                runtime=rt,
                url=f"https://example.com/page{i}",
                summary=f"Summary {i}",
                evidence=f"Evidence {i}",
                goal="test",
            )
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert len(mb["page_info"]) == 3
        ids = [p["id"] for p in mb["page_info"]]
        assert ids == [1, 2, 3]


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
# outline_update_tool (Phase 2.6: [sources: ...] format, no coverage %, no iteration count)
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

    def test_reminder_uses_sources_format(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.outline_update_tool.func(
            runtime=rt, outline_content="### Section 1\n",
        )
        assert "REMINDER" in result
        assert "[sources: 1, 2]" in result
        assert "<citation>" not in result
        assert "research_reflect" in result


class TestOutlineUpdateCitationValidation:
    def test_detects_invalid_citation_ids(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt, url="https://ex.com/1", summary="s", evidence="e", goal="g",
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
                runtime=rt, url=f"https://ex.com/{i}", summary="s", evidence="e", goal="g",
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
# evidence_store + evidence_retrieve reminders (Phase 2.6 updates)
# ---------------------------------------------------------------------------


class TestEvidenceStoreReminder:
    def test_return_contains_reminder_and_counts(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/r1",
            summary="Test source.",
            evidence="Evidence data.",
            goal="test",
        )
        assert "REMINDER" in result
        assert "update outline" in result
        assert "research_reflect" in result
        assert "Research iterations: 0" in result
        assert "Total sources: 1" in result

    def test_reminder_shows_research_iteration_count(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools._save_memory_bank(
            tmp_path,
            {"page_info": [], "url2id": {}, "executed_queries": [], "research_iterations": 5},
        )
        result = research_tools.evidence_store_tool.func(
            runtime=rt,
            url="https://example.com/r2",
            summary="More data.",
            evidence="Details.",
            goal="test",
        )
        assert "Research iterations: 5" in result


class TestEvidenceRetrieveReminder:
    def test_return_contains_citation_format_reminder(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        research_tools.evidence_store_tool.func(
            runtime=rt, url="https://ex.com/1", summary="s", evidence="e", goal="g",
        )
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="1")
        assert "[id_X]" in result
        assert "[sources: ...]" in result
        assert "outline-only" in result

    def test_no_reminder_when_no_results(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.evidence_retrieve_tool.func(runtime=rt, ids="99")
        assert "No evidence found" in result
        assert "REMINDER" not in result


# ---------------------------------------------------------------------------
# research_reflect_tool (Phase 2.6: LLM Reflection)
# ---------------------------------------------------------------------------


def _make_llm_response(content: dict) -> MagicMock:
    """Create a mock LLM response with JSON content."""
    mock_response = MagicMock()
    mock_response.content = json.dumps(content)
    return mock_response


def _seed_mb(tmp_path, *, sources=12, research_iterations=4):
    """Create a memory bank with the given number of sources and iterations."""
    research_tools._save_memory_bank(
        tmp_path,
        {
            "page_info": [
                {"id": i, "url": f"u{i}", "summary": f"Summary {i}", "evidence": f"Evidence {i}", "goal": "g", "status": "ok"}
                for i in range(1, sources + 1)
            ],
            "url2id": {f"u{i}": i for i in range(1, sources + 1)},
            "executed_queries": [f"q{i}" for i in range(sources)],
            "research_iterations": research_iterations,
        },
    )


class TestResearchReflect:
    def test_no_outline_returns_instruction(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        result = research_tools.research_reflect_tool.func(runtime=rt)
        assert "No outline found" in result

    def test_increments_research_iterations(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n")
        research_tools._save_memory_bank(
            tmp_path, {"page_info": [], "url2id": {}, "executed_queries": []}
        )
        with patch("deerflow.models.create_chat_model", side_effect=Exception("skip")):
            research_tools.research_reflect_tool.func(runtime=rt)
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert mb["research_iterations"] == 1

        with patch("deerflow.models.create_chat_model", side_effect=Exception("skip")):
            research_tools.research_reflect_tool.func(runtime=rt)
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert mb["research_iterations"] == 2

    def test_no_coverage_percentage_in_output(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n[sources: 1]\n### 1.2 B\n")
        _seed_mb(tmp_path, sources=2, research_iterations=0)
        with patch("deerflow.models.create_chat_model", side_effect=Exception("skip")):
            result = research_tools.research_reflect_tool.func(runtime=rt)
        assert "Coverage:" not in result
        assert "%" not in result


class TestResearchReflectLLM:
    @patch("deerflow.models.create_chat_model")
    def test_llm_complete_with_hard_gates_met(self, mock_create, tmp_path):
        mock_model = MagicMock()
        mock_model.invoke.return_value = _make_llm_response({
            "research_complete": True,
            "section_gaps": {},
            "priority_section": "",
            "knowledge_gap": "",
            "suggested_queries": [],
            "outline_evolution": "No changes needed",
            "reasoning": "All 5 dimensions scored above 90%.",
        })
        mock_create.return_value = mock_model

        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n[sources: 1]\n")
        _seed_mb(tmp_path, sources=12, research_iterations=2)

        result = research_tools.research_reflect_tool.func(runtime=rt)
        assert "Proceed to writing" in result
        assert "⛔" not in result

    @patch("deerflow.models.create_chat_model")
    def test_llm_complete_but_hard_gates_unmet(self, mock_create, tmp_path):
        """LLM says complete but iterations < 3 → still blocked."""
        mock_model = MagicMock()
        mock_model.invoke.return_value = _make_llm_response({
            "research_complete": True,
            "section_gaps": {},
            "priority_section": "",
            "knowledge_gap": "",
            "suggested_queries": [],
            "outline_evolution": "No changes needed",
            "reasoning": "Looks good.",
        })
        mock_create.return_value = mock_model

        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n[sources: 1]\n")
        _seed_mb(tmp_path, sources=5, research_iterations=0)

        result = research_tools.research_reflect_tool.func(runtime=rt)
        assert "⛔ DO NOT PROCEED TO WRITING" in result

    @patch("deerflow.models.create_chat_model")
    def test_llm_not_complete_with_suggestions(self, mock_create, tmp_path):
        mock_model = MagicMock()
        mock_model.invoke.return_value = _make_llm_response({
            "research_complete": False,
            "section_gaps": {"Performance": "No benchmark data"},
            "priority_section": "Performance",
            "knowledge_gap": "Missing latency and throughput metrics",
            "suggested_queries": [
                "system X benchmark latency throughput 2025",
                "system X vs Y performance comparison",
            ],
            "outline_evolution": "Add subsection ### 3.3 Performance Benchmarks",
            "reasoning": "Core mechanisms well covered but empirical data dimension at 40%.",
        })
        mock_create.return_value = mock_model

        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n[sources: 1]\n")
        _seed_mb(tmp_path, sources=12, research_iterations=2)

        result = research_tools.research_reflect_tool.func(runtime=rt)
        assert "Suggested queries" in result
        assert "benchmark" in result.lower()
        assert "Outline evolution" in result
        assert "Performance Benchmarks" in result
        assert "Semantic gaps" in result
        assert "Priority focus" in result

    @patch("deerflow.models.create_chat_model")
    def test_llm_not_complete_no_outline_evolution(self, mock_create, tmp_path):
        """outline_evolution = 'No changes needed' should not appear in output."""
        mock_model = MagicMock()
        mock_model.invoke.return_value = _make_llm_response({
            "research_complete": False,
            "section_gaps": {"Limits": "Missing"},
            "priority_section": "Limits",
            "knowledge_gap": "No failure modes",
            "suggested_queries": ["system X failure modes"],
            "outline_evolution": "No changes needed",
            "reasoning": "Gaps remain.",
        })
        mock_create.return_value = mock_model

        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n[sources: 1]\n")
        _seed_mb(tmp_path, sources=12, research_iterations=2)

        result = research_tools.research_reflect_tool.func(runtime=rt)
        assert "Outline evolution" not in result

    @patch("deerflow.models.create_chat_model")
    def test_user_query_injected_into_prompt(self, mock_create, tmp_path):
        mock_model = MagicMock()
        mock_model.invoke.return_value = _make_llm_response({
            "research_complete": False,
            "section_gaps": {},
            "priority_section": "",
            "knowledge_gap": "",
            "suggested_queries": [],
            "outline_evolution": "",
            "reasoning": "",
        })
        mock_create.return_value = mock_model

        msg = SimpleNamespace(type="human", content="Research quantum computing trends 2026")
        rt = _make_runtime(str(tmp_path), messages=[msg])
        (tmp_path / "outline.md").write_text("### 1.1 A\n")
        _seed_mb(tmp_path, sources=2, research_iterations=0)

        research_tools.research_reflect_tool.func(runtime=rt)

        call_args = mock_model.invoke.call_args
        prompt_text = call_args[0][0]
        assert "quantum computing" in prompt_text

    def test_llm_failure_fallback(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n")
        _seed_mb(tmp_path, sources=2, research_iterations=0)

        with patch("deerflow.models.create_chat_model", side_effect=Exception("LLM unavailable")):
            result = research_tools.research_reflect_tool.func(runtime=rt)

        assert "conservative fallback" in result
        assert "⛔" in result

    def test_llm_failure_never_says_complete(self, tmp_path):
        """Even with hard gates met, LLM failure → conservative (not complete)."""
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n[sources: 1]\n")
        _seed_mb(tmp_path, sources=12, research_iterations=2)

        with patch("deerflow.models.create_chat_model", side_effect=Exception("fail")):
            result = research_tools.research_reflect_tool.func(runtime=rt)

        assert "Proceed to writing" not in result
        assert "conservative fallback" in result


class TestResearchReflectHardLimit:
    def test_15_iteration_hard_limit(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 Intro\n### 1.2 Body\n")
        _seed_mb(tmp_path, sources=5, research_iterations=14)

        with patch("deerflow.models.create_chat_model", side_effect=Exception("skip")):
            result = research_tools.research_reflect_tool.func(runtime=rt)

        assert "Hard limit reached" in result
        assert "Proceed to writing" in result
        assert "⛔" not in result

    def test_no_hard_limit_at_14(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 Intro\n### 1.2 Body\n")
        _seed_mb(tmp_path, sources=2, research_iterations=13)

        with patch("deerflow.models.create_chat_model", side_effect=Exception("skip")):
            result = research_tools.research_reflect_tool.func(runtime=rt)

        assert "Hard limit" not in result


class TestIterationCountInReflectNotOutline:
    def test_outline_update_does_not_change_iterations(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        for i in range(3):
            research_tools.outline_update_tool.func(
                runtime=rt, outline_content=f"### Section {i}\n"
            )
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert mb.get("research_iterations", 0) == 0

    def test_reflect_increments_iterations(self, tmp_path):
        rt = _make_runtime(str(tmp_path))
        (tmp_path / "outline.md").write_text("### 1.1 A\n")
        research_tools._save_memory_bank(
            tmp_path, {"page_info": [], "url2id": {}, "executed_queries": []}
        )
        with patch("deerflow.models.create_chat_model", side_effect=Exception("skip")):
            research_tools.research_reflect_tool.func(runtime=rt)
        mb = json.loads((tmp_path / "evidence_bank.json").read_text())
        assert mb["research_iterations"] == 1


# ---------------------------------------------------------------------------
# check_query_duplicate_tool
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
