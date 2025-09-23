"""
Utility functions for Research Platform CLI.
"""

import re
from pathlib import Path
from typing import Any
from uuid import UUID

import click
import yaml
from rich.prompt import Confirm, Prompt


def validate_uuid(ctx, param, value):
    """Validate UUID parameter."""
    if value is None:
        return None

    try:
        return UUID(value)
    except ValueError:
        raise click.BadParameter(f"Invalid UUID: {value}")


def validate_domains(domains_str: str) -> list[str]:
    """Validate and parse domains string."""
    if not domains_str:
        raise click.BadParameter("At least one domain must be specified")

    # Split by comma or semicolon
    domains = re.split(r"[,;]", domains_str)
    domains = [d.strip() for d in domains if d.strip()]

    if not domains:
        raise click.BadParameter("At least one domain must be specified")

    return domains


def prompt_for_project_details() -> dict[str, Any]:
    """Interactive prompt for project details."""
    console = click.get_current_context().obj.get("console")

    title = Prompt.ask("Project title")
    if not title:
        raise click.Abort("Title is required")

    query_text = Prompt.ask("Research query")
    if not query_text:
        raise click.Abort("Query is required")

    domains_str = Prompt.ask(
        "Research domains (comma-separated)",
        default="General",
    )
    domains = validate_domains(domains_str)

    depth_level = Prompt.ask(
        "Research depth",
        choices=["survey", "comprehensive", "exhaustive"],
        default="comprehensive",
    )

    user_id = Prompt.ask("User ID", default="cli-user")

    # Optional scope configuration
    configure_scope = Confirm.ask("Configure research scope?", default=False)

    scope = None
    if configure_scope:
        scope = {}

        max_sources = Prompt.ask(
            "Maximum number of sources",
            default="50",
        )
        scope["max_sources"] = int(max_sources)

        languages_str = Prompt.ask(
            "Languages (comma-separated)",
            default="en",
        )
        scope["languages"] = [l.strip() for l in languages_str.split(",")]

        geographic_scope_str = Prompt.ask(
            "Geographic scope (comma-separated, optional)",
            default="",
        )
        if geographic_scope_str:
            scope["geographic_scope"] = [
                g.strip() for g in geographic_scope_str.split(",")
            ]

    return {
        "title": title,
        "query_text": query_text,
        "domains": domains,
        "depth_level": depth_level,
        "user_id": user_id,
        "scope": scope,
    }


def load_projects_from_file(file_path: Path) -> list[dict[str, Any]]:
    """Load project definitions from YAML or JSON file."""
    if not file_path.exists():
        raise click.BadParameter(f"File not found: {file_path}")

    with open(file_path) as f:
        if file_path.suffix in [".yaml", ".yml"]:
            data = yaml.safe_load(f)
        elif file_path.suffix == ".json":
            import json

            data = json.load(f)
        else:
            raise click.BadParameter(
                f"Unsupported file format: {file_path.suffix}. Use .yaml or .json"
            )

    # Ensure it's a list
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise click.BadParameter(
            "File must contain a list of projects or a single project"
        )

    # Validate each project
    for i, project in enumerate(data):
        if "title" not in project:
            raise click.BadParameter(f"Project {i+1}: Missing 'title'")
        if "query_text" not in project:
            raise click.BadParameter(f"Project {i+1}: Missing 'query_text'")
        if "domains" not in project:
            raise click.BadParameter(f"Project {i+1}: Missing 'domains'")

        # Set defaults
        project.setdefault("depth_level", "comprehensive")
        project.setdefault("user_id", "cli-user")

    return data


def save_results_to_file(
    results: Any,
    output_path: Path,
    format_type: str = "json",
) -> None:
    """Save results to file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        if format_type == "json":
            import json

            json.dump(results, f, indent=2, default=str)
        elif format_type == "yaml":
            yaml.dump(results, f, default_flow_style=False, default=str)
        elif format_type == "csv":
            f.write(results)
        else:
            f.write(str(results))


def parse_key_value_pairs(pairs: list[str]) -> dict[str, Any]:
    """Parse key=value pairs from command line."""
    result = {}

    for pair in pairs:
        if "=" not in pair:
            raise click.BadParameter(f"Invalid key=value pair: {pair}")

        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()

        # Try to parse value as different types
        if value.lower() in ["true", "false"]:
            value = value.lower() == "true"
        elif value.isdigit():
            value = int(value)
        elif value.replace(".", "", 1).isdigit():
            value = float(value)
        elif value.startswith("[") and value.endswith("]"):
            # Parse as list
            value = [v.strip() for v in value[1:-1].split(",")]

        result[key] = value

    return result


def format_duration(seconds: int) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
