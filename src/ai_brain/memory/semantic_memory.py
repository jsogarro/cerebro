"""
Semantic Memory Manager

Manages semantic memory for storing and retrieving knowledge using vector
similarity search. Integrates with the existing vector database infrastructure
to provide intelligent knowledge retrieval for agents.

Semantic memory stores:
- Knowledge embeddings for fast similarity search
- Factual information and learned knowledge
- Concept relationships and associations
- Domain-specific expertise and patterns
- Retrieved information with relevance scoring
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    # Try to import sentence transformers for embeddings
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None
    logger.warning("sentence-transformers not available - using fallback embeddings")

try:
    # Try to import qdrant client for vector storage
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ImportError:
    QdrantClient = None
    models = None
    logger.warning("qdrant-client not available - using fallback storage")


@dataclass
class SemanticItem:
    """Individual item stored in semantic memory."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Knowledge organization
    domain: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)

    # Temporal information
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    access_count: int = 0

    # Quality and relevance
    confidence_score: float = 1.0
    source: str | None = None
    verified: bool = False


@dataclass
class SemanticQuery:
    """Query parameters for semantic search."""

    query_text: str = ""
    query_embedding: list[float] | None = None
    domain: str | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None
    min_confidence: float = 0.0
    limit: int = 10
    similarity_threshold: float = 0.7


@dataclass
class SemanticResult:
    """Result from semantic search."""

    item: SemanticItem
    similarity_score: float
    relevance_explanation: str = ""


