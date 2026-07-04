# Live Eval Report

**Generated:** 2026-07-04T06:59:31.274027+00:00

## Summary

- **Total Checks:** 10
- **Passed:** 10 ✅
- **Failed:** 0 ❌
- **Total Cost:** $0.0777

## Cost Breakdown

| Check | Model | Input Tokens | Output Tokens | Cost (USD) |
|-------|-------|--------------|---------------|------------|
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | anthropic/claude-sonnet-4.6 | 1326 | 1239 | $0.038475 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 411 | 194 | $0.001210 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | anthropic/claude-sonnet-4.6 | 1167 | 1291 | $0.036870 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 17 | 178 | $0.000390 |
| openrouter | deepseek/deepseek-chat | 10 | 9 | $0.000038 |
| openrouter | deepseek/deepseek-chat | 18 | 58 | $0.000152 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 32 | 61 | $0.000186 |
| openrouter | deepseek/deepseek-chat | 10 | 10 | $0.000040 |
| openrouter | deepseek/deepseek-chat | 18 | 59 | $0.000154 |

## Check Results

### multi_domain.concat ✅

**Status:** passed

- **strategy:** concat
- **domains:** ['research', 'analytics']

### multi_domain.llm ✅

**Status:** passed

- **strategy:** llm
- **synthesis_len:** 1585
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

- **narrative_len:** 2131
- **budgets:** [4000]
- **structured_calls:** [{'model': 'anthropic/claude-sonnet-4.6', 'response_format': 'json_object', 'max_tokens': 4000}]
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.research ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 1018
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}

### text_routing.content ✅

**Status:** passed

- **models:** ['deepseek/deepseek-chat', 'deepseek/deepseek-chat']
- **content_len:** 303
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
- **content_len:** 331
- **confidence:** 0.9
- **gemini_fallbacks:** {'text': 0, 'structured': 0}
