"""
Base class for MCP tools.

Provides common functionality for all MCP tool implementations.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    """Parameter definition for MCP tools."""

    name: str = Field(..., description="Parameter name")
    type: str = Field(..., description="Parameter type (string, integer, etc.)")
    description: str = Field(..., description="Parameter description")
    required: bool = Field(default=True, description="Whether parameter is required")
    default: Any | None = Field(
        default=None, description="Default value if not required"
    )


class ToolMetadata(BaseModel):
    """Metadata for MCP tools."""

    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    version: str = Field(default="1.0.0", description="Tool version")
    parameters: list[ToolParameter] = Field(
        default_factory=list, description="Tool parameters"
    )
    examples: list[dict[str, Any]] = Field(
        default_factory=list, description="Usage examples"
    )
    tags: list[str] = Field(
        default_factory=list, description="Tool tags for categorization"
    )


class BaseMCPTool(ABC):
    """
    Abstract base class for MCP tools.

    All MCP tools must inherit from this class and implement
    the required methods.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize the MCP tool.

        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self._metadata = self._build_metadata()

    @abstractmethod
    def _build_metadata(self) -> ToolMetadata:
        """
        Build tool metadata.

        Returns:
            Tool metadata object
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool parameters

        Returns:
            Tool execution result
        """
        pass

    async def validate_parameters(self, **kwargs) -> bool:
        """
        Validate tool parameters.

        Args:
            **kwargs: Parameters to validate

        Returns:
            True if valid, False otherwise
        """
        required_params = [p.name for p in self._metadata.parameters if p.required]

        for param in required_params:
            if param not in kwargs:
                self.logger.error(f"Missing required parameter: {param}")
                return False

        return True

    def get_metadata(self) -> ToolMetadata:
        """
        Get tool metadata.

        Returns:
            Tool metadata
        """
        return self._metadata

    def get_name(self) -> str:
        """
        Get tool name.

        Returns:
            Tool name
        """
        return self._metadata.name

    def get_description(self) -> str:
        """
        Get tool description.

        Returns:
            Tool description
        """
        return self._metadata.description

    def log_execution(self, params: dict[str, Any], result: dict[str, Any]):
        """
        Log tool execution for monitoring.

        Args:
            params: Execution parameters
            result: Execution result
        """
        self.logger.info(
            f"Tool {self.get_name()} executed",
            extra={
                "tool_name": self.get_name(),
                "parameters": params,
                "result_keys": list(result.keys()) if result else [],
                "success": result.get("success", False) if result else False,
            },
        )

    async def handle_error(
        self, error: Exception, params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle tool execution errors.

        Args:
            error: Exception that occurred
            params: Parameters that caused the error

        Returns:
            Error response dictionary
        """
        self.logger.error(
            f"Tool {self.get_name()} failed: {error!s}",
            extra={
                "tool_name": self.get_name(),
                "parameters": params,
                "error": str(error),
            },
        )

        return {
            "success": False,
            "error": str(error),
            "tool": self.get_name(),
            "parameters": params,
        }

    async def __call__(self, **kwargs) -> dict[str, Any]:
        """
        Make tool callable.

        Args:
            **kwargs: Tool parameters

        Returns:
            Tool execution result
        """
        try:
            # Validate parameters
            if not await self.validate_parameters(**kwargs):
                return await self.handle_error(ValueError("Invalid parameters"), kwargs)

            # Execute tool
            result = await self.execute(**kwargs)

            # Log execution
            self.log_execution(kwargs, result)

            return result

        except Exception as e:
            return await self.handle_error(e, kwargs)
