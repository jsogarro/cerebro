"""Centralized constants for the Cerebro platform.

This module provides a single source of truth for configuration values
that appear across multiple modules.
"""

# =============================================================================
# Retry Configuration
# =============================================================================

# Default retry attempts for standard operations
DEFAULT_RETRY_ATTEMPTS = 2

# Maximum retry attempts for critical operations
MAX_RETRY_ATTEMPTS = 3

# Minimum retry attempts for fast-fail operations
MIN_RETRY_ATTEMPTS = 1

# No retries for speed-optimized operations
NO_RETRY = 0


# =============================================================================
# Timeout Configuration (seconds)
# =============================================================================

# Default timeout for agent execution (5 minutes)
DEFAULT_AGENT_TIMEOUT = 300

# Short timeout for fast operations (1 minute)
SHORT_TIMEOUT = 60

# Medium timeout for standard operations (3 minutes)
MEDIUM_TIMEOUT = 180

# Long timeout for complex operations (4 minutes)
LONG_TIMEOUT = 240

# Extended timeout for complex/cheap models (10 minutes)
EXTENDED_TIMEOUT = 600

# Very short timeout for speed tests (30 seconds)
SPEED_TEST_TIMEOUT = 30

# HTTP client timeout for external APIs
HTTP_CLIENT_TIMEOUT = 5.0

# Academic API timeout
ACADEMIC_API_TIMEOUT = 30.0

# LaTeX compilation timeout
LATEX_COMPILE_TIMEOUT = 60

# WebSocket ping timeout
WEBSOCKET_PING_TIMEOUT = 10


# =============================================================================
# Parallel Execution Limits
# =============================================================================

# No parallelism for direct mode
DIRECT_MODE_PARALLELISM = 1

# Low parallelism for debate/simple parallel operations
LOW_PARALLELISM = 3

# High parallelism for ensemble/speed-optimized operations
HIGH_PARALLELISM = 5

# Maximum parallelism for aggressive speed optimization
MAX_PARALLELISM = 10


# =============================================================================
# Quality & Consensus Thresholds
# =============================================================================

# Minimum quality score for acceptable results
MIN_QUALITY_SCORE = 0.7

# Good quality threshold
GOOD_QUALITY_THRESHOLD = 0.75

# High quality threshold
HIGH_QUALITY_THRESHOLD = 0.8

# Excellent quality threshold
EXCELLENT_QUALITY_THRESHOLD = 0.9

# Default confidence score fallback
DEFAULT_CONFIDENCE_SCORE = 0.75

# High consensus threshold for cross-domain synthesis
HIGH_CONSENSUS_THRESHOLD = 0.8


# =============================================================================
# Cache Configuration
# =============================================================================

# Default cache TTL (1 hour)
DEFAULT_CACHE_TTL = 3600

# Long-term cache TTL for stable data (24 hours)
LONG_TERM_CACHE_TTL = 86400

# Service registry TTL (5 minutes)
SERVICE_REGISTRY_TTL = 300


# =============================================================================
# Resource Limits
# =============================================================================

# Maximum number of worker threads for supervisor
MAX_SUPERVISOR_WORKERS = 8


# =============================================================================
# Token & Context Limits
# =============================================================================

# Default estimated tokens for routing decisions
DEFAULT_ESTIMATED_TOKENS = 1000

# Higher token estimate for complex queries
HIGH_ESTIMATED_TOKENS = 2000


# =============================================================================
# Pagination & Query Limits
# =============================================================================

# Default audit log query limit
AUDIT_LOG_LIMIT = 10000

# Default episodic memory analysis limit
EPISODIC_MEMORY_LIMIT = 1000


# =============================================================================
# Gemini Configuration Defaults
# =============================================================================

# Default temperature for Gemini generation
DEFAULT_GEMINI_TEMPERATURE = 0.7

# Default top_p for Gemini generation
DEFAULT_GEMINI_TOP_P = 0.9
