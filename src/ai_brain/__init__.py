"""
Cerebro AI Brain - Multi-Modal LLM Intelligence System

This module contains the core intelligence components of Cerebro, transforming it from
a specialized research platform into a comprehensive AI brain capable of handling
diverse domains with intelligent routing, hierarchical coordination, and self-improvement.

Key Components:
- MASR (Multi-Agent System Router): Intelligent query routing and cost optimization
- Memory Management: Multi-tier memory system (working, episodic, semantic, procedural)
- Model Providers: Dynamic foundation model selection and routing
- Learning System: Self-improvement through reflection and fine-tuning
"""

from .router.masr import MASRouter
from .memory.multi_tier_memory import MultiTierMemorySystem
from .providers.model_router import ModelRouter

__version__ = "2.0.0"
__author__ = "Cerebro Development Team"

__all__ = [
    "MASRouter",
    "MultiTierMemorySystem",
    "ModelRouter",
]