class SemanticMemoryManager:
    """
    Manages semantic memory using vector similarity search.

    Provides intelligent knowledge storage and retrieval with:
    - Vector embeddings for semantic similarity
    - Fast approximate nearest neighbor search
    - Knowledge organization and categorization
    - Relevance scoring and explanation
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize semantic memory manager."""
        self.config = config

        # Vector database configuration
        self.vector_db_url = config.get("vector_db_url", "http://localhost:6333")
        self.collection_name = config.get("collection_name", "cerebro_semantic")
        self.embedding_dimension = config.get("embedding_dimension", 384)

        # Embedding model configuration
        self.embedding_model_name = config.get("embedding_model", "all-MiniLM-L6-v2")

        # Initialize components
        self.embedding_model = None
        self.vector_client = None

        # Fallback storage
        self._fallback_storage: list[SemanticItem] = []

        # Performance tracking
        self.store_count = 0
        self.search_count = 0

    async def initialize(self) -> None:
        """Initialize the semantic memory system."""

        # Initialize embedding model
        if SentenceTransformer:
            try:
                self.embedding_model = SentenceTransformer(self.embedding_model_name)
                logger.info(f"Initialized embedding model: {self.embedding_model_name}")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                self.embedding_model = None

        # Initialize vector database
        if QdrantClient and models:
            try:
                self.vector_client = QdrantClient(url=self.vector_db_url)

                # Create collection if it doesn't exist
                await self._ensure_collection_exists()

                logger.info("Semantic memory initialized with Qdrant")

            except Exception as e:
                logger.error(f"Failed to connect to vector database: {e}")
                self.vector_client = None

        if not self.embedding_model or not self.vector_client:
            logger.warning("Using fallback storage for semantic memory")

    async def store(self, item: SemanticItem) -> bool:
        """
        Store an item in semantic memory.

        Args:
            item: Semantic item to store

        Returns:
            True if stored successfully
        """

        try:
            # Generate embedding if not provided
            if not item.embedding and item.content:
                item.embedding = await self._generate_embedding(item.content)

            if self.vector_client and item.embedding:
                await self._store_vector_db(item)
            else:
                self._store_fallback(item)

            self.store_count += 1
            logger.debug(f"Stored semantic item: {item.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to store semantic item {item.id}: {e}")
            return False

    async def search(self, query: SemanticQuery) -> list[SemanticResult]:
        """
        Search semantic memory for relevant items.

        Args:
            query: Search query parameters

        Returns:
            List of relevant results with similarity scores
        """

        try:
            # Generate query embedding if not provided
            if not query.query_embedding and query.query_text:
                query.query_embedding = await self._generate_embedding(query.query_text)

            if self.vector_client and query.query_embedding:
                results = await self._search_vector_db(query)
            else:
                results = self._search_fallback(query)

            self.search_count += 1
            logger.debug(f"Semantic search returned {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"Failed to search semantic memory: {e}")
            return []

    async def store_knowledge(
        self,
        content: str,
        domain: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
        confidence: float = 1.0,
    ) -> str:
        """
        Store knowledge in semantic memory.

        Args:
            content: Knowledge content to store
            domain: Knowledge domain (research, business, etc.)
            category: Knowledge category
            tags: Optional tags
            source: Source of the knowledge
            confidence: Confidence score (0-1)

        Returns:
            ID of stored knowledge item
        """

        item = SemanticItem(
            content=content,
            domain=domain,
            category=category,
            tags=tags or [],
            source=source,
            confidence_score=confidence,
            verified=confidence > 0.8,  # High confidence items are considered verified
        )

        success = await self.store(item)
        return item.id if success else ""

    async def retrieve_knowledge(
        self,
        query: str,
        domain: str | None = None,
        limit: int = 5,
        min_similarity: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Retrieve relevant knowledge for a query.

        Args:
            query: Query text
            domain: Optional domain filter
            limit: Maximum number of results
            min_similarity: Minimum similarity threshold

        Returns:
            List of relevant knowledge items
        """

        search_query = SemanticQuery(
            query_text=query,
            domain=domain,
            limit=limit,
            similarity_threshold=min_similarity,
        )

        results = await self.search(search_query)

        # Convert to simple knowledge format
        knowledge_items = []
        for result in results:
            knowledge_items.append(
                {
                    "content": result.item.content,
                    "similarity": result.similarity_score,
                    "domain": result.item.domain,
                    "category": result.item.category,
                    "tags": result.item.tags,
                    "source": result.item.source,
                    "confidence": result.item.confidence_score,
                    "created_at": result.item.created_at.isoformat(),
                }
            )

        return knowledge_items

    async def update_item(self, item_id: str, updates: dict[str, Any]) -> bool:
        """Update an existing semantic memory item."""

        try:
            if self.vector_client:
                # For vector DB, we need to retrieve, update, and re-store
                # This is a simplified approach
                return False  # Not implemented in this version
            else:
                # Update in fallback storage
                for item in self._fallback_storage:
                    if item.id == item_id:
                        for key, value in updates.items():
                            if hasattr(item, key):
                                setattr(item, key, value)
                        return True
                return False

        except Exception as e:
            logger.error(f"Failed to update semantic item {item_id}: {e}")
            return False

    async def delete_item(self, item_id: str) -> bool:
        """Delete an item from semantic memory."""

        try:
            if self.vector_client:
                # Delete from vector database
                await self.vector_client.delete(
                    collection_name=self.collection_name,
                    points_selector=models.PointIdsList(points=[item_id]),
                )
                return True
            else:
                # Delete from fallback storage
                self._fallback_storage = [
                    item for item in self._fallback_storage if item.id != item_id
                ]
                return True

        except Exception as e:
            logger.error(f"Failed to delete semantic item {item_id}: {e}")
            return False

    async def get_memory_stats(self) -> dict[str, Any]:
        """Get semantic memory statistics."""

        stats = {
            "store_count": self.store_count,
            "search_count": self.search_count,
            "total_operations": self.store_count + self.search_count,
        }

        try:
            if self.vector_client:
                collection_info = await self.vector_client.get_collection(
                    self.collection_name
                )
                stats.update(
                    {
                        "total_items": collection_info.points_count,
                        "vector_dimension": collection_info.config.params.vectors.size,
                        "database_connected": True,
                    }
                )
            else:
                stats.update(
                    {
                        "total_items": len(self._fallback_storage),
                        "database_connected": False,
                    }
                )

        except Exception as e:
            logger.error(f"Failed to get semantic memory stats: {e}")

        return stats

    async def _generate_embedding(self, text: str) -> list[float] | None:
        """Generate embedding for text."""

        if self.embedding_model:
            try:
                # Generate embedding using sentence transformer
                embedding = self.embedding_model.encode(text)
                return embedding.tolist()
            except Exception as e:
                logger.error(f"Failed to generate embedding: {e}")

        # Fallback: simple hash-based "embedding"
        import hashlib

        text_hash = hashlib.md5(text.encode()).hexdigest()
        # Convert to pseudo-embedding (not semantically meaningful)
        pseudo_embedding = [
            float(int(text_hash[i : i + 2], 16)) / 255.0
            for i in range(0, min(len(text_hash), self.embedding_dimension * 2), 2)
        ]

        # Pad or truncate to correct dimension
        while len(pseudo_embedding) < self.embedding_dimension:
            pseudo_embedding.append(0.0)

        return pseudo_embedding[: self.embedding_dimension]

    async def _ensure_collection_exists(self) -> None:
        """Ensure vector collection exists."""

        assert self.vector_client is not None
        assert models is not None

        try:
            # Check if collection exists
            collections = await self.vector_client.get_collections()
            collection_names = [c.name for c in collections.collections]

            if self.collection_name not in collection_names:
                # Create collection
                await self.vector_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.embedding_dimension, distance=models.Distance.COSINE
                    ),
                )
                logger.info(f"Created vector collection: {self.collection_name}")

        except Exception as e:
            logger.error(f"Failed to ensure collection exists: {e}")
            raise

    async def _store_vector_db(self, item: SemanticItem) -> None:
        """Store item in vector database."""

        assert self.vector_client is not None
        assert models is not None

        point = models.PointStruct(
            id=item.id,
            vector=item.embedding,
            payload={
                "content": item.content,
                "domain": item.domain,
                "category": item.category,
                "tags": item.tags,
                "created_at": item.created_at.isoformat(),
                "confidence_score": item.confidence_score,
                "source": item.source,
                "verified": item.verified,
                "metadata": item.metadata,
            },
        )

        await self.vector_client.upsert(
            collection_name=self.collection_name, points=[point]
        )

    async def _search_vector_db(self, query: SemanticQuery) -> list[SemanticResult]:
        """Search vector database."""

        assert self.vector_client is not None
        assert models is not None

        # Build filter conditions
        filter_conditions = []

        if query.domain:
            filter_conditions.append(
                models.FieldCondition(
                    key="domain", match=models.MatchValue(value=query.domain)
                )
            )

        if query.categories:
            filter_conditions.append(
                models.FieldCondition(
                    key="category", match=models.MatchAny(any=query.categories)
                )
            )

        if query.min_confidence > 0:
            filter_conditions.append(
                models.FieldCondition(
                    key="confidence_score", range=models.Range(gte=query.min_confidence)
                )
            )

        # Combine filter conditions
        query_filter = None
        if filter_conditions:
            if len(filter_conditions) == 1:
                query_filter = filter_conditions[0]
            else:
                query_filter = models.Filter(must=filter_conditions)

        # Perform vector search
        search_result = await self.vector_client.search(
            collection_name=self.collection_name,
            query_vector=query.query_embedding,
            query_filter=query_filter,
            limit=query.limit,
            score_threshold=query.similarity_threshold,
        )

        # Convert to SemanticResult objects
        results = []
        for hit in search_result:
            # Reconstruct SemanticItem from payload
            payload = hit.payload
            item = SemanticItem(
                id=hit.id,
                content=payload["content"],
                embedding=None,  # Don't store embedding in result
                domain=payload.get("domain"),
                category=payload.get("category"),
                tags=payload.get("tags", []),
                created_at=datetime.fromisoformat(payload["created_at"]),
                confidence_score=payload.get("confidence_score", 1.0),
                source=payload.get("source"),
                verified=payload.get("verified", False),
                metadata=payload.get("metadata", {}),
            )

            result = SemanticResult(
                item=item,
                similarity_score=hit.score,
                relevance_explanation=f"Semantic similarity: {hit.score:.3f}",
            )

            results.append(result)

        return results

    def _store_fallback(self, item: SemanticItem) -> None:
        """Store item in fallback storage."""
        self._fallback_storage.append(item)

        # Limit fallback storage size
        max_fallback_size = self.config.get("max_fallback_size", 10000)
        if len(self._fallback_storage) > max_fallback_size:
            # Remove oldest items
            self._fallback_storage.sort(key=lambda x: x.created_at)
            self._fallback_storage = self._fallback_storage[-max_fallback_size:]

    def _search_fallback(self, query: SemanticQuery) -> list[SemanticResult]:
        """Search fallback storage using simple text matching."""

        results = []
        query_lower = query.query_text.lower()

        for item in self._fallback_storage:
            # Apply filters
            if query.domain and item.domain != query.domain:
                continue

            if query.categories and item.category not in query.categories:
                continue

            if (
                query.min_confidence > 0
                and item.confidence_score < query.min_confidence
            ):
                continue

            # Simple text similarity (keyword matching)
            content_lower = item.content.lower()

            # Count matching words
            query_words = set(query_lower.split())
            content_words = set(content_lower.split())

            if query_words and content_words:
                overlap = len(query_words & content_words)
                total_words = len(query_words | content_words)
                similarity = overlap / total_words if total_words > 0 else 0.0
            else:
                similarity = 0.0

            if similarity >= query.similarity_threshold:
                result = SemanticResult(
                    item=item,
                    similarity_score=similarity,
                    relevance_explanation=f"Keyword overlap: {similarity:.3f}",
                )
                results.append(result)

        # Sort by similarity and limit
        results.sort(key=lambda x: x.similarity_score, reverse=True)
        return results[: query.limit]

    async def close(self) -> None:
        """Close semantic memory manager and cleanup resources."""
        if self.vector_client:
            # Qdrant client doesn't need explicit closing
            pass


__all__ = ["SemanticItem", "SemanticMemoryManager", "SemanticQuery", "SemanticResult"]
