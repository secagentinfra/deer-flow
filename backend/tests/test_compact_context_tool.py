"""Unit tests for compact_context_tool (Phase 2.8)."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command

compact_context_module = importlib.import_module(
    "deerflow.tools.builtins.compact_context_tool"
)
compact_context_tool = compact_context_module.compact_context_tool
_get_workspace_path = compact_context_module._get_workspace_path
_read_research_metadata = compact_context_module._read_research_metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(*, messages=None, workspace_path="/tmp/ws") -> SimpleNamespace:
    state: dict = {
        "thread_data": {"workspace_path": workspace_path},
        "messages": messages or [],
    }
    return SimpleNamespace(state=state, tool_call_id="call-123")


def _call_tool(runtime, *, reason="test reason"):
    """Invoke compact_context_tool bypassing LangChain tool wrapping."""
    return compact_context_tool.func(
        runtime=runtime,
        tool_call_id="call-123",
        reason=reason,
    )


# ---------------------------------------------------------------------------
# _get_workspace_path
# ---------------------------------------------------------------------------


class TestGetWorkspacePath:
    def test_reads_from_thread_data(self):
        runtime = _make_runtime(workspace_path="/custom/ws")
        assert _get_workspace_path(runtime) == "/custom/ws"

    def test_fallback_when_no_thread_data(self):
        runtime = SimpleNamespace(state={})
        assert _get_workspace_path(runtime) == "/mnt/user-data/workspace"

    def test_fallback_when_state_is_none(self):
        runtime = SimpleNamespace(state=None)
        assert _get_workspace_path(runtime) == "/mnt/user-data/workspace"


# ---------------------------------------------------------------------------
# _read_research_metadata
# ---------------------------------------------------------------------------


class TestReadResearchMetadata:
    def test_returns_zeros_for_missing_workspace(self, tmp_path):
        meta = _read_research_metadata(str(tmp_path / "nonexistent"))
        assert meta["research_iterations"] == 0
        assert meta["total_sources"] == 0
        assert meta["total_queries"] == 0

    def test_reads_research_state(self, tmp_path):
        state = {"research_iterations": 7, "reflections": [{}] * 7}
        (tmp_path / "research_state.json").write_text(json.dumps(state))
        meta = _read_research_metadata(str(tmp_path))
        assert meta["research_iterations"] == 7

    def test_reads_evidence_bank_counts(self, tmp_path):
        bank = {
            "page_info": [{"id": i} for i in range(12)],
            "executed_queries": ["q1", "q2", "q3"],
        }
        (tmp_path / "evidence_bank.json").write_text(json.dumps(bank))
        meta = _read_research_metadata(str(tmp_path))
        assert meta["total_sources"] == 12
        assert meta["total_queries"] == 3

    def test_tolerates_malformed_json(self, tmp_path):
        (tmp_path / "research_state.json").write_text("NOT JSON")
        (tmp_path / "evidence_bank.json").write_text("{broken")
        meta = _read_research_metadata(str(tmp_path))
        assert meta["research_iterations"] == 0
        assert meta["total_sources"] == 0


# ---------------------------------------------------------------------------
# compact_context_tool — success path
# ---------------------------------------------------------------------------


class TestCompactContextToolSuccess:
    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    @patch(
        "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
    )
    def test_returns_command(self, mock_sm_class, mock_create_model, tmp_path):
        mock_model = MagicMock()
        mock_create_model.return_value = mock_model

        mock_middleware = MagicMock()
        mock_middleware._create_summary.return_value = "Summary text"
        mock_sm_class.return_value = mock_middleware

        runtime = _make_runtime(
            messages=[HumanMessage(content="hello")],
            workspace_path=str(tmp_path),
        )
        result = _call_tool(runtime)

        assert isinstance(result, Command)

    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    @patch(
        "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
    )
    def test_messages_structure(self, mock_sm_class, mock_create_model, tmp_path):
        mock_create_model.return_value = MagicMock()

        mock_middleware = MagicMock()
        mock_middleware._create_summary.return_value = "Research summary here"
        mock_sm_class.return_value = mock_middleware

        runtime = _make_runtime(
            messages=[HumanMessage(content="research task")],
            workspace_path=str(tmp_path),
        )
        result = _call_tool(runtime)

        msgs = result.update["messages"]
        # Must start with REMOVE_ALL_MESSAGES sentinel
        assert isinstance(msgs[0], RemoveMessage)
        assert msgs[0].id == REMOVE_ALL_MESSAGES
        # Must have at least two HumanMessages (summary + kickoff)
        human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]
        assert len(human_msgs) >= 2
        # Must end with a ToolMessage
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1

    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    @patch(
        "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
    )
    def test_kickoff_contains_workspace_paths(
        self, mock_sm_class, mock_create_model, tmp_path
    ):
        mock_create_model.return_value = MagicMock()

        mock_middleware = MagicMock()
        mock_middleware._create_summary.return_value = "summary"
        mock_sm_class.return_value = mock_middleware

        runtime = _make_runtime(workspace_path=str(tmp_path))
        result = _call_tool(runtime)

        kickoff_msg = result.update["messages"][2]  # summary, kickoff, tool
        assert isinstance(kickoff_msg, HumanMessage)
        assert "outline.md" in kickoff_msg.content
        assert "evidence_retrieve" in kickoff_msg.content

    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    @patch(
        "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
    )
    def test_metadata_in_kickoff(self, mock_sm_class, mock_create_model, tmp_path):
        mock_create_model.return_value = MagicMock()
        mock_middleware = MagicMock()
        mock_middleware._create_summary.return_value = "summary"
        mock_sm_class.return_value = mock_middleware

        state = {"research_iterations": 5}
        (tmp_path / "research_state.json").write_text(json.dumps(state))
        bank = {"page_info": [{}] * 8, "executed_queries": []}
        (tmp_path / "evidence_bank.json").write_text(json.dumps(bank))

        runtime = _make_runtime(workspace_path=str(tmp_path))
        result = _call_tool(runtime)

        kickoff = result.update["messages"][2].content
        assert "5" in kickoff   # research_iterations
        assert "8" in kickoff   # total_sources

    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    @patch(
        "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
    )
    def test_works_with_empty_messages(self, mock_sm_class, mock_create_model, tmp_path):
        mock_create_model.return_value = MagicMock()
        mock_middleware = MagicMock()
        mock_middleware._create_summary.return_value = "No previous history."
        mock_sm_class.return_value = mock_middleware

        runtime = _make_runtime(messages=[], workspace_path=str(tmp_path))
        result = _call_tool(runtime)

        assert isinstance(result, Command)
        msgs = result.update["messages"]
        assert isinstance(msgs[0], RemoveMessage)


# ---------------------------------------------------------------------------
# compact_context_tool — degraded path (failure)
# ---------------------------------------------------------------------------


class TestCompactContextToolDegradedPath:
    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    @patch(
        "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
    )
    def test_on_failure_returns_command_without_remove(
        self, mock_sm_class, mock_create_model, tmp_path
    ):
        mock_create_model.return_value = MagicMock()
        mock_middleware = MagicMock()
        mock_middleware._create_summary.side_effect = RuntimeError("model failure")
        mock_sm_class.return_value = mock_middleware

        runtime = _make_runtime(workspace_path=str(tmp_path))
        result = _call_tool(runtime)

        assert isinstance(result, Command)
        msgs = result.update["messages"]
        # Must NOT contain RemoveMessage — messages list is untouched
        remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
        assert len(remove_msgs) == 0
        # Must contain a ToolMessage with error info
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "compact_context failed" in tool_msgs[0].content

    @patch("deerflow.tools.builtins.compact_context_tool.create_chat_model")
    def test_on_model_creation_failure_returns_command(
        self, mock_create_model, tmp_path
    ):
        mock_create_model.side_effect = ValueError("no models configured")

        runtime = _make_runtime(workspace_path=str(tmp_path))
        result = _call_tool(runtime)

        assert isinstance(result, Command)
        msgs = result.update["messages"]
        remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
        assert len(remove_msgs) == 0
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1


# ---------------------------------------------------------------------------
# Phase 3: Writing Protocol in kickoff
# ---------------------------------------------------------------------------


class TestKickoffWritingProtocol:
    """Verify the WRITER_KICKOFF_TEMPLATE contains the Writing Protocol (Phase 3)."""

    def _get_kickoff(self, tmp_path) -> str:
        """Helper: run the tool and return kickoff message content."""
        with (
            patch(
                "deerflow.tools.builtins.compact_context_tool.create_chat_model"
            ) as mock_create_model,
            patch(
                "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
            ) as mock_sm_class,
        ):
            mock_create_model.return_value = MagicMock()
            mock_middleware = MagicMock()
            mock_middleware._create_summary.return_value = "Research summary"
            mock_sm_class.return_value = mock_middleware

            runtime = _make_runtime(workspace_path=str(tmp_path))
            result = _call_tool(runtime)

        kickoff_msgs = [
            m
            for m in result.update["messages"]
            if isinstance(m, HumanMessage) and "Writing Protocol" in m.content
        ]
        assert len(kickoff_msgs) == 1, "Expected exactly one kickoff HumanMessage with 'Writing Protocol'"
        return kickoff_msgs[0].content

    def test_kickoff_contains_writing_protocol(self, tmp_path):
        kickoff = self._get_kickoff(tmp_path)

        # Per-section workflow constraints (most critical behavioral fix)
        assert "For EACH section" in kickoff
        assert "one call per section" in kickoff
        assert "do NOT batch" in kickoff

        # Quality requirements
        assert "≥2 paragraphs" in kickoff
        assert "table" in kickoff.lower()

        # Correct tool name
        assert "present_files" in kickoff

        # No dangling reference to lost SKILL.md
        assert "as defined in your skill instructions" not in kickoff

    def test_kickoff_prohibits_web_search(self, tmp_path):
        kickoff = self._get_kickoff(tmp_path)
        assert "Do NOT call `web_search`" in kickoff or "Do NOT call web_search" in kickoff


# ---------------------------------------------------------------------------
# Phase 5.1: original_query injection and language instruction
# ---------------------------------------------------------------------------


class TestPhase51OriginalQueryInjection:
    """Verify original_query is extracted and language instruction appears in kickoff."""

    def _run_tool(self, tmp_path, messages):
        with (
            patch(
                "deerflow.tools.builtins.compact_context_tool.create_chat_model"
            ) as mock_create_model,
            patch(
                "deerflow.tools.builtins.compact_context_tool.SummarizationMiddleware"
            ) as mock_sm_class,
        ):
            mock_create_model.return_value = MagicMock()
            mock_middleware = MagicMock()
            mock_middleware._create_summary.return_value = "Research summary"
            mock_sm_class.return_value = mock_middleware

            runtime = _make_runtime(messages=messages, workspace_path=str(tmp_path))
            return _call_tool(runtime)

    def _get_kickoff(self, result) -> str:
        kickoff_msgs = [
            m
            for m in result.update["messages"]
            if isinstance(m, HumanMessage) and "Writing Protocol" in m.content
        ]
        assert len(kickoff_msgs) == 1
        return kickoff_msgs[0].content

    def test_kickoff_contains_original_query(self, tmp_path):
        messages = [HumanMessage(content="深度研究：量子计算在密码学中的应用")]
        result = self._run_tool(tmp_path, messages)
        kickoff = self._get_kickoff(result)
        assert "深度研究：量子计算在密码学中的应用" in kickoff

    def test_kickoff_contains_language_instruction(self, tmp_path):
        messages = [HumanMessage(content="research quantum computing")]
        result = self._run_tool(tmp_path, messages)
        kickoff = self._get_kickoff(result)
        assert "Write the entire report in the same language as the Original Research Query above." in kickoff

    def test_kickoff_extracts_first_human_message(self, tmp_path):
        messages = [
            HumanMessage(content="first query"),
            HumanMessage(content="second query"),
        ]
        result = self._run_tool(tmp_path, messages)
        kickoff = self._get_kickoff(result)
        assert "first query" in kickoff
        assert "second query" not in kickoff

    def test_no_crash_when_no_human_messages(self, tmp_path):
        """original_query gracefully degrades to empty string with no HumanMessage."""
        messages = []
        result = self._run_tool(tmp_path, messages)
        assert isinstance(result, Command)
        kickoff = self._get_kickoff(result)
        assert "Original Research Query" in kickoff

    def test_skips_empty_human_messages(self, tmp_path):
        messages = [
            HumanMessage(content=""),
            HumanMessage(content="   "),
            HumanMessage(content="actual query"),
        ]
        result = self._run_tool(tmp_path, messages)
        kickoff = self._get_kickoff(result)
        assert "actual query" in kickoff
