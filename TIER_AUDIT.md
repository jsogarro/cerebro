# Tier Audit for Structured Call Sites

## Summary

All call sites of `_generate_structured_with_routing` and `_generate_with_routing` audited for tier assignment. Tier precedence is now: **explicit `tier=` > task-derived > "balanced"**.

## Structured Call Sites

| File | Line | Method | Task Param | Tier Param | Effective Tier | Notes |
|------|------|--------|------------|------------|----------------|-------|
| `citation_agent.py` | 328 | `_format_citations_with_gemini` | `None` | `"simple"` | **simple** | ✅ Citation formatting (low-cost) |
| `literature_review_agent.py` | 717 | `_search_sources_structured` | `None` | (default) | balanced | Research source discovery |
| `literature_review_agent.py` | 750 | `_analyze_sources_structured` | `None` | (default) | balanced | Research source analysis |
| `synthesis_agent.py` | 61 | `execute` | `task` | (default) | task-derived | Multi-domain synthesis |
| `methodology_agent.py` | 59 | `execute` | `task` | (default) | task-derived | Research methodology analysis |

## Unstructured Call Sites (for reference)

| File | Line | Method | Task Param | Effective Tier |
|------|------|--------|------------|----------------|
| `comparative_analysis_agent.py` | 665 | `_analyze_comparison_with_gemini` | `task` | task-derived |
| `literature_review_agent.py` | 526 | `_search_with_gemini` | `task` | task-derived |
| `literature_review_agent.py` | 866 | `_synthesize_with_gemini` | `task` | task-derived |
| `llm_worker_base.py` | 465 | `execute` | `task` | task-derived |

## Cost Impact

The key win is **citation formatting** now routes to the **simple tier** (DeepSeek ~$0.001/call) instead of balanced (Claude Sonnet 4.6 ~$0.07/call), a **~70x cost reduction** for this formatting-only task.
