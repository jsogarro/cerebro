"""
Configuration management for Research Platform CLI.
"""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class CLIConfig(BaseModel):
    """CLI configuration model."""

    api_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the Research Platform API",
    )
    api_timeout: int = Field(
        default=30,
        description="API request timeout in seconds",
    )
    output_format: str = Field(
        default="table",
        description="Default output format (table, json, yaml)",
    )
    verbose: bool = Field(
        default=False,
        description="Enable verbose output",
    )
    color: bool = Field(
        default=True,
        description="Enable colored output",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of retries for failed requests",
    )

    # Future: Authentication
    api_key: str | None = Field(
        default=None,
        description="API key for authentication",
    )
    auth_token: str | None = Field(
        default=None,
        description="JWT authentication token",
    )

    @field_validator("output_format")
    @classmethod
    def validate_output_format(cls, v: str) -> str:
        """Validate output format."""
        valid_formats = ["table", "json", "yaml", "csv"]
        if v not in valid_formats:
            raise ValueError(f"Invalid output format. Must be one of: {valid_formats}")
        return v

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str) -> str:
        """Validate and normalize API URL."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("API URL must start with http:// or https://")
        # Remove trailing slash
        return v.rstrip("/")

    @classmethod
    def from_env(cls) -> "CLIConfig":
        """Load configuration from environment variables."""
        # Load from .env file if exists
        env_file = Path.home() / ".research-cli.env"
        if env_file.exists():
            load_dotenv(env_file)
        else:
            # Try loading from current directory
            load_dotenv(".env.cli")

        # Build config from environment variables
        config_dict: dict[str, Any] = {}

        if api_url := os.getenv("RESEARCH_API_URL"):
            config_dict["api_url"] = api_url

        if api_timeout := os.getenv("RESEARCH_API_TIMEOUT"):
            config_dict["api_timeout"] = int(api_timeout)

        if output_format := os.getenv("RESEARCH_OUTPUT_FORMAT"):
            config_dict["output_format"] = output_format

        if verbose := os.getenv("RESEARCH_VERBOSE"):
            config_dict["verbose"] = verbose.lower() in ("true", "1", "yes")

        if color := os.getenv("RESEARCH_COLOR"):
            config_dict["color"] = color.lower() in ("true", "1", "yes")

        if max_retries := os.getenv("RESEARCH_MAX_RETRIES"):
            config_dict["max_retries"] = int(max_retries)

        if api_key := os.getenv("RESEARCH_API_KEY"):
            config_dict["api_key"] = api_key

        if auth_token := os.getenv("RESEARCH_AUTH_TOKEN"):
            config_dict["auth_token"] = auth_token

        return cls(**config_dict)

    def save_to_file(self, path: Path | None = None) -> None:
        """Save configuration to file."""
        if path is None:
            path = Path.home() / ".research-cli.env"

        with open(path, "w") as f:
            f.write("# Research Platform CLI Configuration\n")
            f.write(f"RESEARCH_API_URL={self.api_url}\n")
            f.write(f"RESEARCH_API_TIMEOUT={self.api_timeout}\n")
            f.write(f"RESEARCH_OUTPUT_FORMAT={self.output_format}\n")
            f.write(f"RESEARCH_VERBOSE={str(self.verbose).lower()}\n")
            f.write(f"RESEARCH_COLOR={str(self.color).lower()}\n")
            f.write(f"RESEARCH_MAX_RETRIES={self.max_retries}\n")

            if self.api_key:
                f.write(f"RESEARCH_API_KEY={self.api_key}\n")

            if self.auth_token:
                f.write(f"RESEARCH_AUTH_TOKEN={self.auth_token}\n")


# Global configuration instance
config = CLIConfig.from_env()
