"""Research tools for Deep Research v2: Memory Bank, Outline Management, and LLM Reflection."""

import json
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState

_mb_lock = threading.Lock()


REFLECTION_PROMPT = """You are a research completeness evaluator analyzing research about: {user_query}

## Current Outline (with source annotations)
{outline}

## Source Summaries
{summaries}

## Context
- Research iteration: {iteration}
- Total unique sources: {total_sources}

## Task 1: Semantic Completeness Assessment

Assess research completeness using these quantitative criteria (assign 0-100% to each):

1. **Core mechanisms / components** (weight: critical):
   - 90-100%: Comprehensive with specific details and examples
   - 70-89%: Substantial but missing some specifics
   - 40-69%: Basic overview only
   - 0-39%: Minimal or missing
2. **Empirical data / benchmarks**: Quantitative data, metrics, case studies?
3. **Comparative analysis**: Alternatives, tradeoffs, competing approaches?
4. **Limitations / failure modes**: Weaknesses, constraints, open challenges?
5. **Timeliness**: Information current and from recent sources?

Research is "complete" ONLY when average coverage exceeds 90% AND no critical dimension falls below 70%.

IMPORTANT: Err on the side of setting research_complete to false if there is ANY doubt about thoroughness. When in doubt, continue researching.

## Task 2: Outline Evolution Analysis

Examine whether the current evidence reveals topics NOT yet in the outline:
- Do any source summaries discuss aspects not represented as outline sections?
- Are any outline sections redundant or overlapping?
- Should any section be split into more specific subsections?
- Does the outline structure match the natural structure of the topic?
- Have search efforts been too narrow (concentrated on few angles)?

## Task 3: Next Search Direction

If research is not complete, suggest 2-3 specific search queries targeting the most pressing gaps.
Each query should be specific (5-10 key terms), not generic.
Queries MUST relate to the original research topic: {user_query}
NEVER suggest queries that would lead research away from the original topic.

## Required Output
Return a JSON object (no markdown code fences):
{{
  "research_complete": true or false,
  "section_gaps": {{"Section Name": "Brief gap description"}},
  "priority_section": "The section name with the most pressing gap",
  "knowledge_gap": "What specific information or angle is most needed next",
  "suggested_queries": ["specific targeted query 1", "specific targeted query 2"],
  "outline_evolution": "Natural language suggestions for outline changes: new sections to add, sections to merge/split, restructuring advice. Write 'No changes needed' if outline is well-structured.",
  "reasoning": "2-3 sentence overall assessment"
}}
"""


def _get_workspace_path(runtime: ToolRuntime[ContextT, ThreadState] | None) -> Path:
    """Get workspace path from runtime state."""
    if runtime:
        thread_data = runtime.state.get("thread_data")
        if thread_data:
            wp = thread_data.get("workspace_path")
            if wp:
                return Path(wp)
    return Path("/mnt/user-data/workspace")


def _load_memory_bank(workspace: Path) -> dict:
    """Load or initialize the memory bank."""
    mb_path = workspace / "evidence_bank.json"
    if mb_path.exists():
        try:
            return json.loads(mb_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"page_info": [], "url2id": {}, "executed_queries": []}
    return {"page_info": [], "url2id": {}, "executed_queries": []}


