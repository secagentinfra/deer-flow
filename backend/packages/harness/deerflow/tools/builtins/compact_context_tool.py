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
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
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

**Original Research Query**: {original_query}

You are now in the **Writing Phase** of the Deep Research methodology.
Write the entire report in the same language as the Original Research Query above.
The conversation history above is a compressed summary of {research_iterations}
research iterations with {total_sources} sources.

All evidence is persisted in the workspace:
- **Outline**: `{outline_path}` (read this first)
- **Evidence**: use `evidence_retrieve` with the source IDs listed in the outline

### Writing Protocol

**Report structure rule:**
- Follow the outline's heading hierarchy: `##` for chapters, `###` for sections within them.
- Chapters with subsections in the outline should NOT have loose content above the first `###`.
- Short chapters (e.g., Introduction, Conclusion) may contain content directly under `##`.

**Step 1 — Initialize the report:**
1. Choose a report file path under `/mnt/user-data/outputs/`
2. Write the report title (`# Title`) and Introduction to the file using `write_file`
   - Base the Introduction on the research summary above (key themes, scope, structure preview)

**Step 2 — Write sections incrementally:**

For EACH section in the outline, in order:
1. Read the `[sources: <ID>, ...]` line below the section heading
2. Call `evidence_retrieve` with those source IDs — one call per section, do NOT batch all sources in one call
3. Append the section to the report file **immediately** using `write_file(append=True)` — do NOT retrieve multiple sections before writing.
   - Include the `##` chapter heading when starting a new chapter
   - Include the `###` section heading, full section body, and inline citations
   - Use `[citation:Title](URL)` inline citations with the Title and URL from each `<source>` block
4. Move to the next section and repeat

**Step 3 — Finalize the report:**
1. Append Conclusion (key takeaways) and Sources section (`- [Title](URL) - brief description`) to the report file using `write_file(append=True)`
2. Call `report_validate` with the report file path — fix any issues it reports, then call again until PASS
3. Call `task(subagent_type="report_reviewer", prompt="Review and improve the research report at <report_path> for: <original_query>")` — if this call fails or times out, proceed directly to step 4
4. Call `present_files` to deliver the report

**Writing style — make every word tell:**
- **Active voice**: "The study revealed..." not "It was revealed by the study..."
- **Positive, definite assertions**: "Growth slowed to 2%" not "Growth was not very strong"
- **Concrete language**: Prefer specific data, names, and numbers over vague abstractions
- **Omit needless words**: Cut filler ("the fact that", "it is worth noting that", "in order to"). A sentence should contain no unnecessary words, a paragraph no unnecessary sentences.
- **Topic sentences**: Open each paragraph with its central claim; develop with evidence, close with significance
- **Parallel construction**: Express comparable ideas in matching grammatical form (tables, lists, comparisons)
- **Emphatic endings**: Place the key insight at the end of each sentence and paragraph

**Quality requirements:**
- Cite EVERY factual statement — no uncited claims
- Each section: ≥2 paragraphs of analysis (not shallow enumeration)
- Include ≥2 comparative or summary tables with post-table analysis
- Analyze WHY findings matter, not just WHAT they are

**Prohibitions:**
- Do NOT call `web_search` or `web_fetch`
- Do NOT include `[sources: ...]` lines in the report — those are outline-only markers
- Do NOT accumulate sections in chat and combine later — write each section directly to the file
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

        original_query = ""
        for msg in messages:
            if isinstance(msg, HumanMessage) and msg.content:
                text = msg.content if isinstance(msg.content, str) else str(msg.content)
                if text.strip():
                    original_query = text.strip()
                    break

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
            outline_path=f"{VIRTUAL_PATH_PREFIX}/workspace/outline.md",
            original_query=original_query,
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
                        content=(
                            f"compact_context failed: {e}. "
                            "Proceed to writing using the full conversation history. "
                            "Follow the Phase 2 (Hierarchical Writing) and Phase 3 (Report Finalization) "
                            "steps from the deep-research skill instructions in your context."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
