"""
Multi-Tier Memory System

Coordinates and orchestrates all memory tiers to provide intelligent,
context-aware memory management for the Cerebro AI Brain system.

Integrates:
- Working Memory: Short-term context and active conversation state
- Episodic Memory: Event-based memory of interactions and experiences
- Semantic Memory: Long-term knowledge storage with vector search
- Procedural Memory: Learned patterns, workflows, and optimizations

Provides unified memory interface for agents with intelligent retrieval,
cross-tier relationships, and automatic memory management.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .episodic_memory import Episode, EpisodeQuery, EpisodicMemoryManager, EventType
from .procedural_memory import ProceduralMemoryManager, Procedure
from .semantic_memory import SemanticMemoryManager
from .working_memory import ConversationContext, WorkingMemoryManager

logger = logging.getLogger(__name__)


class MemoryTier(Enum):
    """Memory tier types."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


@dataclass
class MemoryContext:
    """Context for memory operations."""

    session_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    domain: str | None = None
    task_type: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class MemoryResult:
    """Result from memory retrieval."""

    tier: MemoryTier
    content: Any
    relevance_score: float
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntelligentRecall:
    """Result from intelligent memory recall."""

    primary_results: list[MemoryResult] = field(default_factory=list)
    supporting_context: dict[str, Any] = field(default_factory=dict)
    related_episodes: list[Episode] = field(default_factory=list)
    applicable_procedures: list[Procedure] = field(default_factory=list)
    confidence_score: float = 0.0
    recall_reasoning: str = ""


