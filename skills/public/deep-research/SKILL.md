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

**Stage 1 Rules: Information Collection**
- Focus on COMPREHENSIVENESS: cover ALL key dimensions
- Use BULLET POINTS, not paragraphs
- Record key data points and causal relationships
- Note surprising findings and contradictions

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
8. **Update outline sources**: Call `outline_update` to add `[sources: 1, 2]` annotations
   below subsections with new evidence:
   ```
   ### 2.1 Architecture Overview
   [sources: 4, 5, 6]
   ```
9. **Loop**: Go back to step 1

**CRITICAL REMINDERS for Phase 1:**
- `task(subagent_type="reflection")` is the cycle anchor — follow its `suggested_queries` and `outline_evolution` guidance
- Each subsection needs a `[sources: 1, 2]` line below it (NOT in the heading)
- Do NOT use `<citation>` tags — they are deprecated
- Do NOT invent your own search direction when `suggested_queries` is available
- If `outline_evolution` suggests new sections or restructuring, update the outline FIRST
- Fetch FULL content for promising results, don't rely on snippets
- After storing evidence, ALWAYS update the outline with new source IDs

### Phase Transition: Research to Writing

After the reflection subagent returns `research_complete: true`
(or `research_iterations >= 15` forced cap):

1. Call `compact_context(reason="Research phase complete. Transitioning to writing.")`
2. After compression, your context will contain:
   - A structured summary of the research phase
   - Writing instructions with paths to outline and evidence bank
3. Proceed directly to Phase 2 (Hierarchical Writing)
   - Do NOT call web_search or web_fetch in Phase 2
   - Use evidence_retrieve to access stored evidence

### Phase 2: Hierarchical Writing

**Stage 2 Rules: Report Generation**
- Focus on INSIGHTFULNESS: granular analysis, causal relationships
- Focus on HELPFULNESS: fluent, coherent, logical structure
- Every factual statement MUST have inline citation
- Include ≥2 tables with post-table analysis

**For each section in the outline:**

1. **Read sources**: Look at the `[sources: X, Y]` line below the section heading
2. **Retrieve evidence**: Call `evidence_retrieve` with those source IDs
3. **Write section**: Analyze the evidence and write the section content with inline citations `[id_X]`
4. **Move to next section**: Repeat for all sections

**CRITICAL REMINDERS for Phase 2:**
- Cite EVERY factual statement: `According to [id_X], ...` or `... [id_X, id_Y]`
- Analyze WHY findings matter, don't just enumerate
- Each section should have ≥2 paragraphs of analysis
- NO shallow enumeration without interpretation
- NO statistics without context and analysis
- Call `evidence_retrieve` for EACH section separately — do NOT batch all sources in one call
- Write each section immediately after retrieving its evidence, before moving to the next

### Phase 3: Report Assembly

1. Combine all sections into a single report
2. Add Introduction (synthesize key themes) and Conclusion (key takeaways)
3. Generate References list from evidence bank: `[id_X] Title - URL`
4. Save as `research_{topic}_{YYYYMMDD}.md` in `/mnt/user-data/outputs/`
5. Call `present_file` to deliver the report

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