def _save_memory_bank(workspace: Path, mb: dict):
    """Persist the memory bank."""
    mb_path = workspace / "evidence_bank.json"
    mb_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = mb_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(mb, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp_path.replace(mb_path)


def _parse_outline(outline: str) -> dict[str, list[int]]:
    """Parse outline, return {heading_text: [cited_source_ids]}.

    Only supports [sources: ...] format on a line following a heading.
    Only counts ### and #### headings (excludes ## container headings).
    Allows intermediate lines (description text, blank lines) between
    a heading and its [sources: ...] annotation.
    """
    result: dict[str, list[int]] = {}
    current_heading: str | None = None
    for line in outline.splitlines():
        stripped = line.strip()
        if re.match(r"^#{3,4}\s+", stripped):
            current_heading = stripped
            result[current_heading] = []
        elif current_heading:
            match = re.match(r"^\[sources:\s*([\d,\s]+)\]$", stripped)
            if match:
                ids = [int(x.strip()) for x in match.group(1).split(",")
                       if x.strip().isdigit()]
                result[current_heading] = ids
                current_heading = None
    return result


def _extract_user_query(runtime: ToolRuntime[ContextT, ThreadState] | None) -> str:
    """Extract the user's original query from runtime state messages."""
    if runtime and runtime.state.get("messages"):
        for msg in runtime.state["messages"]:
            if hasattr(msg, "type") and msg.type == "human":
                return str(msg.content)[:500]
    return "(unknown research topic)"


def _extract_json(text: str) -> dict | None:
    """Robustly extract JSON from LLM response, handling fences and surrounding text."""
    # Try 1: Extract from <answer> tags (EDR pattern)
    match = re.search(r"<answer>\s*(.*?)\s*</answer>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try 2: Extract from markdown code fences
    match = re.search(r"```(?:json)?\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try 3: Find first {...} JSON block
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # Try 4: Parse entire content as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@tool("evidence_store", parse_docstring=True)
def evidence_store_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    url: str,
    summary: str,
    evidence: str,
    goal: str,
) -> str:
    """Store extracted evidence in the Memory Bank. Use after fetching and analyzing a web page.

    The summary is kept short (1-2 sentences) for the Planner's context.
    The evidence contains detailed extracted quotes, data, and analysis for the Writer.

    Args:
        url: The source URL.
        summary: A short 1-2 sentence summary of the page's relevance.
        evidence: Detailed extracted evidence (key quotes, data points, analysis).
        goal: The search goal this evidence relates to.
    """
    workspace = _get_workspace_path(runtime)
    with _mb_lock:
        mb = _load_memory_bank(workspace)

        if url in mb["url2id"]:
            existing_id = mb["url2id"][url]
            return f"URL already stored as id_{existing_id}. No duplicate stored."

        new_id = len(mb["url2id"]) + 1
        mb["url2id"][url] = new_id
        mb["page_info"].append({
            "id": new_id,
            "url": url,
            "goal": goal,
            "summary": summary,
            "evidence": evidence,
            "status": "successful",
        })
        _save_memory_bank(workspace, mb)
        total_sources = len(mb["page_info"])
        research_iterations = mb.get("research_iterations", 0)
    return (
        f"Stored as id_{new_id}. Total sources: {total_sources}. "
        f"Research iterations: {research_iterations}. Summary: {summary}\n"
        "REMINDER: Next steps — update outline with this source ID, "
        "then call research_reflect to assess progress."
    )


@tool("evidence_retrieve", parse_docstring=True)
def evidence_retrieve_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    ids: str,
) -> str:
    """Retrieve evidence from the Memory Bank by citation IDs. Use when writing a report section.

    Args:
        ids: Comma-separated citation IDs to retrieve (e.g. "1,3,5" or "1, 3, 5").
    """
    workspace = _get_workspace_path(runtime)
    mb = _load_memory_bank(workspace)

    id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    id_to_url = {v: k for k, v in mb["url2id"].items()}

    results = []
    for cid in id_list:
        url = id_to_url.get(cid)
        if url:
            info = next((p for p in mb["page_info"] if p["url"] == url), None)
            if info:
                results.append(
                    f'<source id="{cid}">\nURL: {url}\nEvidence:\n{info["evidence"]}\n</source>'
                )

    if not results:
        return f"No evidence found for IDs: {ids}"
    body = "\n\n".join(results)
    return (
        f"{body}\n\n"
        "REMINDER: Use inline [id_X] or [id_X, id_Y] format for citations in the report. "
        "Do NOT include [sources: ...] lines in the report — those are outline-only markers."
    )


@tool("outline_update", parse_docstring=True)
def outline_update_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    outline_content: str,
) -> str:
    """Update the research outline. Mark which sources support each subsection using [sources: ...] on a separate line.

    Format example:
    ## 1. Introduction

    ### 1.1 Background
    [sources: 1, 2]

    ### 1.2 Problem Statement
    [sources: 3]

    Args:
        outline_content: The full updated outline in Markdown format with [sources: ...] annotations.
    """
    workspace = _get_workspace_path(runtime)
    outline_path = workspace / "outline.md"
    outline_path.parent.mkdir(parents=True, exist_ok=True)
    outline_path.write_text(outline_content, encoding="utf-8")

    parsed = _parse_outline(outline_content)
    total = len(parsed) if parsed else 0
    cited = sum(1 for ids in parsed.values() if ids)

    with _mb_lock:
        mb = _load_memory_bank(workspace)
        _save_memory_bank(workspace, mb)
        total_sources = len(mb["page_info"])

        all_cited_ids: set[int] = set()
        for ids in parsed.values():
            all_cited_ids.update(ids)
        registered_ids = set(mb["url2id"].values())
        invalid_ids = sorted(all_cited_ids - registered_ids)

    id_warning = ""
    if invalid_ids:
        id_warning = (
            f"\n⚠️ Invalid citation IDs: {invalid_ids} — "
            "not registered in evidence bank. Remove or replace them."
        )

    return (
        f"Outline updated ({total} subsections, {cited} with sources). "
        f"Total sources in evidence bank: {total_sources}.{id_warning}\n"
        "REMINDER:\n"
        "- Each subsection needs a [sources: 1, 2] line below it (outline tracking ONLY)\n"
        "- In writing phase: use inline [id_X] format, call evidence_retrieve BEFORE each section\n"
        "- Call research_reflect to assess completeness and get next search suggestions."
    )


