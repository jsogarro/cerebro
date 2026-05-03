"""
Episodic Memory Manager

Manages episodic memory for storing interaction history, user experiences,
and contextual episodes. Uses PostgreSQL for persistent, structured storage
with rich querying capabilities.

Episodic memory stores:
- Complete conversation histories
- User interaction patterns
- Agent decision sequences
- Contextual episodes and events
- Performance feedback and outcomes
- Temporal relationships between events
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

HAS_SQLALCHEMY = False
AsyncSession = None
AsyncEngine = None
Base = None

try:
    from sqlalchemy import (
        JSON,
        Boolean,
        Column,
        DateTime,
        Float,
        Integer,
        String,
        Text,
        and_,  # noqa: F401
        asc,
        desc,
        or_,  # noqa: F401
    )
    from sqlalchemy.ext.asyncio import AsyncEngine as _AsyncEngine
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.orm import declarative_base, sessionmaker  # noqa: F401
    from sqlalchemy.sql import text

    HAS_SQLALCHEMY = True
    AsyncSession = _AsyncSession
    AsyncEngine = _AsyncEngine
    Base = declarative_base()
except ImportError:
    # Fallback if SQLAlchemy not available
    logger.warning("SQLAlchemy not available - episodic memory will use fallback")

# Database models
if HAS_SQLALCHEMY:

    class EpisodicEvent(Base):  # type: ignore
        """Database model for episodic events."""

        __tablename__ = "episodic_events"

        id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
        session_id = Column(String, nullable=False, index=True)
        user_id = Column(String, nullable=True, index=True)
        agent_id = Column(String, nullable=True, index=True)

        # Event details
        event_type = Column(String, nullable=False, index=True)
        event_data = Column(JSON, nullable=False)
        context = Column(JSON, nullable=True)

        # Temporal information
        timestamp = Column(DateTime, nullable=False, default=datetime.now, index=True)
        duration_ms = Column(Integer, nullable=True)
        sequence_number = Column(Integer, nullable=True)

        # Outcome and feedback
        success = Column(Boolean, nullable=True)
        quality_score = Column(Float, nullable=True)
        user_feedback = Column(Text, nullable=True)

        # Metadata
        tags = Column(JSON, nullable=True)
        event_metadata = Column(JSON, nullable=True)

        created_at = Column(DateTime, default=datetime.now)

    class EpisodicSession(Base):  # type: ignore
        """Database model for episodic sessions."""

        __tablename__ = "episodic_sessions"

        session_id = Column(String, primary_key=True)
        user_id = Column(String, nullable=True, index=True)

        # Session details
        session_type = Column(String, nullable=True)
        start_time = Column(DateTime, nullable=False, index=True)
        end_time = Column(DateTime, nullable=True)
        duration_ms = Column(Integer, nullable=True)

        # Summary statistics
        event_count = Column(Integer, default=0)
        success_rate = Column(Float, nullable=True)
        avg_quality_score = Column(Float, nullable=True)

        # Session outcome
        session_outcome = Column(String, nullable=True)
        user_satisfaction = Column(Float, nullable=True)
        notes = Column(Text, nullable=True)

        # Metadata
        session_metadata = Column(JSON, nullable=True)
        created_at = Column(DateTime, default=datetime.now)

else:
    EpisodicEvent = None  # type: ignore
    EpisodicSession = None  # type: ignore


class EventType(Enum):
    """Types of episodic events."""

    CONVERSATION_START = "conversation_start"
    CONVERSATION_END = "conversation_end"
    USER_MESSAGE = "user_message"
    AGENT_RESPONSE = "agent_response"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    ERROR_OCCURRED = "error_occurred"
    FEEDBACK_RECEIVED = "feedback_received"
    DECISION_MADE = "decision_made"
    LEARNING_EVENT = "learning_event"


@dataclass
class Episode:
    """Individual episode in episodic memory."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user_id: str | None = None
    agent_id: str | None = None

    # Event details
    event_type: EventType = EventType.USER_MESSAGE
    event_data: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    # Temporal information
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: int | None = None
    sequence_number: int | None = None

    # Outcome and feedback
    success: bool | None = None
    quality_score: float | None = None
    user_feedback: str | None = None

    # Metadata
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeQuery:
    """Query parameters for episode retrieval."""

    session_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    event_types: list[EventType] | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    tags: list[str] | None = None
    min_quality_score: float | None = None
    limit: int = 100
    offset: int = 0
    order_by: str = "timestamp"
    order_direction: str = "desc"


