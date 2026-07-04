# Live Eval Report

**Generated:** 2026-07-04T05:39:42.196546+00:00

## Summary

- **Total Checks:** 9
- **Passed:** 9 ✅
- **Failed:** 0 ❌
- **Total Cost:** $0.0816

## Cost Breakdown

| Check | Model | Input Tokens | Output Tokens | Cost (USD) |
|-------|-------|--------------|---------------|------------|
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | anthropic/claude-sonnet-4.6 | 1326 | 1378 | $0.040560 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 411 | 200 | $0.001222 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | anthropic/claude-sonnet-4.6 | 1167 | 1417 | $0.038760 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 17 | 192 | $0.000418 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 18 | 65 | $0.000166 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 32 | 64 | $0.000192 |

## Check Results

### multi_domain.concat ✅

**Status:** passed

- **strategy:** concat
- **domains:** ['research', 'analytics']

### multi_domain.llm ✅

**Status:** passed

- **strategy:** llm
- **synthesis_len:** 2353
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

- **narrative_len:** 2336
- **budgets:** [4000]
- **structured_calls:** [{'model': 'anthropic/claude-sonnet-4.6', 'response_format': 'json_object', 'max_tokens': 4000}]
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.research ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 1178
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.content ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 351
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.analytics ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 212
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}
