"""Research tools for Deep Research v2: Memory Bank and Outline Management."""

import json
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths

_mb_lock = threading.Lock()


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


@tool("evidence_store", parse_docstring=True)
def evidence_store_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    url: str,
    title: str,
    summary: str,
    evidence: str,
    goal: str,
) -> str:
    """Store extracted evidence in the Memory Bank. Use after fetching and analyzing a web page.

    The summary is kept short (1-2 sentences) for the Planner's context.
    The evidence contains detailed extracted quotes, data, and analysis for the Writer.

    Args:
        url: The source URL.
        title: The page title (from web_search results or web_fetch).
        summary: A short 1-2 sentence summary of the page's relevance.
        evidence: Detailed extracted evidence (key quotes, data points, analysis).
        goal: The search goal this evidence relates to.
    """
    workspace = _get_workspace_path(runtime)
    with _mb_lock:
        mb = _load_memory_bank(workspace)

        if url in mb["url2id"]:
            existing_id = mb["url2id"][url]
            return f"URL already stored as source {existing_id}. No duplicate stored."

        new_id = len(mb["url2id"]) + 1
        mb["url2id"][url] = new_id
        mb["page_info"].append({
            "id": new_id,
            "url": url,
            "title": title,
            "goal": goal,
            "summary": summary,
            "evidence": evidence,
            "status": "successful",
        })
        _save_memory_bank(workspace, mb)
        total_sources = len(mb["page_info"])
    return (
        f"Stored as source {new_id}. Total sources: {total_sources}. "
        f"Summary: {summary}\n"
        "REMINDER: Next steps — update outline with this source ID, "
        "then call task(subagent_type='reflection') to assess progress."
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
                    f'<source id="{cid}">\nTitle: {info["title"]}\nURL: {url}\nEvidence:\n{info["evidence"]}\n</source>'
                )

    if not results:
        return f"No evidence found for IDs: {ids}"
    body = "\n\n".join(results)
    return (
        f"{body}\n\n"
        "REMINDER: Cite sources using [citation:Title](URL) format in the report. "
        "Use the Title and URL from each <source> block above. "
        "Example: [citation:AI Diagnosis Accuracy](https://example.com/page1). "
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
        "- In writing phase: cite with [citation:Title](URL) format, call evidence_retrieve BEFORE each section\n"
        "- Call task(subagent_type='reflection') to assess completeness and get next search suggestions."
    )


def _extract_heading_key(heading: str) -> str:
    """Strip '#' prefix and leading number/dot prefix to get key text.

    '### 2.1 Architecture Overview' → 'Architecture Overview'
    '## Performance Analysis' → 'Performance Analysis'
    """
    text = re.sub(r"^#+\s*", "", heading).strip()
    text = re.sub(r"^\d+(\.\d+)*\.?\s+", "", text).strip()
    return text


def _parse_report_sections(report_text: str) -> list[tuple[str, str]]:
    """Parse report into [(heading_raw, body_text), ...] split by any ATX heading (#–######)."""
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_body_lines: list[str] = []

    for line in report_text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            if current_heading is not None:
                sections.append((current_heading, "\n".join(current_body_lines)))
            current_heading = line
            current_body_lines = []
        else:
            if current_heading is not None:
                current_body_lines.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_body_lines)))

    return sections


def _heading_level(heading: str) -> int:
    """Return the heading depth: '#' → 1, '##' → 2, '###' → 3, etc."""
    m = re.match(r"^(#+)\s+", heading.strip())
    return len(m.group(1)) if m else 0


def _is_content_section(heading_key: str) -> bool:
    """Return False for Introduction, Conclusion, Sources, References headings.

    Applied only to leaf sections (###+); # and ## are excluded upstream by
    _heading_level. The 'introduction' keyword here mainly catches an explicit
    '### 1.1 Introduction'-style subsection.
    """
    lower = heading_key.lower()
    return not any(kw in lower for kw in ("introduction", "conclusion", "sources", "references"))


