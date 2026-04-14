"""Multi-tier memory system for Cerebro."""

from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class WorkingMemoryContext:
    """Enhanced working memory for active research session."""
    
    session_id: str
    user_id: str
    project_id: Optional[str] = None
    current_query: str = ""
    query_history: List[str] = field(default_factory=list)
    intermediate_results: Dict[str, Any] = field(default_factory=dict)
    extracted_entities: List[Dict] = field(default_factory=list)
    context_stack: List[Dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    def add_query(self, query: str):
        """Add query to history."""
        if self.current_query:
            self.query_history.append(self.current_query)
        self.current_query = query
        self.last_activity = datetime.now()
    
    def get_relevant_context(self, query: str, max_items: int = 5) -> List[Dict]:
        """Retrieve relevant context items."""
        all_context = []
        
        for hist_query in self.query_history[-10:]:
            relevance = self._calculate_relevance(query, hist_query)
            all_context.append({"type": "previous_query", "content": hist_query, "relevance": relevance})
        
        all_context.sort(key=lambda x: x["relevance"], reverse=True)
        return all_context[:max_items]
    
    def _calculate_relevance(self, query: str, text: str) -> float:
        """Calculate semantic relevance."""
        query_words = set(query.lower().split())
        text_words = set(text.lower().split())
        if not query_words:
            return 0.0
        overlap = len(query_words & text_words)
        return overlap / len(query_words)
    
    def to_prompt_context(self) -> str:
        """Convert to string for LLM context."""
        parts = ["## Research Session Context", f"Current Query: {self.current_query}", "", "### Query History:"]
        for i, query in enumerate(self.query_history[-5:], 1):
            parts.append(f"{i}. {query}")
        return "\n".join(parts)


class WorkingMemoryManager:
    """Manages working memory for active sessions."""
    
    def __init__(self):
        self._memories: Dict[str, WorkingMemoryContext] = {}
    
    async def get_or_create(self, session_id: str, user_id: str, project_id: Optional[str] = None) -> WorkingMemoryContext:
        """Get or create working memory."""
        if session_id in self._memories:
            memory = self._memories[session_id]
            memory.last_activity = datetime.now()
            return memory
        
        memory = WorkingMemoryContext(
            session_id=session_id,
            user_id=user_id,
            project_id=project_id
        )
        self._memories[session_id] = memory
        return memory


@dataclass
class EpisodicEvent:
    """An event in episodic memory."""
    id: str
    user_id: str
    event_type: str
    event_data: Dict[str, Any]
    query_text: Optional[str] = None
    quality_score: float = 0.5
    created_at: datetime = field(default_factory=datetime.now)


class EpisodicMemoryService:
    """Manages episodic memory (event history)."""
    
    async def record_event(self, user_id: str, event_type: str, event_data: dict, 
                          query_text: Optional[str] = None) -> EpisodicEvent:
        """Record an event."""
        import uuid
        event = EpisodicEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type=event_type,
            event_data=event_data,
            query_text=query_text
        )
        return event
    
    async def get_recent_context(self, user_id: str, limit: int = 20) -> List[EpisodicEvent]:
        """Get recent events."""
        return []


@dataclass  
class SemanticEntity:
    """An entity in semantic memory."""
    id: str
    entity_type: str
    name: str
    description: str
    confidence: float = 0.5


class EntityExtractionService:
    """Extract and manage semantic entities."""
    
    async def extract_from_text(self, text: str) -> List[SemanticEntity]:
        """Extract entities from text."""
        return []


class ProceduralMemoryService:
    """Learn and apply procedural knowledge."""
    
    async def get_applicable_skills(self, query: str, domains: List[str], user_id: str) -> List[Dict]:
        """Get skills applicable to query."""
        return []


class QuerySuggestionService:
    """Generate intelligent query suggestions."""
    
    async def get_suggestions(self, user_id: str, current_query: str) -> List[Dict]:
        """Get context-aware suggestions."""
        return []