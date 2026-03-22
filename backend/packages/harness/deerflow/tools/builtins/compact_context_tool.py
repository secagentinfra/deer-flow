"""Context compression tool for phase transitions in deep research."""

import json
import logging
from pathlib import Path
from typing import Annotated

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState
from deerflow.config.summarization_config import get_summarization_config
from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)

RESEARCH_SUMMARY_PROMPT = """\
You are summarizing the research phase of a deep-research session to prepare
for the writing phase. The full evidence and outline are persisted on disk —
your summary should capture the RESEARCH CONTEXT, not duplicate raw data.

Produce a structured summary with these sections:

1. **Research Question**: The user's original query and intent
2. **Research Progress**: Total iterations completed, total sources collected,
   total queries executed
3. **Outline Overview**: Top-level section titles with source count per section
4. **Key Findings**: 3-5 most significant discoveries or patterns across all evidence
5. **Resolved Gaps**: What knowledge gaps were identified and how they were filled
6. **Unresolved Limitations**: Any remaining gaps or caveats the writer should note
7. **Completion Assessment**: Why research was deemed complete (or forced by cap)

Messages to summarize:
{messages}
"""

WRITER_KICKOFF_TEMPLATE = """\
## Phase Transition: Research to Writing

The research phase is complete. The conversation history above is a compressed
summary of {research_iterations} research iterations with {total_sources} sources.

All evidence is persisted in the workspace:
- **Outline**: `{outline_path}` (read this first)
- **Evidence bank**: `{evidence_path}` (use evidence_retrieve for each section)
- **Research state**: `{research_state_path}` (iteration history, for reference only)

**Your task now**: Write the research report following the outline structure.
Do NOT search for more information. Do NOT call web_search or web_fetch.
Proceed directly to Phase 2 (Hierarchical Writing) as defined in your skill instructions.
"""


def _get_workspace_path(runtime: ToolRuntime[ContextT, ThreadState]) -> str:
    """Extract workspace path from runtime state."""
    if runtime.state:
        thread_data = runtime.state.get("thread_data")
        if thread_data:
            wp = thread_data.get("workspace_path")
            if wp:
                return wp
    return "/mnt/user-data/workspace"


def _read_research_metadata(workspace: str) -> dict:
    """Read research_state.json and evidence_bank.json to extract metadata."""
    ws = Path(workspace)
    metadata: dict = {
        "research_iterations": 0,
        "total_sources": 0,
        "total_queries": 0,
    }

    state_path = ws / "research_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            metadata["research_iterations"] = state.get("research_iterations", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    evidence_path = ws / "evidence_bank.json"
    if evidence_path.exists():
        try:
            bank = json.loads(evidence_path.read_text(encoding="utf-8"))
            metadata["total_sources"] = len(bank.get("page_info", []))
            metadata["total_queries"] = len(bank.get("executed_queries", []))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    return metadata


@tool("compact_context", parse_docstring=True)
def compact_context_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    reason: str,
) -> Command:
    """Compress conversation history for phase transition.

    Call this when transitioning from research to writing to summarize
    accumulated tool call history and inject writing context.

    Args:
        reason: Why compression is needed (e.g., "Research phase complete").
    """
    try:
        messages = runtime.state.get("messages", [])
        workspace = _get_workspace_path(runtime)
        metadata = _read_research_metadata(workspace)

        # Build summarization model — reuse SummarizationMiddleware config model if set,
        # otherwise fall back to the default model.
        config = get_summarization_config()
        model = create_chat_model(name=config.model_name, thinking_enabled=False)

        # Instantiate middleware solely to reuse its _create_summary method.
        # No trigger is needed (we're calling explicitly, not via before_model).
        # trim_tokens_to_summarize=None passes all messages to the summary model;
        # if the model's context is exceeded the exception is caught below.
        middleware = SummarizationMiddleware(
            model=model,
            summary_prompt=RESEARCH_SUMMARY_PROMPT,
            trim_tokens_to_summarize=None,
        )
        summary_text = middleware._create_summary(messages)  # noqa: SLF001

        kickoff_text = WRITER_KICKOFF_TEMPLATE.format(
            research_iterations=metadata["research_iterations"],
            total_sources=metadata["total_sources"],
            outline_path=f"{workspace}/outline.md",
            evidence_path=f"{workspace}/evidence_bank.json",
            research_state_path=f"{workspace}/research_state.json",
        )

        logger.info(
            "[compact_context] Compressing %d messages (reason: %s). "
            "Research: %d iterations, %d sources.",
            len(messages),
            reason,
            metadata["research_iterations"],
            metadata["total_sources"],
        )

        return Command(
            update={
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    HumanMessage(content=summary_text),
                    HumanMessage(content=kickoff_text),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": tool_call_id,
                                "name": "compact_context",
                                "args": {"reason": reason},
                            }
                        ],
                    ),
                    ToolMessage(
                        content=(
                            f"Context compressed successfully. "
                            f"{metadata['research_iterations']} iterations, "
                            f"{metadata['total_sources']} sources summarized."
                        ),
                        tool_call_id=tool_call_id,
                    ),
                ],
            }
        )

    except Exception as e:
        logger.error("[compact_context] Failed: %s", e, exc_info=True)
        # Degraded path: return only a ToolMessage without REMOVE_ALL_MESSAGES so
        # that the existing message list is untouched and the agent can continue.
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"compact_context failed: {e}. Proceed to writing with full history.",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
