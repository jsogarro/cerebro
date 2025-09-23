"""
MASR (Multi-Agent System Router) Package

Intelligent routing system that analyzes query complexity and optimizes
model/agent allocation for cost-effective and performant execution.
"""

from .masr import MASRouter
from .query_analyzer import QueryComplexityAnalyzer
from .cost_optimizer import CostOptimizer

__all__ = ["MASRouter", "QueryComplexityAnalyzer", "CostOptimizer"]
