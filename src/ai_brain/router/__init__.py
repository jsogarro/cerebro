"""
MASR (Multi-Agent System Router) Package

Intelligent routing system that analyzes query complexity and optimizes
model/agent allocation for cost-effective and performant execution.
"""

from .cost_optimizer import CostOptimizer
from .masr import MASRouter
from .query_analyzer import QueryComplexityAnalyzer

__all__ = ["CostOptimizer", "MASRouter", "QueryComplexityAnalyzer"]