@tool("research_reflect", parse_docstring=True)
def research_reflect_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
) -> str:
    """Reflect on current research progress using LLM-based semantic evaluation.

    This is the research cycle's control center. Each call counts as one research
    iteration. Returns a structured reflection with:
    - Completeness assessment (LLM-driven, not programmatic coverage)
    - Identified semantic gaps and priority section
    - Suggested search queries for next round (gap→search closed loop)
    - Outline evolution suggestions (drives outline restructuring)

    Call this after each round of searching and outline updating.
    """
    workspace = _get_workspace_path(runtime)

    outline_path = workspace / "outline.md"
    if not outline_path.exists():
        return "No outline found. Create an outline first using outline_update."
    outline = outline_path.read_text(encoding="utf-8")

    with _mb_lock:
        mb = _load_memory_bank(workspace)
        mb["research_iterations"] = mb.get("research_iterations", 0) + 1
        _save_memory_bank(workspace, mb)

    research_iterations = mb["research_iterations"]
    total_sources = len(mb["page_info"])

    user_query = _extract_user_query(runtime)

    # Hard limit: prevent infinite loops
    if research_iterations >= 15:
        return (
            "## Research Reflection\n\n"
            f"**Sources**: {total_sources} | **Iterations**: {research_iterations}\n\n"
            "**⚠️ Hard limit reached (max 15 iterations).** Proceed to writing with current research."
        )

    # Hard gates (minimum thresholds)
    hard_gates_met = research_iterations >= 3 and total_sources >= 10

    # LLM Reflection
    reflection_result = None
    try:
        from deerflow.models import create_chat_model

        summaries = "\n".join(
            f"- [id_{p['id']}] {p.get('summary', 'No summary')}"
            for p in mb["page_info"]
        )
        prompt = REFLECTION_PROMPT.format(
            user_query=user_query,
            outline=outline,
            summaries=summaries or "(no sources collected yet)",
            iteration=research_iterations,
            total_sources=total_sources,
        )
        model = create_chat_model(thinking_enabled=False)
        response = model.invoke(prompt)
        response_text = str(response.content).strip()
        reflection_result = _extract_json(response_text)
    except Exception:
        reflection_result = None

    # Extract fields
    if reflection_result:
        llm_says_complete = reflection_result.get("research_complete", False)
        section_gaps = reflection_result.get("section_gaps", {})
        priority_section = reflection_result.get("priority_section", "")
        knowledge_gap = reflection_result.get("knowledge_gap", "")
        suggested_queries = reflection_result.get("suggested_queries", [])
        outline_evolution = reflection_result.get("outline_evolution", "")
        reasoning = reflection_result.get("reasoning", "")
    else:
        llm_says_complete = False
        section_gaps = {}
        priority_section = ""
        knowledge_gap = ""
        suggested_queries = []
        outline_evolution = ""
        reasoning = ""

    # Hard gate override: even if LLM says complete, must meet minimum thresholds
    research_complete = llm_says_complete and hard_gates_met

    # Build reflection report
    report_lines = [
        "## Research Reflection",
        "",
        f"**Sources**: {total_sources} | **Iteration**: {research_iterations}",
        "",
    ]

    if reflection_result:
        if section_gaps:
            report_lines.append("**Semantic gaps identified:**")
            for section, gap in section_gaps.items():
                report_lines.append(f"- **{section}**: {gap}")
            report_lines.append("")

        if priority_section:
            report_lines.append(f"**Priority focus**: {priority_section}")
        if knowledge_gap:
            report_lines.append(f"**Key knowledge gap**: {knowledge_gap}")
        if reasoning:
            report_lines.append(f"**Assessment**: {reasoning}")
        report_lines.append("")

        if outline_evolution and outline_evolution.lower() != "no changes needed":
            report_lines.append(f"**Outline evolution**: {outline_evolution}")
            report_lines.append("")

        if suggested_queries and not research_complete:
            report_lines.append("**Suggested queries for next round:**")
            for q in suggested_queries[:3]:
                report_lines.append(f"- `{q}`")
            report_lines.append("")
    else:
        report_lines.append("*(LLM reflection unavailable — using conservative fallback)*")
        report_lines.append("")

    # Recommendation
    if research_complete:
        report_lines.append(
            "✅ **Research is comprehensive.** Proceed to writing phase."
        )
    elif not hard_gates_met:
        blockers = []
        if research_iterations < 3:
            blockers.append(f"iterations {research_iterations}/3")
        if total_sources < 10:
            blockers.append(f"sources {total_sources}/10")
        report_lines.append(
            f"⛔ DO NOT PROCEED TO WRITING. Unmet: {', '.join(blockers)}."
        )
        if suggested_queries:
            report_lines.append("Use the suggested queries above for your next search round.")
    else:
        report_lines.append(
            "⚠️ Minimum thresholds met but research gaps remain. "
            "Continue researching to fill the gaps identified above."
        )
        if suggested_queries:
            report_lines.append("Use the suggested queries above for your next search round.")

    return "\n".join(report_lines)


@tool("check_query_duplicate", parse_docstring=True)
def check_query_duplicate_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    query: str,
) -> str:
    """Check if a search query is too similar to previously executed queries (85% similarity threshold).

    Call this BEFORE executing web_search to avoid redundant searches.

    Args:
        query: The search query to check.
    """
    workspace = _get_workspace_path(runtime)
    with _mb_lock:
        mb = _load_memory_bank(workspace)

        for executed in mb.get("executed_queries", []):
            ratio = SequenceMatcher(None, query.lower(), executed.lower()).ratio()
            if ratio >= 0.85:
                return (
                    f"DUPLICATE: '{query}' is {ratio:.0%} similar to previously "
                    f"executed query '{executed}'. Try a different angle."
                )

        mb.setdefault("executed_queries", []).append(query)
        _save_memory_bank(workspace, mb)
    return f"Query is unique. Recorded. Total unique queries: {len(mb['executed_queries'])}."
