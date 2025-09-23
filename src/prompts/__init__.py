"""
Cerebro Advanced Prompt Management System

Provides dynamic, YAML-based prompt templates with versioning, hot-reload,
and A/B testing capabilities. Replaces hard-coded prompt functions with
flexible, configuration-driven prompt management.

Key Features:
- YAML template loading with variable substitution
- Hot-reload capability for real-time prompt updates
- Prompt versioning and A/B testing
- Template inheritance and specialization
- Integration with LangGraph and TalkHier protocols
"""

from .manager import PromptManager
from .versioning import PromptVersionManager
from .schemas import PromptTemplate, PromptMetadata, PromptVariable

__version__ = "2.0.0"
__author__ = "Cerebro Development Team"

__all__ = [
    "PromptManager",
    "PromptVersionManager",
    "PromptTemplate",
    "PromptMetadata",
    "PromptVariable",
]