class MultiTierMemorySystem:
    """
    Unified multi-tier memory system for Cerebro AI Brain.

    Provides intelligent memory management across all tiers with:
    - Cross-tier memory relationships and retrieval
    - Context-aware memory operations
    - Automatic memory lifecycle management
    - Performance optimization and caching
    - Memory consolidation and cleanup
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize multi-tier memory system."""
        self.config = config

        # Initialize memory managers
        self.working_memory = WorkingMemoryManager(config.get("working_memory", {}))
        self.episodic_memory = EpisodicMemoryManager(config.get("episodic_memory", {}))
        self.semantic_memory = SemanticMemoryManager(config.get("semantic_memory", {}))
        self.procedural_memory = ProceduralMemoryManager(
            config.get("procedural_memory", {})
        )

        # System configuration
        self.enable_cross_tier_retrieval = config.get("enable_cross_tier", True)
        self.max_recall_items = config.get("max_recall_items", 10)
        self.memory_consolidation_interval = config.get(
            "consolidation_interval", 300
        )  # 5 minutes

        # Performance tracking
        self.total_operations = 0
        self.cross_tier_retrievals = 0
        self.consolidation_count = 0

        # Background tasks
        self._consolidation_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Initialize all memory tiers."""

        logger.info("Initializing multi-tier memory system...")

        # Initialize all memory managers
        await self.working_memory.initialize()
        await self.episodic_memory.initialize()
        await self.semantic_memory.initialize()
        await self.procedural_memory.initialize()

        # Start background consolidation task
        if self.memory_consolidation_interval > 0:
            self._consolidation_task = asyncio.create_task(self._consolidation_loop())

        logger.info("Multi-tier memory system initialized successfully")

    async def store_interaction(
        self,
        context: MemoryContext,
        interaction_data: dict[str, Any],
        performance_score: float | None = None,
    ) -> bool:
        """
        Store an interaction across appropriate memory tiers.

        Args:
            context: Memory context
            interaction_data: Interaction data to store
            performance_score: Optional performance score

        Returns:
            True if stored successfully
        """

        try:
            self.total_operations += 1

            # Store in working memory (conversation context)
            if context.session_id:
                success_working = await self._store_in_working_memory(
                    context, interaction_data
                )
            else:
                success_working = True

            # Store in episodic memory (interaction history)
            success_episodic = await self._store_in_episodic_memory(
                context, interaction_data, performance_score
            )

            # Extract and store knowledge in semantic memory
            success_semantic = await self._extract_and_store_knowledge(
                context, interaction_data
            )

            # Learn procedures if applicable
            success_procedural = await self._learn_procedures(
                context, interaction_data, performance_score
            )

            return all(
                [
                    success_working,
                    success_episodic,
                    success_semantic,
                    success_procedural,
                ]
            )

        except Exception as e:
            logger.error(f"Failed to store interaction: {e}")
            return False

    async def intelligent_recall(
        self, query: str, context: MemoryContext, max_results: int | None = None
    ) -> IntelligentRecall:
        """
        Perform intelligent memory recall across all tiers.

        Args:
            query: Query for memory recall
            context: Memory context
            max_results: Maximum results to return

        Returns:
            Intelligent recall results
        """

        try:
            self.total_operations += 1
            max_results = max_results or self.max_recall_items

            # Parallel retrieval from all tiers
            recall_tasks = [
                self._recall_from_working_memory(query, context),
                self._recall_from_episodic_memory(query, context),
                self._recall_from_semantic_memory(query, context),
                self._recall_from_procedural_memory(query, context),
            ]

            if self.enable_cross_tier_retrieval:
                self.cross_tier_retrievals += 1

            # Wait for all retrievals
            working_results, episodic_results, semantic_results, procedural_results = (
                await asyncio.gather(*recall_tasks, return_exceptions=True)
            )

            # Handle any exceptions
            for i, result in enumerate(
                [
                    working_results,
                    episodic_results,
                    semantic_results,
                    procedural_results,
                ]
            ):
                if isinstance(result, BaseException):
                    logger.error(f"Memory tier {i} retrieval failed: {result}")
                    if i == 0:
                        working_results = []
                    elif i == 1:
                        episodic_results = []
                    elif i == 2:
                        semantic_results = []
                    elif i == 3:
                        procedural_results = []

            # Combine and rank results
            recall = self._combine_and_rank_results(
                working_results if isinstance(working_results, list) else [],
                episodic_results if isinstance(episodic_results, list) else [],
                semantic_results if isinstance(semantic_results, list) else [],
                procedural_results if isinstance(procedural_results, list) else [],
                query,
                max_results,
            )

            return recall

        except Exception as e:
            logger.error(f"Intelligent recall failed: {e}")
            return IntelligentRecall(
                confidence_score=0.0, recall_reasoning="Recall failed"
            )

    async def get_conversation_context(
        self, session_id: str
    ) -> ConversationContext | None:
        """Get conversation context from working memory."""
        return await self.working_memory.retrieve_conversation_context(session_id)

    async def update_conversation_context(
        self, session_id: str, updates: dict[str, Any]
    ) -> bool:
        """Update conversation context in working memory."""
        return await self.working_memory.update_conversation_context(
            session_id, updates
        )

    async def add_message_to_context(
        self, session_id: str, message: dict[str, Any]
    ) -> bool:
        """Add message to conversation context."""
        return await self.working_memory.add_message_to_context(session_id, message)

    async def store_agent_state(
        self, agent_id: str, state: dict[str, Any], session_id: str | None = None
    ) -> bool:
        """Store agent state in working memory."""
        return await self.working_memory.store_agent_state(agent_id, state, session_id)

    async def retrieve_agent_state(
        self, agent_id: str, session_id: str | None = None
    ) -> dict[str, Any] | None:
        """Retrieve agent state from working memory."""
        return await self.working_memory.retrieve_agent_state(agent_id, session_id)

    async def store_knowledge(
        self,
        content: str,
        domain: str | None = None,
        source: str | None = None,
        confidence: float = 1.0,
    ) -> str:
        """Store knowledge in semantic memory."""
        return await self.semantic_memory.store_knowledge(
            content, domain=domain, source=source, confidence=confidence
        )

    async def retrieve_knowledge(
        self, query: str, domain: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Retrieve relevant knowledge."""
        return await self.semantic_memory.retrieve_knowledge(
            query, domain=domain, limit=limit
        )

    async def get_best_procedure(
        self,
        task_type: str,
        domain: str | None = None,
        agent_type: str | None = None,
    ) -> Procedure | None:
        """Get best procedure for a task."""
        return await self.procedural_memory.get_best_procedure_for_task(
            task_type, domain=domain, agent_type=agent_type
        )

    async def learn_from_interaction(
        self,
        context: MemoryContext,
        interaction_data: dict[str, Any],
        performance_score: float,
        success: bool,
    ) -> dict[str, Any]:
        """Learn from an interaction across all memory tiers."""

        results: dict[str, Any] = {
            "episodic_stored": False,
            "knowledge_extracted": False,
            "procedure_learned": False,
            "procedure_id": None,
        }

        try:
            # Store episodic memory
            episode = Episode(
                session_id=context.session_id or "unknown",
                user_id=context.user_id,
                agent_id=context.agent_id,
                event_type=EventType.LEARNING_EVENT,
                event_data=interaction_data,
                success=success,
                quality_score=performance_score,
                tags=context.tags,
            )

            results["episodic_stored"] = await self.episodic_memory.store_episode(
                episode
            )

            # Extract knowledge if successful interaction
            if success and performance_score > 0.7:
                knowledge_content = self._extract_knowledge_from_interaction(
                    interaction_data
                )
                if knowledge_content:
                    await self.semantic_memory.store_knowledge(
                        knowledge_content,
                        domain=context.domain,
                        source="learned_interaction",
                        confidence=performance_score,
                    )
                    results["knowledge_extracted"] = True

            # Learn procedure if applicable
            if interaction_data.get("steps") and success:
                procedure_id = await self.procedural_memory.learn_from_episode(
                    {
                        **interaction_data,
                        "domain": context.domain,
                        "agent_type": context.agent_id,
                        "task_type": context.task_type,
                        "episode_id": episode.id,
                    },
                    performance_score,
                    success,
                )

                if procedure_id:
                    results["procedure_learned"] = True
                    results["procedure_id"] = procedure_id

        except Exception as e:
            logger.error(f"Failed to learn from interaction: {e}")

        return results

    async def consolidate_memory(self) -> dict[str, int]:
        """Perform memory consolidation across tiers."""

        consolidation_results = {
            "working_cleaned": 0,
            "episodic_cleaned": 0,
            "semantic_optimized": 0,
            "procedures_optimized": 0,
        }

        try:
            # Clean up expired working memory
            consolidation_results["working_cleaned"] = (
                await self.working_memory.cleanup_expired()
            )

            # Clean up old episodic memories
            consolidation_results["episodic_cleaned"] = (
                await self.episodic_memory.cleanup_old_episodes()
            )

            # Clean up old procedures
            consolidation_results["procedures_optimized"] = (
                await self.procedural_memory.cleanup_old_procedures()
            )

            # TODO: Implement semantic memory optimization
            # This could include:
            # - Removing duplicate knowledge
            # - Consolidating similar items
            # - Updating relevance scores

            self.consolidation_count += 1
            logger.info(f"Memory consolidation completed: {consolidation_results}")

        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")

        return consolidation_results

    async def get_memory_stats(self) -> dict[str, Any]:
        """Get comprehensive memory statistics."""

        # Get stats from all tiers
        working_stats = await self.working_memory.get_memory_stats()
        episodic_stats = await self.episodic_memory.get_memory_stats()
        semantic_stats = await self.semantic_memory.get_memory_stats()
        procedural_stats = await self.procedural_memory.get_memory_stats()

        return {
            "system": {
                "total_operations": self.total_operations,
                "cross_tier_retrievals": self.cross_tier_retrievals,
                "consolidation_count": self.consolidation_count,
                "cross_tier_enabled": self.enable_cross_tier_retrieval,
            },
            "working_memory": working_stats,
            "episodic_memory": episodic_stats,
            "semantic_memory": semantic_stats,
            "procedural_memory": procedural_stats,
        }

    async def purge_user_data(self, user_id: str) -> dict[str, int]:
        """Purge user-scoped data from memory tiers that store user interactions."""

        working_deleted = await self.working_memory.delete_by_user_id(user_id)
        episodic_deleted = await self.episodic_memory.delete_by_user_id(user_id)
        semantic_deleted = await self.semantic_memory.delete_by_user_id(user_id)
        procedural_deleted = await self.procedural_memory.delete_by_user_id(user_id)
        return {
            "working_memory": working_deleted,
            "episodic_memory": episodic_deleted,
            "semantic_memory": semantic_deleted,
            "procedural_memory": procedural_deleted,
        }

    async def _store_in_working_memory(
        self, context: MemoryContext, interaction_data: dict[str, Any]
    ) -> bool:
        """Store interaction in working memory."""

        if not context.session_id:
            return True  # No session to store

        # Update or create conversation context
        conv_context = await self.working_memory.retrieve_conversation_context(
            context.session_id
        )

        if not conv_context:
            conv_context = ConversationContext(
                session_id=context.session_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
            )

        # Add interaction as message
        message = {
            "type": "interaction",
            "data": interaction_data,
            "timestamp": datetime.now().isoformat(),
        }

        conv_context.messages.append(message)

        return await self.working_memory.store_conversation_context(conv_context)

    async def _store_in_episodic_memory(
        self,
        context: MemoryContext,
        interaction_data: dict[str, Any],
        performance_score: float | None,
    ) -> bool:
        """Store interaction in episodic memory."""

        episode = Episode(
            session_id=context.session_id or "unknown",
            user_id=context.user_id,
            agent_id=context.agent_id,
            event_type=EventType.AGENT_RESPONSE,  # Default type
            event_data=interaction_data,
            quality_score=performance_score,
            tags=context.tags,
            metadata={"domain": context.domain, "task_type": context.task_type},
        )

        return await self.episodic_memory.store_episode(episode)

    async def _extract_and_store_knowledge(
        self, context: MemoryContext, interaction_data: dict[str, Any]
    ) -> bool:
        """Extract and store knowledge from interaction."""

        # Simple knowledge extraction (could be enhanced with NLP)
        knowledge_content = self._extract_knowledge_from_interaction(interaction_data)

        if knowledge_content:
            knowledge_id = await self.semantic_memory.store_knowledge(
                knowledge_content,
                domain=context.domain,
                source="interaction",
                confidence=0.8,
            )
            return bool(knowledge_id)

        return True  # No knowledge to extract is not a failure

    async def _learn_procedures(
        self,
        context: MemoryContext,
        interaction_data: dict[str, Any],
        performance_score: float | None,
    ) -> bool:
        """Learn procedures from interaction."""

        # Only learn if there are steps and good performance
        if (
            not interaction_data.get("steps")
            or not performance_score
            or performance_score < 0.7
        ):
            return True

        episode_data = {
            **interaction_data,
            "domain": context.domain,
            "agent_type": context.agent_id,
            "task_type": context.task_type,
        }

        procedure_id = await self.procedural_memory.learn_from_episode(
            episode_data, performance_score, True
        )

        return bool(procedure_id)

    def _extract_knowledge_from_interaction(
        self, interaction_data: dict[str, Any]
    ) -> str | None:
        """Extract knowledge content from interaction data."""

        # Simple extraction - look for knowledge indicators
        if "result" in interaction_data:
            result = interaction_data["result"]
            if isinstance(result, str) and len(result) > 50:
                return result

        if "summary" in interaction_data:
            summary = interaction_data["summary"]
            if isinstance(summary, str):
                return summary

        if "insights" in interaction_data:
            insights = interaction_data["insights"]
            if isinstance(insights, list):
                return ". ".join(str(i) for i in insights)
            elif isinstance(insights, str):
                return insights

        return None

    async def _recall_from_working_memory(
        self, query: str, context: MemoryContext
    ) -> list[MemoryResult]:
        """Recall from working memory."""

        results = []

        try:
            # Get conversation context if session available
            if context.session_id:
                conv_context = await self.working_memory.retrieve_conversation_context(
                    context.session_id
                )

                if conv_context:
                    # Simple relevance check on recent messages
                    query_lower = query.lower()

                    for message in conv_context.messages[-10:]:  # Last 10 messages
                        message_content = str(message.get("data", message))
                        if any(
                            word in message_content.lower()
                            for word in query_lower.split()
                        ):
                            result = MemoryResult(
                                tier=MemoryTier.WORKING,
                                content=message,
                                relevance_score=0.8,  # High relevance for working memory
                                timestamp=datetime.fromisoformat(
                                    message.get("timestamp", datetime.now().isoformat())
                                ),
                                metadata={"session_id": context.session_id},
                            )
                            results.append(result)

        except Exception as e:
            logger.error(f"Working memory recall failed: {e}")

        return results

    async def _recall_from_episodic_memory(
        self, query: str, context: MemoryContext
    ) -> list[MemoryResult]:
        """Recall from episodic memory."""

        results = []

        try:
            # Query recent episodes
            episode_query = EpisodeQuery(
                user_id=context.user_id,
                agent_id=context.agent_id,
                start_time=datetime.now() - timedelta(days=7),
                limit=5,
            )

            episodes = await self.episodic_memory.retrieve_episodes(episode_query)

            # Simple relevance matching
            query_lower = query.lower()

            for episode in episodes:
                episode_content = str(episode.event_data)
                if any(word in episode_content.lower() for word in query_lower.split()):
                    result = MemoryResult(
                        tier=MemoryTier.EPISODIC,
                        content=episode,
                        relevance_score=0.6,  # Medium relevance
                        timestamp=episode.timestamp,
                        metadata={"episode_id": episode.id},
                    )
                    results.append(result)

        except Exception as e:
            logger.error(f"Episodic memory recall failed: {e}")

        return results

    async def _recall_from_semantic_memory(
        self, query: str, context: MemoryContext
    ) -> list[MemoryResult]:
        """Recall from semantic memory."""

        results = []

        try:
            knowledge_items = await self.semantic_memory.retrieve_knowledge(
                query, domain=context.domain, limit=3
            )

            for item in knowledge_items:
                result = MemoryResult(
                    tier=MemoryTier.SEMANTIC,
                    content=item,
                    relevance_score=item["similarity"],
                    timestamp=datetime.fromisoformat(item["created_at"]),
                    metadata={"domain": item["domain"]},
                )
                results.append(result)

        except Exception as e:
            logger.error(f"Semantic memory recall failed: {e}")

        return results

    async def _recall_from_procedural_memory(
        self, query: str, context: MemoryContext
    ) -> list[MemoryResult]:
        """Recall from procedural memory."""

        results = []

        try:
            # Get relevant procedures
            if context.task_type:
                procedure = await self.procedural_memory.get_best_procedure_for_task(
                    context.task_type,
                    domain=context.domain,
                    agent_type=context.agent_id,
                )

                if procedure:
                    result = MemoryResult(
                        tier=MemoryTier.PROCEDURAL,
                        content=procedure,
                        relevance_score=procedure.confidence,
                        timestamp=procedure.last_updated,
                        metadata={"procedure_id": procedure.id},
                    )
                    results.append(result)

        except Exception as e:
            logger.error(f"Procedural memory recall failed: {e}")

        return results

    def _combine_and_rank_results(
        self,
        working_results: list[MemoryResult],
        episodic_results: list[MemoryResult],
        semantic_results: list[MemoryResult],
        procedural_results: list[MemoryResult],
        query: str,
        max_results: int,
    ) -> IntelligentRecall:
        """Combine and rank results from all memory tiers."""

        # Combine all results
        all_results = (
            working_results + episodic_results + semantic_results + procedural_results
        )

        # Sort by relevance score and recency
        all_results.sort(
            key=lambda r: (r.relevance_score, r.timestamp.timestamp()), reverse=True
        )

        # Take top results
        primary_results = all_results[:max_results]

        # Extract supporting context
        supporting_context = {
            "working_memory_items": len(working_results),
            "episodic_episodes": len(episodic_results),
            "semantic_knowledge": len(semantic_results),
            "applicable_procedures": len(procedural_results),
        }

        # Extract related episodes and procedures
        related_episodes = [
            r.content for r in episodic_results if isinstance(r.content, Episode)
        ]
        applicable_procedures = [
            r.content for r in procedural_results if isinstance(r.content, Procedure)
        ]

        # Calculate confidence based on result quality and quantity
        confidence = min(
            len(primary_results) / max_results * 0.5  # Quantity factor
            + sum(r.relevance_score for r in primary_results)
            / max(len(primary_results), 1)
            * 0.5,  # Quality factor
            1.0,
        )

        # Generate reasoning
        reasoning = f"Retrieved {len(primary_results)} relevant items from "
        active_tiers = []
        if working_results:
            active_tiers.append("working memory")
        if episodic_results:
            active_tiers.append("episodic memory")
        if semantic_results:
            active_tiers.append("semantic knowledge")
        if procedural_results:
            active_tiers.append("learned procedures")
        reasoning += ", ".join(active_tiers)

        return IntelligentRecall(
            primary_results=primary_results,
            supporting_context=supporting_context,
            related_episodes=related_episodes,
            applicable_procedures=applicable_procedures,
            confidence_score=confidence,
            recall_reasoning=reasoning,
        )

    async def _consolidation_loop(self) -> None:
        """Background memory consolidation loop."""

        while True:
            try:
                await asyncio.sleep(self.memory_consolidation_interval)
                await self.consolidate_memory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Memory consolidation error: {e}")

    async def close(self) -> None:
        """Close memory system and cleanup resources."""

        if self._consolidation_task:
            self._consolidation_task.cancel()

        # Close all memory managers
        await self.working_memory.close()
        await self.episodic_memory.close()
        await self.semantic_memory.close()
        # Procedural memory doesn't need explicit closing


__all__ = [
    "IntelligentRecall",
    "MemoryContext",
    "MemoryResult",
    "MemoryTier",
    "MultiTierMemorySystem",
]
