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

### Phase 1: Dynamic Research Loop (≥3 iterations)

**Stage 1 Rules: Information Collection**
- Focus on COMPREHENSIVENESS: cover ALL key dimensions
- Use BULLET POINTS, not paragraphs
- Record key data points and causal relationships
- Note surprising findings and contradictions

**Each iteration (anchored by `research_reflect`):**

1. **Reflect**: Call `research_reflect` — it returns:
   - Semantic completeness assessment (LLM-driven, not programmatic coverage)
   - `suggested_queries`: targeted search queries for next round
   - `outline_evolution`: suggestions for outline restructuring
   - Whether to continue researching or proceed to writing
2. **Evolve outline**: If `outline_evolution` suggests changes (new sections, merges, splits),
   call `outline_update` with the restructured outline
3. **Search**: Use `suggested_queries` from reflection as primary search targets.
   Call `check_query_duplicate` for each, then `web_search` for non-duplicate queries
4. **Select & Fetch**: Review search results, select top 2-3 most relevant URLs, call `web_fetch` for each
5. **Extract & Store**: For each fetched page, call `evidence_store` with:
   - `summary`: 1-2 sentence summary of relevance
   - `evidence`: Detailed extracted quotes, data points, key findings
   - `goal`: The search goal this evidence relates to
6. **Update outline sources**: Call `outline_update` to add `[sources: 1, 2]` annotations
   below subsections with new evidence:
   ```
   ### 2.1 Architecture Overview
   [sources: 4, 5, 6]
   ```
7. **Loop**: Go back to step 1 (next `research_reflect` call = next iteration)

**CRITICAL REMINDERS for Phase 1:**
- `research_reflect` is the cycle anchor — follow its `suggested_queries` and `outline_evolution` guidance
- Each subsection needs a `[sources: 1, 2]` line below it (NOT in the heading)
- Do NOT use `<citation>` tags — they are deprecated
- Do NOT invent your own search direction when `suggested_queries` is available
- If `outline_evolution` suggests new sections or restructuring, update the outline FIRST
- Fetch FULL content for promising results, don't rely on snippets
- After storing evidence, ALWAYS update the outline with new source IDs

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
- Simple query: 5-10 searches, 1-2 research iterations
- Medium query: 10-20 searches, 2-3 research iterations
- Complex query: 20+ searches, 3+ research iterations

## Quality Gates

Research readiness is assessed by `research_reflect` (LLM-driven semantic evaluation):
1. ✅ LLM quantitative assessment: scores each of 5 dimensions (0-100%), requires average >90% and no critical <70%
2. ✅ ≥3 research iterations completed (hard gate, counted by `research_reflect` calls)
3. ✅ ≥10 unique sources in evidence bank (hard gate)
4. ⛔ Safety cap: 15 iterations max

NOTE: No programmatic coverage percentage. The LLM evaluator is the sole judge of research completeness.