class EpisodicMemoryManager:
    """
    Manages episodic memory for storing and retrieving interaction history.

    Episodic memory provides:
    - Persistent storage of user interactions
    - Temporal querying of past events
    - Pattern analysis across sessions
    - Learning from historical feedback
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize episodic memory manager."""
        self.config = config

        # Database configuration
        self.database_url: str | None = config.get("database_url")
        self.table_prefix: str = config.get("table_prefix", "cerebro_")

        # Memory configuration
        self.max_session_duration_hours: int = config.get("max_session_duration_hours", 24)
        self.retention_days: int = config.get("retention_days", 7)
        self.max_size: int = config.get("max_size", 10000)
        self.cleanup_interval: int = config.get("cleanup_interval", 300)

        # Database components
        self.engine: Any = None
        self.session_factory: Any = None

        # Fallback storage
        self._fallback_storage: list[Episode] = []
        self._fallback_accessed_at: dict[str, datetime] = {}

        # Performance tracking
        self.write_count = 0
        self.read_count = 0

    async def initialize(self) -> None:
        """Initialize the episodic memory system."""

        if self.database_url and HAS_SQLALCHEMY:
            try:
                # Create async engine
                self.engine = create_async_engine(
                    self.database_url, echo=self.config.get("sql_debug", False)
                )

                # Create session factory
                from sqlalchemy.ext.asyncio import async_sessionmaker

                self.session_factory = async_sessionmaker(
                    bind=self.engine, class_=AsyncSession, expire_on_commit=False
                )

                # Create tables
                if Base is not None:
                    async with self.engine.begin() as conn:
                        await conn.run_sync(Base.metadata.create_all)

                logger.info("Episodic memory initialized with PostgreSQL")

            except Exception as e:
                logger.error(
                    f"Failed to initialize PostgreSQL for episodic memory: {e}"
                )
                self.engine = None

        if not self.engine:
            logger.warning("Using fallback storage for episodic memory")

    async def store_episode(self, episode: Episode) -> bool:
        """
        Store an episode in episodic memory.

        Args:
            episode: Episode to store

        Returns:
            True if stored successfully
        """

        try:
            if self.engine and self.session_factory:
                await self._store_episode_db(episode)
            else:
                self._store_episode_fallback(episode)

            self.write_count += 1
            logger.debug(f"Stored episode: {episode.event_type.value}")
            return True

        except Exception as e:
            logger.error(f"Failed to store episode {episode.id}: {e}")
            return False

    async def retrieve_episodes(self, query: EpisodeQuery) -> list[Episode]:
        """
        Retrieve episodes based on query parameters.

        Args:
            query: Query parameters

        Returns:
            List of matching episodes
        """

        try:
            if self.engine and self.session_factory:
                episodes = await self._retrieve_episodes_db(query)
            else:
                episodes = self._retrieve_episodes_fallback(query)

            self.read_count += 1
            logger.debug(f"Retrieved {len(episodes)} episodes")
            return episodes

        except Exception as e:
            logger.error(f"Failed to retrieve episodes: {e}")
            return []

    async def get_session_summary(self, session_id: str) -> dict[str, Any] | None:
        """Get summary statistics for a session."""

        try:
            if self.engine and self.session_factory:
                return await self._get_session_summary_db(session_id)
            else:
                return self._get_session_summary_fallback(session_id)

        except Exception as e:
            logger.error(f"Failed to get session summary for {session_id}: {e}")
            return None

    async def find_similar_episodes(
        self,
        reference_episode: Episode,
        similarity_threshold: float = 0.7,
        limit: int = 10,
    ) -> list[tuple[Episode, float]]:
        """
        Find episodes similar to a reference episode.

        Args:
            reference_episode: Episode to find similar ones to
            similarity_threshold: Minimum similarity score
            limit: Maximum number of results

        Returns:
            List of (episode, similarity_score) tuples
        """

        # Query recent episodes of similar type
        query = EpisodeQuery(
            event_types=[reference_episode.event_type],
            user_id=reference_episode.user_id,
            start_time=datetime.now() - timedelta(days=30),
            limit=limit * 2,  # Get more for filtering
        )

        recent_episodes = await self.retrieve_episodes(query)

        # Calculate similarity scores
        similar_episodes = []

        for episode in recent_episodes:
            if episode.id == reference_episode.id:
                continue  # Skip the reference episode itself

            similarity = self._calculate_episode_similarity(reference_episode, episode)

            if similarity >= similarity_threshold:
                similar_episodes.append((episode, similarity))

        # Sort by similarity and limit results
        similar_episodes.sort(key=lambda x: x[1], reverse=True)
        return similar_episodes[:limit]

    async def analyze_patterns(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Analyze patterns in episodic memory.

        Args:
            user_id: Analyze patterns for specific user
            agent_id: Analyze patterns for specific agent
            days: Number of days to analyze

        Returns:
            Pattern analysis results
        """

        start_time = datetime.now() - timedelta(days=days)

        query = EpisodeQuery(
            user_id=user_id,
            agent_id=agent_id,
            start_time=start_time,
            limit=1000,  # Analyze recent episodes
        )

        episodes = await self.retrieve_episodes(query)

        # Analyze patterns
        patterns = {
            "total_episodes": len(episodes),
            "event_type_distribution": {},
            "success_rate": 0.0,
            "avg_quality_score": 0.0,
            "peak_activity_hours": [],
            "common_tags": {},
            "session_patterns": {},
        }

        if not episodes:
            return patterns

        # Event type distribution
        event_counts: dict[str, int] = {}
        successful_events = 0
        quality_scores: list[float] = []
        hourly_activity = [0] * 24
        tag_counts: dict[str, int] = {}

        for episode in episodes:
            # Event types
            event_type = episode.event_type.value
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

            # Success rate
            if episode.success is not None and episode.success:
                successful_events += 1

            # Quality scores
            if episode.quality_score is not None:
                quality_scores.append(episode.quality_score)

            # Activity patterns
            hour = episode.timestamp.hour
            hourly_activity[hour] += 1

            # Tags
            for tag in episode.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        patterns["event_type_distribution"] = event_counts
        patterns["success_rate"] = successful_events / len(episodes) if episodes else 0
        patterns["avg_quality_score"] = (
            sum(quality_scores) / len(quality_scores) if quality_scores else 0
        )

        # Find peak activity hours
        max_activity = max(hourly_activity)
        patterns["peak_activity_hours"] = [
            hour
            for hour, count in enumerate(hourly_activity)
            if count > max_activity * 0.8
        ]

        patterns["common_tags"] = dict(
            sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        )

        return patterns

    async def cleanup_old_episodes(self, retention_days: int | None = None) -> int:
        """
        Clean up old episodes beyond retention period.

        Args:
            retention_days: Days to retain (None = use config default)

        Returns:
            Number of episodes cleaned up
        """

        retention_days = retention_days or self.retention_days
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        try:
            if self.engine and self.session_factory:
                return await self._cleanup_old_episodes_db(cutoff_date)
            else:
                return self._cleanup_old_episodes_fallback(cutoff_date)

        except Exception as e:
            logger.error(f"Failed to cleanup old episodes: {e}")
            return 0

    async def get_memory_stats(self) -> dict[str, Any]:
        """Get episodic memory statistics."""

        stats = {
            "write_count": self.write_count,
            "read_count": self.read_count,
            "total_operations": self.write_count + self.read_count,
        }

        try:
            if self.engine and self.session_factory:
                async with self.session_factory() as session:
                    # Get episode count
                    result = await session.execute(
                        text("SELECT COUNT(*) FROM episodic_events")
                    )
                    stats["total_episodes"] = result.scalar() or 0

                    # Get session count
                    result = await session.execute(
                        text("SELECT COUNT(*) FROM episodic_sessions")
                    )
                    stats["total_sessions"] = result.scalar() or 0

                    # Get recent activity
                    recent_date = datetime.now() - timedelta(days=7)
                    result = await session.execute(
                        text(
                            "SELECT COUNT(*) FROM episodic_events WHERE timestamp > :date"
                        ),
                        {"date": recent_date},
                    )
                    stats["recent_episodes_7d"] = result.scalar() or 0

            else:
                stats.update(
                    {
                        "total_episodes": len(self._fallback_storage),
                        "database_connected": False,
                    }
                )

        except Exception as e:
            logger.error(f"Failed to get episodic memory stats: {e}")

        return stats

    async def _store_episode_db(self, episode: Episode) -> None:
        """Store episode in PostgreSQL database."""

        assert self.session_factory is not None
        async with self.session_factory() as session:
            db_episode = EpisodicEvent(
                id=episode.id,
                session_id=episode.session_id,
                user_id=episode.user_id,
                agent_id=episode.agent_id,
                event_type=episode.event_type.value,
                event_data=episode.event_data,
                context=episode.context,
                timestamp=episode.timestamp,
                duration_ms=episode.duration_ms,
                sequence_number=episode.sequence_number,
                success=episode.success,
                quality_score=episode.quality_score,
                user_feedback=episode.user_feedback,
                tags=episode.tags,
                event_metadata=episode.metadata,
            )

            session.add(db_episode)
            await session.commit()

    async def _retrieve_episodes_db(self, query: EpisodeQuery) -> list[Episode]:
        """Retrieve episodes from PostgreSQL database."""

        assert self.session_factory is not None
        from sqlalchemy import select

        async with self.session_factory() as session:
            # Build query
            db_query = select(EpisodicEvent)

            if query.session_id:
                db_query = db_query.where(EpisodicEvent.session_id == query.session_id)

            if query.user_id:
                db_query = db_query.where(EpisodicEvent.user_id == query.user_id)

            if query.agent_id:
                db_query = db_query.where(EpisodicEvent.agent_id == query.agent_id)

            if query.event_types:
                event_type_values = [et.value for et in query.event_types]
                db_query = db_query.where(
                    EpisodicEvent.event_type.in_(event_type_values)
                )

            if query.start_time:
                db_query = db_query.where(EpisodicEvent.timestamp >= query.start_time)

            if query.end_time:
                db_query = db_query.where(EpisodicEvent.timestamp <= query.end_time)

            if query.min_quality_score:
                db_query = db_query.where(
                    EpisodicEvent.quality_score >= query.min_quality_score
                )

            # Order by
            if query.order_direction == "desc":
                db_query = db_query.order_by(
                    desc(getattr(EpisodicEvent, query.order_by))
                )
            else:
                db_query = db_query.order_by(
                    asc(getattr(EpisodicEvent, query.order_by))
                )

            # Limit and offset
            db_query = db_query.offset(query.offset).limit(query.limit)

            # Execute query
            result = await session.execute(db_query)
            db_episodes = result.scalars().all()

            # Convert to Episode objects
            episodes: list[Episode] = []
            for db_episode in db_episodes:
                episode = Episode(
                    id=str(db_episode.id),
                    session_id=str(db_episode.session_id),
                    user_id=str(db_episode.user_id) if db_episode.user_id else None,
                    agent_id=str(db_episode.agent_id) if db_episode.agent_id else None,
                    event_type=EventType(str(db_episode.event_type)),
                    event_data=dict(db_episode.event_data) if db_episode.event_data else {},
                    context=dict(db_episode.context) if db_episode.context else {},
                    timestamp=db_episode.timestamp,
                    duration_ms=int(db_episode.duration_ms) if db_episode.duration_ms else None,
                    sequence_number=int(db_episode.sequence_number) if db_episode.sequence_number else None,
                    success=bool(db_episode.success) if db_episode.success is not None else None,
                    quality_score=float(db_episode.quality_score) if db_episode.quality_score else None,
                    user_feedback=str(db_episode.user_feedback) if db_episode.user_feedback else None,
                    tags=list(db_episode.tags) if db_episode.tags else [],
                    metadata=dict(db_episode.event_metadata) if db_episode.event_metadata else {},
                )
                episodes.append(episode)

            return episodes

    def _store_episode_fallback(self, episode: Episode) -> None:
        """Store episode in fallback storage."""
        self._fallback_storage.append(episode)
        self._fallback_accessed_at[episode.id] = datetime.now()
        self._evict_fallback_if_needed()

    def _retrieve_episodes_fallback(self, query: EpisodeQuery) -> list[Episode]:
        """Retrieve episodes from fallback storage."""

        episodes = self._fallback_storage

        # Apply filters
        if query.session_id:
            episodes = [e for e in episodes if e.session_id == query.session_id]

        if query.user_id:
            episodes = [e for e in episodes if e.user_id == query.user_id]

        if query.agent_id:
            episodes = [e for e in episodes if e.agent_id == query.agent_id]

        if query.event_types:
            episodes = [e for e in episodes if e.event_type in query.event_types]

        if query.start_time:
            episodes = [e for e in episodes if e.timestamp >= query.start_time]

        if query.end_time:
            episodes = [e for e in episodes if e.timestamp <= query.end_time]

        if query.min_quality_score:
            episodes = [
                e
                for e in episodes
                if e.quality_score and e.quality_score >= query.min_quality_score
            ]

        # Sort
        reverse = query.order_direction == "desc"
        episodes.sort(key=lambda e: getattr(e, query.order_by), reverse=reverse)

        # Apply offset and limit
        selected = episodes[query.offset : query.offset + query.limit]
        now = datetime.now()
        for episode in selected:
            self._fallback_accessed_at[episode.id] = now
        return selected

    def _evict_fallback_if_needed(self) -> None:
        """Evict least recently used fallback episodes over the configured cap."""
        while len(self._fallback_storage) > self.max_size:
            lru_episode = min(
                self._fallback_storage,
                key=lambda episode: self._fallback_accessed_at.get(
                    episode.id,
                    episode.timestamp,
                ),
            )
            self._fallback_storage = [
                episode for episode in self._fallback_storage if episode.id != lru_episode.id
            ]
            self._fallback_accessed_at.pop(lru_episode.id, None)

    def _calculate_episode_similarity(
        self, episode1: Episode, episode2: Episode
    ) -> float:
        """Calculate similarity score between two episodes."""

        similarity = 0.0

        # Event type similarity
        if episode1.event_type == episode2.event_type:
            similarity += 0.3

        # Context similarity (simple approach)
        context1_keys = set(episode1.context.keys())
        context2_keys = set(episode2.context.keys())

        if context1_keys and context2_keys:
            key_overlap = len(context1_keys & context2_keys)
            total_keys = len(context1_keys | context2_keys)
            similarity += 0.3 * (key_overlap / total_keys)

        # Tag similarity
        tags1 = set(episode1.tags)
        tags2 = set(episode2.tags)

        if tags1 and tags2:
            tag_overlap = len(tags1 & tags2)
            total_tags = len(tags1 | tags2)
            similarity += 0.2 * (tag_overlap / total_tags)

        # Temporal proximity (episodes close in time are more similar)
        time_diff = abs((episode1.timestamp - episode2.timestamp).total_seconds())
        max_time_diff = 86400  # 24 hours
        time_similarity = max(0, 1 - (time_diff / max_time_diff))
        similarity += 0.2 * time_similarity

        return min(similarity, 1.0)

    async def _get_session_summary_db(self, session_id: str) -> dict[str, Any]:
        """Get session summary from database."""
        assert self.session_factory is not None
        async with self.session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM episodic_sessions WHERE session_id = :sid"),
                {"sid": session_id},
            )
            row = result.first()
            if row:
                return dict(row._mapping)
            return {}

    def _get_session_summary_fallback(self, session_id: str) -> dict[str, Any]:
        """Get session summary from fallback storage."""
        episodes = [e for e in self._fallback_storage if e.session_id == session_id]
        if not episodes:
            return {}
        return {
            "session_id": session_id,
            "event_count": len(episodes),
            "success_rate": sum(1 for e in episodes if e.success) / len(episodes)
            if episodes
            else 0,
        }

    async def _cleanup_old_episodes_db(self, cutoff_date: datetime) -> int:
        """Cleanup old episodes from database."""
        assert self.session_factory is not None
        async with self.session_factory() as session:
            result = await session.execute(
                text("DELETE FROM episodic_events WHERE timestamp < :cutoff"),
                {"cutoff": cutoff_date},
            )
            await session.commit()
            return result.rowcount if hasattr(result, "rowcount") and result.rowcount else 0

    def _cleanup_old_episodes_fallback(self, cutoff_date: datetime) -> int:
        """Cleanup old episodes from fallback storage."""
        original_count = len(self._fallback_storage)
        self._fallback_storage = [
            e for e in self._fallback_storage if e.timestamp >= cutoff_date
        ]
        retained_ids = {episode.id for episode in self._fallback_storage}
        self._fallback_accessed_at = {
            episode_id: accessed_at
            for episode_id, accessed_at in self._fallback_accessed_at.items()
            if episode_id in retained_ids
        }
        return original_count - len(self._fallback_storage)

    async def close(self) -> None:
        """Close episodic memory manager and cleanup resources."""
        if self.engine:
            await self.engine.dispose()


__all__ = ["Episode", "EpisodeQuery", "EpisodicMemoryManager", "EventType"]
