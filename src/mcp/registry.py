"""
Tool registry for MCP server.

Manages tool registration, discovery, and versioning.
"""

import logging

from src.mcp.base import BaseMCPTool, ToolMetadata

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Registry for MCP tools.

    Manages tool registration, discovery, and versioning.
    """

    def __init__(self):
        """Initialize tool registry."""
        self._tools: dict[str, BaseMCPTool] = {}
        self._metadata: dict[str, ToolMetadata] = {}
        self._versions: dict[str, list[str]] = {}

    def register(self, tool: BaseMCPTool):
        """
        Register a tool.

        Args:
            tool: Tool instance to register
        """
        metadata = tool.get_metadata()
        name = metadata.name
        version = metadata.version

        # Check if tool already exists
        if name in self._tools:
            logger.warning(f"Tool {name} already registered, replacing")

        # Register tool
        self._tools[name] = tool
        self._metadata[name] = metadata

        # Track versions
        if name not in self._versions:
            self._versions[name] = []
        if version not in self._versions[name]:
            self._versions[name].append(version)

        logger.info(f"Registered tool: {name} v{version}")

    def unregister(self, name: str) -> bool:
        """
        Unregister a tool.

        Args:
            name: Tool name

        Returns:
            True if unregistered, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            del self._metadata[name]
            logger.info(f"Unregistered tool: {name}")
            return True
        return False

    def get_tool(self, name: str) -> BaseMCPTool | None:
        """
        Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        return self._tools.get(name)

    def get_metadata(self, name: str) -> ToolMetadata | None:
        """
        Get tool metadata.

        Args:
            name: Tool name

        Returns:
            Tool metadata or None
        """
        return self._metadata.get(name)

    def list_tools(self) -> list[str]:
        """
        List all registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())

    def list_tools_with_metadata(self) -> dict[str, ToolMetadata]:
        """
        List all tools with their metadata.

        Returns:
            Dictionary of tool names to metadata
        """
        return self._metadata.copy()

    def search_tools(self, query: str) -> list[str]:
        """
        Search for tools by name or description.

        Args:
            query: Search query

        Returns:
            List of matching tool names
        """
        query_lower = query.lower()
        matches = []

        for name, metadata in self._metadata.items():
            if (
                query_lower in name.lower()
                or query_lower in metadata.description.lower()
                or any(query_lower in tag.lower() for tag in metadata.tags)
            ):
                matches.append(name)

        return matches

    def get_tools_by_tag(self, tag: str) -> list[str]:
        """
        Get tools by tag.

        Args:
            tag: Tag to search for

        Returns:
            List of tool names with the tag
        """
        tag_lower = tag.lower()
        matches = []

        for name, metadata in self._metadata.items():
            if any(tag_lower == t.lower() for t in metadata.tags):
                matches.append(name)

        return matches

    def get_tool_versions(self, name: str) -> list[str]:
        """
        Get available versions for a tool.

        Args:
            name: Tool name

        Returns:
            List of versions
        """
        return self._versions.get(name, [])

    def get_registry_info(self) -> dict[str, any]:
        """
        Get registry information.

        Returns:
            Registry info dictionary
        """
        return {
            "total_tools": len(self._tools),
            "tools": self.list_tools(),
            "tags": self._get_all_tags(),
            "versions": {
                name: len(versions) for name, versions in self._versions.items()
            },
        }

    def _get_all_tags(self) -> list[str]:
        """
        Get all unique tags from registered tools.

        Returns:
            List of unique tags
        """
        tags = set()
        for metadata in self._metadata.values():
            tags.update(metadata.tags)
        return sorted(list(tags))

    def clear(self):
        """Clear all registered tools."""
        self._tools.clear()
        self._metadata.clear()
        self._versions.clear()
        logger.info("Registry cleared")
