"""
MASR Service

Standalone service for the Multi-Agent System Router that can run independently
and provide routing decisions via HTTP API. Designed for microservice deployments.

Features:
- HTTP API for routing decisions
- Health monitoring and metrics
- Redis integration for caching and state
- Service discovery and load balancing support
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime
from typing import Any

import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .masr import MASRouter, RoutingStrategy

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RoutingRequest(BaseModel):
    """Request model for MASR routing."""

    query: str = Field(..., description="Query to route")
    context: dict[str, Any] | None = Field(default_factory=dict, description="Additional context")
    strategy: str | None = Field(None, description="Routing strategy override")
    constraints: dict[str, Any] | None = Field(default_factory=dict, description="Routing constraints")


class RoutingResponse(BaseModel):
    """Response model for MASR routing."""
    
    query_id: str
    timestamp: str
    collaboration_mode: str
    supervisor_type: str
    worker_count: int
    worker_types: list[str]
    estimated_cost: float
    estimated_latency_ms: int
    estimated_quality: float
    confidence_score: float
    routing_strategy: str
    complexity_analysis: dict[str, Any]


class HealthResponse(BaseModel):
    """Health check response model."""
    
    status: str
    timestamp: str
    version: str = "1.0.0"
    components: dict[str, str]
    metrics: dict[str, Any]


class MASRService:
    """MASR microservice implementation."""
    
    def __init__(self) -> None:
        """Initialize MASR service."""
        self.app = FastAPI(
            title="Cerebro MASR Service",
            description="Multi-Agent System Router for intelligent query routing",
            version="1.0.0",
            docs_url="/docs",
            redoc_url="/redoc",
        )

        self.host = os.getenv("MASR_HOST", "0.0.0.0")
        self.port = int(os.getenv("MASR_PORT", "9100"))
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.masr_router: MASRouter | None = None
        self.redis_client: redis.Redis[bytes] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self.service_stats: dict[str, Any] = {
            "requests_total": 0,
            "requests_successful": 0,
            "requests_failed": 0,
            "average_response_time_ms": 0.0,
            "started_at": datetime.now().isoformat(),
        }

        self._setup_middleware()
        self._setup_routes()
        self._setup_shutdown_handlers()

    def _setup_middleware(self) -> None:
        """Setup FastAPI middleware."""
        
        # CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        @self.app.middleware("http")
        async def logging_middleware(request: Any, call_next: Any) -> Any:
            start_time = datetime.now()
            response = await call_next(request)
            process_time = (datetime.now() - start_time).total_seconds() * 1000

            logger.info(
                f"{request.method} {request.url.path} - "
                f"Status: {response.status_code} - "
                f"Time: {process_time:.2f}ms"
            )

            return response

    def _setup_routes(self) -> None:
        """Setup FastAPI routes."""
        
        @self.app.get("/health", response_model=HealthResponse)
        async def health_check() -> HealthResponse:
            """Health check endpoint."""

            components_health = {
                "masr_router": "healthy" if self.masr_router else "unavailable",
                "redis": "healthy" if self.redis_client else "unavailable",
            }
            
            # Test Redis connection if available
            if self.redis_client:
                try:
                    await self.redis_client.ping()
                    components_health["redis"] = "healthy"
                except Exception:
                    components_health["redis"] = "unhealthy"
            
            # Test MASR router if available
            if self.masr_router:
                try:
                    masr_health = await self.masr_router.health_check()
                    components_health["masr_router"] = masr_health.get("status", "unknown")
                except Exception:
                    components_health["masr_router"] = "unhealthy"
            
            overall_status = "healthy" if all(
                status in ["healthy", "warning"] for status in components_health.values()
            ) else "unhealthy"
            
            return HealthResponse(
                status=overall_status,
                timestamp=datetime.now().isoformat(),
                components=components_health,
                metrics=self.service_stats,
            )
        
        @self.app.post("/route", response_model=RoutingResponse)
        async def route_query(request: RoutingRequest) -> RoutingResponse:
            """Route a query using MASR intelligence."""

            if not self.masr_router:
                raise HTTPException(status_code=503, detail="MASR router not available")
            
            start_time = datetime.now()
            self.service_stats["requests_total"] = int(self.service_stats["requests_total"]) + 1
            
            try:
                # Parse strategy if provided
                strategy = None
                if request.strategy:
                    try:
                        strategy = RoutingStrategy(request.strategy)
                    except ValueError:
                        raise HTTPException(
                            status_code=400, 
                            detail=f"Invalid routing strategy: {request.strategy}"
                        ) from None
                
                # Get routing decision
                routing_decision = await self.masr_router.route(
                    query=request.query,
                    context=request.context,
                    strategy=strategy,
                    constraints=request.constraints,
                )
                
                process_time = (datetime.now() - start_time).total_seconds() * 1000
                self.service_stats["requests_successful"] = int(self.service_stats["requests_successful"]) + 1
                self._update_average_response_time(process_time)
                
                # Build response
                return RoutingResponse(
                    query_id=routing_decision.query_id,
                    timestamp=routing_decision.timestamp.isoformat(),
                    collaboration_mode=routing_decision.collaboration_mode.value,
                    supervisor_type=routing_decision.agent_allocation.supervisor_type,
                    worker_count=routing_decision.agent_allocation.worker_count,
                    worker_types=routing_decision.agent_allocation.worker_types,
                    estimated_cost=routing_decision.estimated_cost,
                    estimated_latency_ms=routing_decision.estimated_latency_ms,
                    estimated_quality=routing_decision.estimated_quality,
                    confidence_score=routing_decision.confidence_score,
                    routing_strategy=strategy.value if strategy else "auto",
                    complexity_analysis={
                        "level": routing_decision.complexity_analysis.level.value,
                        "score": routing_decision.complexity_analysis.score,
                        "uncertainty": routing_decision.complexity_analysis.uncertainty,
                        "domains": [d.value if hasattr(d, 'value') else str(d) 
                                  for d in routing_decision.complexity_analysis.domains],
                        "subtask_count": routing_decision.complexity_analysis.subtask_count,
                    }
                )
                
            except Exception as e:
                logger.error(f"Routing failed for query '{request.query[:100]}...': {e}")
                self.service_stats["requests_failed"] = int(self.service_stats["requests_failed"]) + 1
                raise HTTPException(status_code=500, detail=f"Routing failed: {e!s}") from e
        
        @self.app.get("/metrics")
        async def get_metrics() -> dict[str, Any]:
            """Get service metrics."""

            metrics = self.service_stats.copy()
            
            if self.masr_router:
                masr_metrics = await self.masr_router.get_metrics()
                metrics["masr_router"] = masr_metrics.__dict__
            
            return metrics
        
        @self.app.get("/stats")
        async def get_detailed_stats() -> dict[str, Any]:
            """Get detailed service statistics."""

            started_at_str = str(self.service_stats["started_at"])
            stats: dict[str, Any] = {
                "service": self.service_stats.copy(),
                "environment": self.environment,
                "uptime_seconds": (
                    datetime.now() -
                    datetime.fromisoformat(started_at_str)
                ).total_seconds(),
            }
            
            if self.masr_router:
                stats["masr_router"] = await self.masr_router.get_metrics()
            
            return stats

    def _setup_shutdown_handlers(self) -> None:
        """Setup graceful shutdown handlers."""
        
        @self.app.on_event("startup")
        async def startup_event() -> None:
            """Initialize service components on startup."""
            await self._initialize_components()
        
        @self.app.on_event("shutdown")
        async def shutdown_event() -> None:
            """Cleanup on service shutdown."""
            await self._cleanup_components()
    
    async def _initialize_components(self) -> None:
        """Initialize MASR service components."""

        logger.info("Initializing MASR service components...")
        
        try:
            # Initialize Redis client
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")
            self.redis_client = redis.from_url(redis_url)
            await self.redis_client.ping()
            logger.info("Redis connection established")
            
            # Initialize MASR router
            masr_config = {
                "default_strategy": os.getenv("MASR_DEFAULT_STRATEGY", "balanced"),
                "enable_adaptive": os.getenv("MASR_ENABLE_ADAPTIVE", "true").lower() == "true",
                "enable_caching": os.getenv("MASR_ENABLE_CACHING", "true").lower() == "true",
                "quality_threshold": float(os.getenv("MASR_QUALITY_THRESHOLD", "0.85")),
                "max_agents": int(os.getenv("MASR_MAX_AGENTS", "10")),
                "enable_learning": os.getenv("MASR_ENABLE_LEARNING", "true").lower() == "true",
            }
            
            self.masr_router = MASRouter(
                config=masr_config,
                model_config_manager=None  # Would be injected in full deployment
            )
            
            logger.info("MASR router initialized successfully")
            
            # Perform initial health check
            health = await self.masr_router.health_check()
            logger.info(f"MASR health check: {health['status']}")
            
        except Exception as e:
            logger.error(f"Failed to initialize MASR service components: {e}")
            raise
    
    async def _cleanup_components(self) -> None:
        """Cleanup service components."""

        logger.info("Cleaning up MASR service components...")
        
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Redis connection closed")
    
    def _update_average_response_time(self, response_time_ms: float) -> None:
        """Update average response time metric."""

        current_avg = float(self.service_stats["average_response_time_ms"])
        total_requests = int(self.service_stats["requests_successful"])

        if total_requests == 1:
            self.service_stats["average_response_time_ms"] = response_time_ms
        else:
            new_avg = ((current_avg * (total_requests - 1)) + response_time_ms) / total_requests
            self.service_stats["average_response_time_ms"] = new_avg

    def run(self) -> None:
        """Run MASR service."""
        
        logger.info(f"Starting MASR service on {self.host}:{self.port}")
        
        # Configure uvicorn
        uvicorn_config = {
            "host": self.host,
            "port": self.port,
            "log_level": os.getenv("LOG_LEVEL", "info").lower(),
            "access_log": True,
            "reload": self.environment == "development",
        }
        
        # Production-specific settings
        if self.environment == "production":
            uvicorn_config.update({
                "workers": int(os.getenv("MASR_WORKERS", "2")),
                "worker_class": "uvicorn.workers.UvicornWorker",
                "max_requests": int(os.getenv("MASR_MAX_REQUESTS", "1000")),
                "max_requests_jitter": int(os.getenv("MASR_MAX_REQUESTS_JITTER", "50")),
            })
        
        def signal_handler(signum: int, frame: Any) -> None:
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            task = asyncio.create_task(self._cleanup_components())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        # Start service
        uvicorn.run(self.app, **uvicorn_config)  # type: ignore[arg-type]


# Create global service instance
masr_service = MASRService()


# Entry point for module execution
async def main() -> None:
    """Main entry point for MASR service."""

    logger.info("Cerebro MASR Service Starting...")
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    logger.info(f"Port: {os.getenv('MASR_PORT', '9100')}")
    
    try:
        # Run service
        masr_service.run()
        
    except Exception as e:
        logger.error(f"MASR service failed to start: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("MASR service interrupted by user")
    finally:
        logger.info("MASR service shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())