@tool("report_validate", parse_docstring=True)
def report_validate_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    file_path: str,
) -> str:
    """Validate a research report against the outline and citation requirements.

    Call this after assembling the report and before presenting it.
    Fix any reported issues and call again until PASS.

    Args:
        file_path: Absolute virtual path to the report file under
            `/mnt/user-data/outputs/`.
    """
    workspace = _get_workspace_path(runtime)
    thread_id = None
    if runtime and getattr(runtime, "context", None):
        thread_id = runtime.context.get("thread_id")

    if not thread_id:
        return (
            "FAIL — 1 issue(s) found:\n"
            "1. Thread ID is not available in runtime context.\n"
            "Fix FAIL issues and call report_validate again."
        )

    expected_prefix = f"{VIRTUAL_PATH_PREFIX}/outputs/"
    if not file_path.startswith(expected_prefix):
        return (
            "FAIL — 1 issue(s) found:\n"
            f"1. report_validate only accepts file paths under {expected_prefix}\n"
            "Fix FAIL issues and call report_validate again."
        )

    try:
        report_path = get_paths().resolve_virtual_path(thread_id, file_path)
    except ValueError as exc:
        return (
            "FAIL — 1 issue(s) found:\n"
            f"1. Invalid report path: {exc}\n"
            "Fix FAIL issues and call report_validate again."
        )

    issues: list[str] = []

    # --- Check 1: Report file exists and has substantial content ---
    if not report_path.exists():
        issues.append("Report file does not exist.")
        return (
            f"FAIL — {len(issues)} issue(s) found:\n"
            + "\n".join(f"{i + 1}. {iss}" for i, iss in enumerate(issues))
            + "\nFix FAIL issues and call report_validate again."
        )

    report_text = report_path.read_text(encoding="utf-8")
    word_count = len(report_text.split())
    if word_count < 100:
        issues.append(
            f"Report has insufficient content ({word_count} words). "
            "Ensure the report is fully written before validating."
        )

    # Parse report sections once (used for checks 2, 3, 4).
    # leaf_content_sections: only ### and deeper headings that are not structural
    # keywords (introduction/conclusion/sources/references).  # and ## headings
    # are container-level and are not required to carry body text.
    report_sections = _parse_report_sections(report_text)
    leaf_content_sections = [
        (h, b) for h, b in report_sections
        if _heading_level(h) >= 3 and _is_content_section(_extract_heading_key(h))
    ]

    # --- Check 2: Outline section count matches report section count ---
    outline_path = workspace / "outline.md"
    informational_lines: list[str] = []
    if outline_path.exists():
        outline_text = outline_path.read_text(encoding="utf-8")
        parsed = _parse_outline(outline_text)
        outline_leaf_count = len(parsed)

        if outline_leaf_count >= 5:
            report_content_count = len(leaf_content_sections)

            if report_content_count < outline_leaf_count * 0.7:
                issues.append(
                    f"Report has {report_content_count} content section(s) but outline has "
                    f"{outline_leaf_count} subsection(s) — more than 30% appear to be missing."
                )
                # Informational: heading name matching
                report_heading_keys = {
                    _extract_heading_key(h).lower() for h, _ in report_sections
                }
                unmatched = []
                for outline_heading in parsed:
                    key = _extract_heading_key(outline_heading)
                    key_lower = key.lower()
                    matched = any(
                        key_lower in rk or rk in key_lower
                        for rk in report_heading_keys
                    )
                    if not matched:
                        unmatched.append(key)
                if unmatched:
                    informational_lines.append(
                        "[informational] Outline sections not matched by heading: "
                        + ", ".join(unmatched)
                        + " (verify content is merged into other sections)"
                    )

    # --- Check 3: Per-leaf-content-section body is non-empty ---
    for heading, body in leaf_content_sections:
        if not body.strip():
            issues.append(
                f'Section "{_extract_heading_key(heading)}" has an empty body. '
                "Write the section content before validating."
            )

    # --- Check 4: Sources section exists and is non-empty ---
    has_sources = False
    for heading, body in report_sections:
        key_lower = _extract_heading_key(heading).lower()
        if "sources" in key_lower or "references" in key_lower:
            if "- [" in body:
                has_sources = True
                break
    if not has_sources:
        issues.append(
            'No Sources/References section found, or the section contains no "- [" entries. '
            "Add a Sources section listing all cited URLs."
        )

    # --- Check 5: No [sources: ...] outline markers in report body ---
    if re.search(r"\[sources:", report_text):
        issues.append(
            'Report contains "[sources: ...]" outline markers. '
            "Remove all [sources: ...] lines — they belong in the outline only."
        )

    if not issues:
        return "PASS — proceed to report_reviewer"

    fail_lines = [f"{i + 1}. {iss}" for i, iss in enumerate(issues)]
    output = "FAIL — {} issue(s) found:\n{}\n".format(len(issues), "\n".join(fail_lines))
    if informational_lines:
        output += "\n".join(informational_lines) + "\n"
    output += "Fix FAIL issues and call report_validate again."
    return output


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
                    f"executed query '{executed}'. Try a different angle or more specific terms.\n"
                    "NOTE: This is a deduplication check ONLY. Do NOT interpret this as a signal to "
                    "stop researching. Reformulate the query with more specific terms and continue."
                )

        mb.setdefault("executed_queries", []).append(query)
        _save_memory_bank(workspace, mb)
    return f"Query is unique. Recorded. Total unique queries: {len(mb['executed_queries'])}."
