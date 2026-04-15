"""
Configuration settings for Cerebro AI Brain Platform.

This configuration supports both the legacy Research Platform functionality
and the new AI Brain capabilities including MASR routing, multi-tier memory,
foundation model providers, and hierarchical agent management.
"""

from typing import Dict, List, Optional, Any
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra environment variables
    )

    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # API Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_WORKERS: int = 4
    SECRET_KEY: str = "MUST_SET_IN_ENV"  # Must be set via environment variable

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://research:research123@localhost:5432/research_db"
    )

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Temporal
    TEMPORAL_HOST: str = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"

    # Gemini API
    GEMINI_API_KEY: str | None = None

    # Worker Configuration
    WORKER_CONCURRENCY: int = 10
    TASK_TIMEOUT_SECONDS: int = 300

    # MCP Configuration
    MCP_PORT: int = 9000
    MCP_TOOLS_ENABLED: bool = True

    # Monitoring
    ENABLE_METRICS: bool = True
    ENABLE_TRACING: bool = True
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"

    # Feature Flags
    ENABLE_CACHE: bool = True
    ENABLE_RATE_LIMITING: bool = True
    MAX_REQUESTS_PER_MINUTE: int = 100

    # Logging
    LOG_LEVEL: str = "INFO"

    # JWT Authentication
    JWT_ALGORITHM: str = "RS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_PRIVATE_KEY_PATH: str | None = "/secrets/jwt_private.pem"
    JWT_PUBLIC_KEY_PATH: str | None = "/secrets/jwt_public.pem"

    # Password Security
    BCRYPT_ROUNDS: int = 12
    PASSWORD_MIN_LENGTH: int = 12
    PASSWORD_HISTORY_LIMIT: int = 5
    CHECK_PASSWORD_BREACHES: bool = True

    # Session Configuration
    SESSION_SECRET_KEY: str | None = None
    SESSION_EXPIRE_HOURS: int = 24
    MAX_SESSIONS_PER_USER: int = 5

    # OAuth2 Providers (for future use)
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None

    # Security Settings
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_PERIOD: int = 60
    ENABLE_MFA: bool = False
    MFA_ISSUER: str = "ResearchPlatform"

    # =====================================
    # AI BRAIN PLATFORM CONFIGURATION
    # =====================================

    # AI Brain General Settings
    AI_BRAIN_ENABLED: bool = True
    AI_BRAIN_MODE: str = "hybrid"  # hybrid, research_only, brain_only
    
    # MASR (Multi-Agent System Router) Configuration
    MASR_ENABLED: bool = True
    MASR_DEFAULT_STRATEGY: str = "balanced"  # cost_efficient, quality_focused, speed_first, balanced, adaptive
    MASR_ENABLE_CACHING: bool = True
    MASR_ENABLE_ADAPTIVE: bool = True
    MASR_COMPLEXITY_WEIGHTS: Dict[str, float] = {
        "linguistic": 0.15,
        "reasoning": 0.25,
        "domain": 0.20,
        "data": 0.15,
        "output": 0.15,
        "time": 0.05,
        "quality": 0.05
    }
    MASR_COMPLEXITY_THRESHOLDS: Dict[str, float] = {
        "simple": 0.3,
        "moderate": 0.7,
        "complex": 1.0
    }

    # Foundation Model Providers Configuration
    MODEL_PROVIDERS_ENABLED: bool = True
    
    # DeepSeek Configuration
    DEEPSEEK_ENABLED: bool = False
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_ENDPOINT: str = "https://api.deepseek.com/v1"
    DEEPSEEK_DEFAULT_MODEL: str = "deepseek-v3"
    
    # Llama Configuration (via Ollama)
    LLAMA_ENABLED: bool = False
    LLAMA_ENDPOINT: str = "http://localhost:11434"
    LLAMA_DEFAULT_MODEL: str = "llama3.3:70b"
    LLAMA_KEEP_ALIVE: str = "5m"
    
    # Gemini Configuration (enhanced)
    GEMINI_ENABLED: bool = True  # Keep existing Gemini as default
    GEMINI_DEFAULT_MODEL: str = "gemini-pro"
    
    # Model Router Configuration
    MODEL_ROUTER_HEALTH_CHECK_INTERVAL: int = 300  # 5 minutes
    MODEL_ROUTER_MAX_RETRIES: int = 3
    MODEL_ROUTER_ENABLE_FALLBACK: bool = True

    # Multi-Tier Memory Configuration
    MEMORY_SYSTEM_ENABLED: bool = True
    MEMORY_ENABLE_CROSS_TIER: bool = True
    MEMORY_MAX_RECALL_ITEMS: int = 10
    MEMORY_CONSOLIDATION_INTERVAL: int = 3600  # 1 hour

    # Working Memory Configuration
    WORKING_MEMORY_ENABLED: bool = True
    WORKING_MEMORY_DEFAULT_TTL: int = 3600  # 1 hour
    WORKING_MEMORY_MAX_MEMORY_MB: int = 512
    WORKING_MEMORY_CLEANUP_INTERVAL: int = 300  # 5 minutes
    WORKING_MEMORY_CONVERSATION_TTL: int = 7200  # 2 hours
    WORKING_MEMORY_MAX_MESSAGES_IN_CONTEXT: int = 50

    # Episodic Memory Configuration  
    EPISODIC_MEMORY_ENABLED: bool = True
    EPISODIC_MEMORY_RETENTION_DAYS: int = 90
    EPISODIC_MEMORY_MAX_SESSION_DURATION_HOURS: int = 24

    # Semantic Memory Configuration
    SEMANTIC_MEMORY_ENABLED: bool = True
    SEMANTIC_MEMORY_VECTOR_DB_URL: str = "http://localhost:6333"
    SEMANTIC_MEMORY_COLLECTION_NAME: str = "cerebro_semantic"
    SEMANTIC_MEMORY_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    SEMANTIC_MEMORY_EMBEDDING_DIMENSION: int = 384

    # Procedural Memory Configuration
    PROCEDURAL_MEMORY_ENABLED: bool = True
    PROCEDURAL_MEMORY_STORAGE_PATH: str = "data/procedural_memory.json"
    PROCEDURAL_MEMORY_MIN_CONFIDENCE: float = 0.7
    PROCEDURAL_MEMORY_MIN_USAGE_PROMOTION: int = 5

    # Agent System Configuration
    AGENTS_MAX_CONCURRENT: int = 20
    AGENTS_MAX_SUPERVISORS: int = 5
    AGENTS_MAX_WORKERS_PER_SUPERVISOR: int = 10
    AGENTS_ENABLE_DYNAMIC_SPAWNING: bool = True
    AGENTS_DEFAULT_TIMEOUT: int = 300  # 5 minutes
    AGENTS_MAX_RETRIES: int = 3

    # Hierarchical Agent Configuration
    HIERARCHICAL_AGENTS_ENABLED: bool = True
    HIERARCHICAL_MAX_DEPTH: int = 3
    HIERARCHICAL_ENABLE_AUTO_SCALING: bool = True
    HIERARCHICAL_SCALE_UP_THRESHOLD: float = 0.8
    HIERARCHICAL_SCALE_DOWN_THRESHOLD: float = 0.3

    # TalkHier Communication Configuration
    TALKHIER_ENABLED: bool = True
    TALKHIER_MAX_REFINEMENT_ROUNDS: int = 3
    TALKHIER_CONSENSUS_THRESHOLD: float = 0.95
    TALKHIER_ENABLE_HIERARCHICAL_REFINEMENT: bool = True

    # Self-Improvement Configuration
    SELF_IMPROVEMENT_ENABLED: bool = True
    SELF_IMPROVEMENT_REFLECTION_FREQUENCY: int = 100  # Every N queries
    SELF_IMPROVEMENT_PERFORMANCE_THRESHOLD: float = 0.8
    SELF_IMPROVEMENT_ENABLE_PROMPT_OPTIMIZATION: bool = True

    # Fine-Tuning Configuration
    FINE_TUNING_ENABLED: bool = False  # Disabled by default
    FINE_TUNING_MIN_EXAMPLES: int = 100
    FINE_TUNING_QUALITY_THRESHOLD: float = 0.9
    FINE_TUNING_AUTO_TRIGGER: bool = False
    
    # Google Cloud Run Configuration
    CLOUD_RUN_ENABLED: bool = False
    CLOUD_RUN_REGION: str = "us-central1"
    CLOUD_RUN_MAX_INSTANCES: int = 100
    CLOUD_RUN_MIN_INSTANCES: int = 1
    CLOUD_RUN_CPU_LIMIT: str = "2"
    CLOUD_RUN_MEMORY_LIMIT: str = "4Gi"
    
    # Advanced Performance Settings
    PERF_ENABLE_PROFILING: bool = False
    PERF_METRICS_RETENTION_DAYS: int = 30
    PERF_SLOW_QUERY_THRESHOLD_MS: int = 1000
    PERF_ENABLE_QUERY_OPTIMIZATION: bool = True
    
    # Cost Optimization Settings
    COST_OPTIMIZATION_ENABLED: bool = True
    COST_MAX_PER_REQUEST: float = 0.10  # USD
    COST_DAILY_BUDGET_LIMIT: float = 100.0  # USD
    COST_ENABLE_BUDGET_ALERTS: bool = True
    
    # Development and Debug Settings
    DEV_ENABLE_AI_BRAIN_DEBUG: bool = False
    DEV_ENABLE_MEMORY_DEBUG: bool = False
    DEV_ENABLE_ROUTING_DEBUG: bool = False
    DEV_MOCK_PROVIDERS: bool = False
    DEV_MOCK_MEMORY: bool = False

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info) -> str:
        """Ensure SECRET_KEY is set properly in production."""
        # Get environment from raw values
        env = info.data.get("ENVIRONMENT", "development")

        if env == "production" and v == "MUST_SET_IN_ENV":
            raise ValueError(
                "SECRET_KEY must be set via environment variable in production. "
                "Generate a secure key with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )

        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters for security. "
                f"Current length: {len(v)}"
            )

        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str, info) -> str:
        """Ensure database credentials are not defaults in production."""
        env = info.data.get("ENVIRONMENT", "development")

        if env == "production":
            # Check for common default credentials
            dangerous_patterns = ["research:research123", "postgres:postgres", ":password@"]
            for pattern in dangerous_patterns:
                if pattern in v:
                    raise ValueError(
                        f"DATABASE_URL contains default credentials ('{pattern}') in production. "
                        "Set secure credentials via environment variable."
                    )

        return v

    def get_ai_brain_config(self) -> Dict[str, Any]:
        """Get complete AI Brain configuration as a dictionary."""
        return {
            # MASR Configuration
            "masr": {
                "enabled": self.MASR_ENABLED,
                "default_strategy": self.MASR_DEFAULT_STRATEGY,
                "enable_caching": self.MASR_ENABLE_CACHING,
                "enable_adaptive": self.MASR_ENABLE_ADAPTIVE,
                "complexity_weights": self.MASR_COMPLEXITY_WEIGHTS,
                "complexity_thresholds": self.MASR_COMPLEXITY_THRESHOLDS,
            },
            
            # Model Providers Configuration
            "providers": {
                "enabled": self.MODEL_PROVIDERS_ENABLED,
                "deepseek": {
                    "enabled": self.DEEPSEEK_ENABLED,
                    "api_key": self.DEEPSEEK_API_KEY,
                    "endpoint": self.DEEPSEEK_ENDPOINT,
                    "default_model": self.DEEPSEEK_DEFAULT_MODEL,
                },
                "llama": {
                    "enabled": self.LLAMA_ENABLED,
                    "endpoint": self.LLAMA_ENDPOINT,
                    "default_model": self.LLAMA_DEFAULT_MODEL,
                    "keep_alive": self.LLAMA_KEEP_ALIVE,
                },
                "gemini": {
                    "enabled": self.GEMINI_ENABLED,
                    "api_key": self.GEMINI_API_KEY,
                    "default_model": self.GEMINI_DEFAULT_MODEL,
                },
            },
            
            # Model Router Configuration
            "model_router": {
                "health_check_interval": self.MODEL_ROUTER_HEALTH_CHECK_INTERVAL,
                "max_retries": self.MODEL_ROUTER_MAX_RETRIES,
                "enable_fallback": self.MODEL_ROUTER_ENABLE_FALLBACK,
            },
            
            # Memory System Configuration
            "memory": {
                "enabled": self.MEMORY_SYSTEM_ENABLED,
                "enable_cross_tier": self.MEMORY_ENABLE_CROSS_TIER,
                "max_recall_items": self.MEMORY_MAX_RECALL_ITEMS,
                "consolidation_interval": self.MEMORY_CONSOLIDATION_INTERVAL,
                
                "working_memory": {
                    "enabled": self.WORKING_MEMORY_ENABLED,
                    "redis_url": self.REDIS_URL,
                    "default_ttl": self.WORKING_MEMORY_DEFAULT_TTL,
                    "max_memory_mb": self.WORKING_MEMORY_MAX_MEMORY_MB,
                    "cleanup_interval": self.WORKING_MEMORY_CLEANUP_INTERVAL,
                    "conversation_ttl": self.WORKING_MEMORY_CONVERSATION_TTL,
                    "max_messages_in_context": self.WORKING_MEMORY_MAX_MESSAGES_IN_CONTEXT,
                },
                
                "episodic_memory": {
                    "enabled": self.EPISODIC_MEMORY_ENABLED,
                    "database_url": self.DATABASE_URL,
                    "retention_days": self.EPISODIC_MEMORY_RETENTION_DAYS,
                    "max_session_duration_hours": self.EPISODIC_MEMORY_MAX_SESSION_DURATION_HOURS,
                },
                
                "semantic_memory": {
                    "enabled": self.SEMANTIC_MEMORY_ENABLED,
                    "vector_db_url": self.SEMANTIC_MEMORY_VECTOR_DB_URL,
                    "collection_name": self.SEMANTIC_MEMORY_COLLECTION_NAME,
                    "embedding_model": self.SEMANTIC_MEMORY_EMBEDDING_MODEL,
                    "embedding_dimension": self.SEMANTIC_MEMORY_EMBEDDING_DIMENSION,
                },
                
                "procedural_memory": {
                    "enabled": self.PROCEDURAL_MEMORY_ENABLED,
                    "storage_path": self.PROCEDURAL_MEMORY_STORAGE_PATH,
                    "min_confidence": self.PROCEDURAL_MEMORY_MIN_CONFIDENCE,
                    "min_usage_promotion": self.PROCEDURAL_MEMORY_MIN_USAGE_PROMOTION,
                },
            },
            
            # Agent System Configuration
            "agents": {
                "max_concurrent": self.AGENTS_MAX_CONCURRENT,
                "max_supervisors": self.AGENTS_MAX_SUPERVISORS,
                "max_workers_per_supervisor": self.AGENTS_MAX_WORKERS_PER_SUPERVISOR,
                "enable_dynamic_spawning": self.AGENTS_ENABLE_DYNAMIC_SPAWNING,
                "default_timeout": self.AGENTS_DEFAULT_TIMEOUT,
                "max_retries": self.AGENTS_MAX_RETRIES,
                
                "hierarchical": {
                    "enabled": self.HIERARCHICAL_AGENTS_ENABLED,
                    "max_depth": self.HIERARCHICAL_MAX_DEPTH,
                    "enable_auto_scaling": self.HIERARCHICAL_ENABLE_AUTO_SCALING,
                    "scale_up_threshold": self.HIERARCHICAL_SCALE_UP_THRESHOLD,
                    "scale_down_threshold": self.HIERARCHICAL_SCALE_DOWN_THRESHOLD,
                },
                
                "communication": {
                    "talkhier_enabled": self.TALKHIER_ENABLED,
                    "max_refinement_rounds": self.TALKHIER_MAX_REFINEMENT_ROUNDS,
                    "consensus_threshold": self.TALKHIER_CONSENSUS_THRESHOLD,
                    "enable_hierarchical_refinement": self.TALKHIER_ENABLE_HIERARCHICAL_REFINEMENT,
                },
            },
            
            # Learning and Improvement
            "learning": {
                "self_improvement": {
                    "enabled": self.SELF_IMPROVEMENT_ENABLED,
                    "reflection_frequency": self.SELF_IMPROVEMENT_REFLECTION_FREQUENCY,
                    "performance_threshold": self.SELF_IMPROVEMENT_PERFORMANCE_THRESHOLD,
                    "enable_prompt_optimization": self.SELF_IMPROVEMENT_ENABLE_PROMPT_OPTIMIZATION,
                },
                
                "fine_tuning": {
                    "enabled": self.FINE_TUNING_ENABLED,
                    "min_examples": self.FINE_TUNING_MIN_EXAMPLES,
                    "quality_threshold": self.FINE_TUNING_QUALITY_THRESHOLD,
                    "auto_trigger": self.FINE_TUNING_AUTO_TRIGGER,
                },
            },
            
            # Performance and Cost
            "performance": {
                "enable_profiling": self.PERF_ENABLE_PROFILING,
                "metrics_retention_days": self.PERF_METRICS_RETENTION_DAYS,
                "slow_query_threshold_ms": self.PERF_SLOW_QUERY_THRESHOLD_MS,
                "enable_query_optimization": self.PERF_ENABLE_QUERY_OPTIMIZATION,
            },
            
            "cost_optimization": {
                "enabled": self.COST_OPTIMIZATION_ENABLED,
                "max_per_request": self.COST_MAX_PER_REQUEST,
                "daily_budget_limit": self.COST_DAILY_BUDGET_LIMIT,
                "enable_budget_alerts": self.COST_ENABLE_BUDGET_ALERTS,
            },
            
            # Cloud Run Configuration
            "cloud_run": {
                "enabled": self.CLOUD_RUN_ENABLED,
                "region": self.CLOUD_RUN_REGION,
                "max_instances": self.CLOUD_RUN_MAX_INSTANCES,
                "min_instances": self.CLOUD_RUN_MIN_INSTANCES,
                "cpu_limit": self.CLOUD_RUN_CPU_LIMIT,
                "memory_limit": self.CLOUD_RUN_MEMORY_LIMIT,
            },
        }
    
    def get_research_platform_config(self) -> Dict[str, Any]:
        """Get legacy research platform configuration for backward compatibility."""
        return {
            "database_url": self.DATABASE_URL,
            "redis_url": self.REDIS_URL,
            "gemini_api_key": self.GEMINI_API_KEY,
            "temporal_host": self.TEMPORAL_HOST,
            "temporal_namespace": self.TEMPORAL_NAMESPACE,
            "mcp_port": self.MCP_PORT,
            "mcp_tools_enabled": self.MCP_TOOLS_ENABLED,
            "enable_cache": self.ENABLE_CACHE,
            "enable_rate_limiting": self.ENABLE_RATE_LIMITING,
            "max_requests_per_minute": self.MAX_REQUESTS_PER_MINUTE,
            "worker_concurrency": self.WORKER_CONCURRENCY,
            "task_timeout_seconds": self.TASK_TIMEOUT_SECONDS,
        }

    def is_ai_brain_mode(self) -> bool:
        """Check if running in AI Brain mode."""
        return self.AI_BRAIN_ENABLED and self.AI_BRAIN_MODE in ["hybrid", "brain_only"]
    
    def is_research_only_mode(self) -> bool:
        """Check if running in research-only mode."""
        return self.AI_BRAIN_MODE == "research_only"


# Create settings instance
settings = Settings()
