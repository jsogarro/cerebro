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

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import uuid

try:
    from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Boolean, JSON
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.sql import text
    from sqlalchemy import and_, or_, desc, asc
except ImportError:
    # Fallback if SQLAlchemy not available
    AsyncSession = None
    declarative_base = None
    logger.warning("SQLAlchemy not available - episodic memory will use fallback")

logger = logging.getLogger(__name__)

# Database models
if declarative_base:
    Base = declarative_base()

    class EpisodicEvent(Base):
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

    class EpisodicSession(Base):
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
    EpisodicEvent = None
    EpisodicSession = None


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
    user_id: Optional[str] = None
    agent_id: Optional[str] = None

    # Event details
    event_type: EventType = EventType.USER_MESSAGE
    event_data: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    # Temporal information
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: Optional[int] = None
    sequence_number: Optional[int] = None

    # Outcome and feedback
    success: Optional[bool] = None
    quality_score: Optional[float] = None
    user_feedback: Optional[str] = None

    # Metadata
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeQuery:
    """Query parameters for episode retrieval."""

    session_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    event_types: Optional[List[EventType]] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    tags: Optional[List[str]] = None
    min_quality_score: Optional[float] = None
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

    def __init__(self, config: Dict[str, Any]):
        """Initialize episodic memory manager."""
        self.config = config

        # Database configuration
        self.database_url = config.get("database_url")
        self.table_prefix = config.get("table_prefix", "cerebro_")

        # Memory configuration
        self.max_session_duration_hours = config.get("max_session_duration_hours", 24)
        self.retention_days = config.get("retention_days", 90)

        # Database components
        self.engine = None
        self.session_factory = None

        # Fallback storage
        self._fallback_storage = []

        # Performance tracking
        self.write_count = 0
        self.read_count = 0

    async def initialize(self) -> None:
        """Initialize the episodic memory system."""

        if self.database_url and AsyncSession:
            try:
                # Create async engine
                self.engine = create_async_engine(
                    self.database_url, echo=self.config.get("sql_debug", False)
                )

                # Create session factory
                self.session_factory = sessionmaker(
                    self.engine, class_=AsyncSession, expire_on_commit=False
                )

                # Create tables
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

    async def retrieve_episodes(self, query: EpisodeQuery) -> List[Episode]:
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

    async def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
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
    ) -> List[Tuple[Episode, float]]:
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
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
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
        event_counts = {}
        successful_events = 0
        quality_scores = []
        hourly_activity = [0] * 24
        tag_counts = {}

        for episode in episodes:
            # Event types
            event_type = episode.event_type.value
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

            # Success rate
            if episode.success is not None:
                if episode.success:
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

    async def cleanup_old_episodes(self, retention_days: Optional[int] = None) -> int:
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

    async def get_memory_stats(self) -> Dict[str, Any]:
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
                    stats["total_episodes"] = result.scalar()

                    # Get session count
                    result = await session.execute(
                        text("SELECT COUNT(*) FROM episodic_sessions")
                    )
                    stats["total_sessions"] = result.scalar()

                    # Get recent activity
                    recent_date = datetime.now() - timedelta(days=7)
                    result = await session.execute(
                        text(
                            "SELECT COUNT(*) FROM episodic_events WHERE timestamp > :date"
                        ),
                        {"date": recent_date},
                    )
                    stats["recent_episodes_7d"] = result.scalar()

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

    async def _retrieve_episodes_db(self, query: EpisodeQuery) -> List[Episode]:
        """Retrieve episodes from PostgreSQL database."""

        async with self.session_factory() as session:
            # Build query
            db_query = session.query(EpisodicEvent)

            if query.session_id:
                db_query = db_query.filter(EpisodicEvent.session_id == query.session_id)

            if query.user_id:
                db_query = db_query.filter(EpisodicEvent.user_id == query.user_id)

            if query.agent_id:
                db_query = db_query.filter(EpisodicEvent.agent_id == query.agent_id)

            if query.event_types:
                event_type_values = [et.value for et in query.event_types]
                db_query = db_query.filter(
                    EpisodicEvent.event_type.in_(event_type_values)
                )

            if query.start_time:
                db_query = db_query.filter(EpisodicEvent.timestamp >= query.start_time)

            if query.end_time:
                db_query = db_query.filter(EpisodicEvent.timestamp <= query.end_time)

            if query.min_quality_score:
                db_query = db_query.filter(
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
            episodes = []
            for db_episode in db_episodes:
                episode = Episode(
                    id=db_episode.id,
                    session_id=db_episode.session_id,
                    user_id=db_episode.user_id,
                    agent_id=db_episode.agent_id,
                    event_type=EventType(db_episode.event_type),
                    event_data=db_episode.event_data or {},
                    context=db_episode.context or {},
                    timestamp=db_episode.timestamp,
                    duration_ms=db_episode.duration_ms,
                    sequence_number=db_episode.sequence_number,
                    success=db_episode.success,
                    quality_score=db_episode.quality_score,
                    user_feedback=db_episode.user_feedback,
                    tags=db_episode.tags or [],
                    metadata=db_episode.event_metadata or {},
                )
                episodes.append(episode)

            return episodes

    def _store_episode_fallback(self, episode: Episode):
        """Store episode in fallback storage."""
        self._fallback_storage.append(episode)

        # Limit fallback storage size
        max_fallback_size = self.config.get("max_fallback_size", 1000)
        if len(self._fallback_storage) > max_fallback_size:
            # Remove oldest episodes
            self._fallback_storage = self._fallback_storage[-max_fallback_size:]

    def _retrieve_episodes_fallback(self, query: EpisodeQuery) -> List[Episode]:
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
        return episodes[query.offset : query.offset + query.limit]

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

    async def close(self) -> None:
        """Close episodic memory manager and cleanup resources."""
        if self.engine:
            await self.engine.dispose()


__all__ = ["EpisodicMemoryManager", "Episode", "EpisodeQuery", "EventType"]
