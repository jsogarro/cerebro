"""
Agent integrations package.

This package provides integration capabilities for agents to use
external tools and services.
"""

from .mcp_integration import MCPIntegration, create_mcp_integrated_agent

__all__ = ["MCPIntegration", "create_mcp_integrated_agent"]
