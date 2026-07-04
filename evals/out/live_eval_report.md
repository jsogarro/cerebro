# Live Eval Report

**Generated:** 2026-07-04T06:59:30.303386+00:00

## Summary

- **Total Checks:** 10
- **Passed:** 10 ✅
- **Failed:** 0 ❌
- **Total Cost:** $0.0785

## Cost Breakdown

| Check | Model | Input Tokens | Output Tokens | Cost (USD) |
|-------|-------|--------------|---------------|------------|
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | anthropic/claude-sonnet-4.6 | 1326 | 1302 | $0.039420 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 411 | 191 | $0.001204 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | anthropic/claude-sonnet-4.6 | 1167 | 1284 | $0.036765 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 17 | 177 | $0.000388 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 18 | 69 | $0.000174 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 32 | 61 | $0.000186 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 18 | 44 | $0.000124 |

## Check Results

### multi_domain.concat ✅

**Status:** passed

- **strategy:** concat
- **domains:** ['research', 'analytics']

### multi_domain.llm ✅

**Status:** passed

- **strategy:** llm
- **synthesis_len:** 1855
- **models:** ['deepseek/deepseek-chat', 'anthropic/claude-sonnet-4.6']
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### provider_health.slug_validation ✅

**Status:** passed

- **valid_slugs:** {'simple': 'deepseek/deepseek-chat', 'balanced': 'anthropic/claude-sonnet-4.6', 'complex': 'anthropic/claude-sonnet-4.6'}
- **invalid_slugs:** {}
- **validation_error:** None

### provider_health.no_stale_state ✅

**Status:** passed

- **stale_slugs:** {}

### structured_routing.citation_simple_tier ✅

**Status:** passed

- **type:** CitationSchema
- **n_citations:** 2
- **structured_calls:** [{'model': 'deepseek/deepseek-chat', 'response_format': 'json_object', 'max_tokens': 4000}]
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### structured_routing.synthesis_scale ✅

**Status:** passed

- **narrative_len:** 2004
- **budgets:** [4000]
- **structured_calls:** [{'model': 'anthropic/claude-sonnet-4.6', 'response_format': 'json_object', 'max_tokens': 4000}]
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.research ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 1080
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.content ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 360
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.analytics ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 245
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.finance ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 240
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}
