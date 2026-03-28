---
name: deep-research
description: Use this skill for ANY question requiring comprehensive web research. Provides structured research methodology with dynamic outline, evidence bank, and hierarchical writing. Use proactively when the user's question needs thorough online information.
---

# Deep Research v2 Skill

## Overview

This skill implements a structured deep research methodology inspired by state-of-the-art research systems. It uses a dynamic outline, structured evidence bank, and hierarchical writing to produce comprehensive, well-cited research reports.

## When to Use This Skill

**Always load this skill when:**
- User asks "research X", "investigate X", "write a report on X"
- The question requires comprehensive, multi-source information
- A single web search would be insufficient
- Before content generation tasks that need factual grounding

## Research Methodology

### Phase 0: Research Brief & Initial Outline (1 step)

1. Analyze the user query to identify key dimensions and subtopics
2. Create an initial outline draft (≤4 levels deep) using `outline_update`:
   ```
   ## 1. Introduction

   ### 1.1 Background

   ### 1.2 Problem Statement

   ## 2. [Main Dimension 1]

   ### 2.1 [Subtopic]

   ### 2.2 [Subtopic]
   ...
   ```
3. This initial outline has NO source annotations — that's expected

### Phase 1: Dynamic Research Loop

**Each iteration:**

1. **Reflect**: Call `task(subagent_type="reflection", prompt="Evaluate research completeness for: {user_query}")` — it returns a JSON message with:
   - `research_iterations`: current iteration count (incremented by Subagent)
   - `research_complete`: semantic completeness assessment
   - `suggested_queries`: targeted search queries for next round
   - `outline_evolution`: suggestions for outline restructuring
2. **Iteration limit check**: If `research_iterations >= 15` in the returned message, proceed to Phase Transition regardless of `research_complete`
3. **Completion check**: If `research_complete` is true (and `research_iterations < 15`), proceed to Phase Transition
4. **Evolve outline**: If `outline_evolution` suggests changes (new sections, merges, splits),
   read current `outline.md`, restructure it, and call `outline_update`.
   Preserve existing `[sources: ...]` annotations: when merging sections, union their IDs;
   when splitting, assign each ID to the most relevant new subsection.
   New sections start without sources (filled in step 8)
5. **Search**: Use `suggested_queries` as primary search targets.
   Call `check_query_duplicate` for each, then `web_search` for non-duplicate queries
6. **Select & Fetch**: Review search results, select top 2-3 most relevant URLs, call `web_fetch` for each
7. **Extract & Store**: For each fetched page, call `evidence_store` with:
   - `summary`: 1-2 sentence summary of relevance
   - `evidence`: Detailed extracted quotes, data points, key findings
   - `goal`: The search goal this evidence relates to
8. **Update outline sources**: Call `outline_update` to add `[sources: <ID>, ...]` annotations
   below subsections with new evidence (use actual source IDs from `evidence_store`):
   ```
   ### 2.1 Architecture Overview
   [sources: 4, 5, 6]
   ```
9. **Loop**: Go back to step 1

**Research loop constraints:**
- Use BULLET POINTS for evidence, not paragraphs
- Note surprising findings and contradictions
- `task(subagent_type="reflection")` is the cycle anchor — do NOT invent your own search direction when `suggested_queries` is available
- Each subsection needs a `[sources: <ID>, ...]` line below it (NOT in the heading) listing the actual source IDs relevant to that subsection
- Do NOT use `<citation>` tags — they are deprecated
- If `outline_evolution` suggests new sections or restructuring, update the outline FIRST
- Fetch FULL content for promising results, don't rely on snippets

### Phase Transition: Research to Writing

After the reflection subagent returns `research_complete: true`
(or `research_iterations >= 15` forced cap):

1. Call `compact_context(reason="Research phase complete. Transitioning to writing.")`
2. After compression, your context will contain:
   - A structured summary of the research phase
   - **The complete Writing Protocol** with step-by-step instructions
3. Follow the Writing Protocol in the compressed context
   - The compressed context is self-contained — do NOT re-read this skill file
   - Do NOT call web_search or web_fetch

### Phase 2: Hierarchical Writing

**Report structure rule:**
- Follow the outline's heading hierarchy: `##` for chapters, `###` for sections within them.
- Chapters with subsections in the outline should NOT have loose content above the first `###`.
- Short chapters (e.g., Introduction, Conclusion) may contain content directly under `##`.

**Step 1 — Initialize the report:**
1. Choose a report file path under `/mnt/user-data/outputs/`
2. Write the report title (`# Title`) and Introduction to the file using `write_file`
   - Base the Introduction on the research summary (key themes, scope, structure preview)

**Step 2 — Write sections incrementally:**

For EACH section in the outline, in order:
1. Read the `[sources: <ID>, ...]` line below the section heading
2. Call `evidence_retrieve` with those source IDs — one call per section, do NOT batch all sources in one call
3. Append the section to the report file **immediately** using `write_file(append=True)` — do NOT retrieve multiple sections before writing.
   - Include the `##` chapter heading when starting a new chapter
   - Include the `###` section heading, full section body, and inline citations
   - Use `[citation:Title](URL)` inline citations with the Title and URL from each `<source>` block
4. Move to the next section and repeat

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

### Phase 3: Report Finalization

1. Append Conclusion (key takeaways) and Sources section (`- [Title](URL) - brief description`) to the report file using `write_file(append=True)`
2. Call `report_validate` with the report file path — fix any issues it reports, then call again until PASS
3. Call `task(subagent_type="report_reviewer", prompt="Review and improve the research report at <report_path> for: <original_query>")` — if this call fails or times out, proceed directly to step 4
4. Call `present_files` to deliver the report

**Prohibitions:**
- Do NOT call `web_search` or `web_fetch`
- Do NOT include `[sources: ...]` lines in the report — those are outline-only markers
- Do NOT accumulate sections in chat and combine later — write each section directly to the file

## Search Strategy

### Effective Query Patterns
- Be specific: "enterprise AI adoption trends 2026" not "AI trends"
- Include authority hints: "[topic] research paper", "[topic] McKinsey report"
- Search for specific types: "[topic] case study", "[topic] statistics"
- Use temporal qualifiers from <current_date>

### Temporal Awareness
Always check `<current_date>` before forming search queries. Use appropriate time precision.

### Scaling (Self-Balancing)
Search breadth scales with query complexity:
- Simple query: ~5-10 targeted searches
- Medium query: ~10-20 searches across multiple angles
- Complex query: 20+ searches with systematic coverage

The number of research iterations is determined by the Reflection Subagent
based on actual evidence quality. Do not set your own iteration target.

## Quality Gates

Research readiness is assessed by the Reflection Subagent (invoked via `task(subagent_type="reflection")`):
1. ✅ Semantic completeness: Subagent reads actual evidence and judges coverage across key dimensions
2. ✅ `research_complete: true` returned in subagent message — proceed to writing
3. ⛔ Safety cap: if `research_iterations >= 15` in returned message, proceed to writing regardless

NOTE: No hard source count gate. The Reflection Subagent judges research sufficiency based on evidence density, not source count. It reads the full evidence bank and makes an informed assessment.
