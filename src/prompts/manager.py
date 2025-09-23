"""
Advanced Prompt Manager

Dynamic prompt loading, management, and optimization system that replaces
hard-coded prompt functions with flexible YAML-based templates.

Features:
- YAML template loading with variable substitution
- Hot-reload capability for real-time updates
- Template inheritance and composition
- A/B testing and version management
- Performance tracking and optimization
- Integration with existing agent systems
"""

import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import yaml
import re
from dataclasses import asdict

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    WATCHDOG_AVAILABLE = False

from .schemas import (
    PromptTemplate,
    PromptCollection,
    PromptMetadata,
    PromptType,
    PromptRole,
    PromptVariable,
)

logger = logging.getLogger(__name__)


class PromptChangeEvent:
    """Event for prompt template changes."""

    def __init__(
        self,
        change_type: str,
        template_path: str,
        template_name: str,
        old_template: Optional[PromptTemplate] = None,
        new_template: Optional[PromptTemplate] = None,
    ):
        self.change_type = change_type  # created, modified, deleted
        self.template_path = template_path
        self.template_name = template_name
        self.old_template = old_template
        self.new_template = new_template
        self.timestamp = datetime.now()


class PromptFileWatcher:
    """File watcher for prompt template changes."""

    def __init__(self, prompt_manager: "PromptManager"):
        self.prompt_manager = prompt_manager
        self.debounce_delay = 1.0
        self.pending_changes: set[str] = set()
        self._debounce_task = None

        if WATCHDOG_AVAILABLE:
            self.__class__.__bases__ = (FileSystemEventHandler,)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith((".yaml", ".yml")):
            self.pending_changes.add(event.src_path)
            self._schedule_reload()

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith((".yaml", ".yml")):
            self.pending_changes.add(event.src_path)
            self._schedule_reload()

    def _schedule_reload(self):
        """Schedule debounced reload of templates."""
        if self._debounce_task:
            self._debounce_task.cancel()

        self._debounce_task = asyncio.create_task(self._debounced_reload())

    async def _debounced_reload(self):
        """Reload templates after debounce delay."""
        await asyncio.sleep(self.debounce_delay)

        if self.pending_changes:
            logger.info(f"Prompt templates changed: {self.pending_changes}")
            await self.prompt_manager._reload_templates()
            self.pending_changes.clear()


class PromptCache:
    """Cache for compiled prompt templates."""

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, datetime] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[str]:
        """Get cached compiled prompt."""
        with self._lock:
            if key in self._cache:
                timestamp = self._timestamps.get(key)
                if (
                    timestamp
                    and (datetime.now() - timestamp).seconds < self.ttl_seconds
                ):
                    return self._cache[key]
                else:
                    # Expired
                    del self._cache[key]
                    del self._timestamps[key]
        return None

    def set(self, key: str, value: str):
        """Cache compiled prompt."""
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = datetime.now()

    def invalidate(self, pattern: Optional[str] = None):
        """Invalidate cache entries."""
        with self._lock:
            if pattern:
                keys_to_remove = [k for k in self._cache.keys() if pattern in k]
                for key in keys_to_remove:
                    self._cache.pop(key, None)
                    self._timestamps.pop(key, None)
            else:
                self._cache.clear()
                self._timestamps.clear()


class PromptManager:
    """
    Advanced prompt manager with YAML templates and hot-reload.

    Provides dynamic prompt loading, template inheritance, variable substitution,
    and performance tracking for all Cerebro agents.
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize prompt manager."""
        self.config = config

        # Template configuration
        self.templates_dir = Path(config.get("templates_dir", "src/prompts/templates"))
        self.enable_hot_reload = config.get("enable_hot_reload", True)
        self.enable_caching = config.get("enable_caching", True)
        self.cache_ttl = config.get("cache_ttl_seconds", 3600)

        # Template storage
        self._lock = threading.RLock()
        self._templates: Dict[str, PromptTemplate] = {}
        self._collections: Dict[str, PromptCollection] = {}
        self._template_inheritance: Dict[str, str] = {}

        # Performance tracking
        self.usage_stats: Dict[str, Dict[str, Any]] = {}
        self.load_count = 0
        self.cache_hits = 0
        self.cache_misses = 0

        # Hot-reload components
        self._observer = None
        self._watcher = None

        # Caching
        if self.enable_caching:
            self._cache = PromptCache(self.cache_ttl)
        else:
            self._cache = None

        # Change notifications
        self._change_listeners: List[Callable[[PromptChangeEvent], None]] = []

    async def initialize(self):
        """Initialize the prompt manager."""
        logger.info("Initializing advanced prompt manager...")

        # Load all template files
        await self._load_all_templates()

        # Start file watcher if hot-reload enabled
        if self.enable_hot_reload and WATCHDOG_AVAILABLE:
            await self._start_file_watcher()

        logger.info(f"Prompt manager initialized with {len(self._templates)} templates")

    async def get_prompt(
        self,
        template_name: str,
        variables: Optional[Dict[str, Any]] = None,
        role: Optional[PromptRole] = None,
        domain: Optional[str] = None,
    ) -> str:
        """
        Get a compiled prompt from template.

        Args:
            template_name: Name of the template to use
            variables: Variables for template substitution
            role: Optional role for template selection
            domain: Optional domain for template selection

        Returns:
            Compiled prompt string
        """

        variables = variables or {}

        # Create cache key
        cache_key = self._create_cache_key(template_name, variables, role, domain)

        # Check cache first
        if self._cache:
            cached_prompt = self._cache.get(cache_key)
            if cached_prompt:
                self.cache_hits += 1
                await self._update_usage_stats(template_name, True)
                return cached_prompt
            self.cache_misses += 1

        try:
            # Find best matching template
            template = await self._find_template(template_name, role, domain)

            if not template:
                raise ValueError(f"Template not found: {template_name}")

            # Compile the prompt
            compiled_prompt = await self._compile_template(template, variables)

            # Cache the result
            if self._cache:
                self._cache.set(cache_key, compiled_prompt)

            # Update usage statistics
            await self._update_usage_stats(template_name, True)

            return compiled_prompt

        except Exception as e:
            logger.error(f"Failed to get prompt {template_name}: {e}")
            await self._update_usage_stats(template_name, False)
            raise

    async def get_prompt_for_agent(
        self,
        agent_type: str,
        task_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Get prompt specifically for an agent type.

        Args:
            agent_type: Type of agent requesting prompt
            task_data: Task-specific data for variable substitution
            context: Additional context for prompt selection

        Returns:
            Compiled prompt optimized for the agent
        """

        # Determine template name based on agent type
        template_name = f"agents/{agent_type}"

        # Prepare variables
        variables = {
            "task": task_data,
            "context": context or {},
            "agent_type": agent_type,
            "timestamp": datetime.now().isoformat(),
        }

        return await self.get_prompt(template_name, variables)

    async def get_supervisor_prompt(
        self,
        supervisor_type: str,
        coordination_data: Dict[str, Any],
        worker_results: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Get prompt for supervisor agents."""

        template_name = f"supervisors/{supervisor_type}"

        variables = {
            "coordination": coordination_data,
            "worker_results": worker_results or [],
            "supervisor_type": supervisor_type,
            "timestamp": datetime.now().isoformat(),
        }

        return await self.get_prompt(template_name, variables)

    async def get_refinement_prompt(
        self,
        refinement_round: int,
        previous_outputs: List[Dict[str, Any]],
        consensus_score: float,
        target_threshold: float = 0.95,
    ) -> str:
        """Get prompt for TalkHier refinement rounds."""

        template_name = f"refinement/round_{refinement_round}"

        variables = {
            "round": refinement_round,
            "previous_outputs": previous_outputs,
            "consensus_score": consensus_score,
            "target_threshold": target_threshold,
            "needs_improvement": consensus_score < target_threshold,
        }

        return await self.get_prompt(template_name, variables)

    async def register_template(self, template: PromptTemplate) -> bool:
        """Register a new template programmatically."""

        try:
            template_name = template.metadata.name

            with self._lock:
                # Store template
                self._templates[template_name] = template

                # Handle inheritance
                if template.inherits_from:
                    self._template_inheritance[template_name] = template.inherits_from

                # Invalidate cache
                if self._cache:
                    self._cache.invalidate(template_name)

                # Notify listeners
                await self._notify_change_listeners(
                    "created", "", template_name, None, template
                )

            logger.info(f"Registered template: {template_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to register template: {e}")
            return False

    async def update_template_performance(
        self,
        template_name: str,
        success: bool,
        quality_score: float,
        execution_time_ms: int,
    ) -> bool:
        """Update template performance metrics."""

        try:
            template = self._templates.get(template_name)
            if not template:
                return False

            # Update metadata
            template.metadata.usage_count += 1

            # Update success rate (exponential moving average)
            alpha = 0.1
            template.metadata.success_rate = (
                1 - alpha
            ) * template.metadata.success_rate + alpha * (1.0 if success else 0.0)

            # Update quality score
            template.metadata.avg_quality_score = (
                1 - alpha
            ) * template.metadata.avg_quality_score + alpha * quality_score

            template.metadata.last_updated = datetime.now().isoformat()

            return True

        except Exception as e:
            logger.error(f"Failed to update template performance {template_name}: {e}")
            return False

    async def get_template_stats(self) -> Dict[str, Any]:
        """Get comprehensive prompt manager statistics."""

        return {
            "manager": {
                "total_templates": len(self._templates),
                "total_collections": len(self._collections),
                "load_count": self.load_count,
                "cache_enabled": self.enable_caching,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "hit_rate": self.cache_hits
                / max(self.cache_hits + self.cache_misses, 1),
            },
            "templates": {
                name: {
                    "usage_count": template.metadata.usage_count,
                    "success_rate": template.metadata.success_rate,
                    "avg_quality": template.metadata.avg_quality_score,
                    "type": template.metadata.type.value,
                    "role": (
                        template.metadata.role.value if template.metadata.role else None
                    ),
                }
                for name, template in self._templates.items()
            },
        }

    async def _load_all_templates(self):
        """Load all template files from directory."""

        if not self.templates_dir.exists():
            logger.warning(f"Templates directory not found: {self.templates_dir}")
            return

        template_files = list(self.templates_dir.rglob("*.yaml")) + list(
            self.templates_dir.rglob("*.yml")
        )

        for template_file in template_files:
            try:
                await self._load_template_file(template_file)
            except Exception as e:
                logger.error(f"Failed to load template {template_file}: {e}")

        self.load_count += 1
        logger.info(f"Loaded {len(self._templates)} prompt templates")

    async def _load_template_file(self, template_path: Path):
        """Load a single template file."""

        # Read YAML file
        content = await asyncio.get_event_loop().run_in_executor(
            None, template_path.read_text, "utf-8"
        )

        # Environment variable substitution
        content_with_env = os.path.expandvars(content)

        # Parse YAML
        template_data = yaml.safe_load(content_with_env)

        if not template_data:
            return

        # Handle single template vs collection
        if "templates" in template_data:
            # This is a collection
            collection = PromptCollection(**template_data)
            self._collections[collection.name] = collection

            # Add individual templates
            for template_name, template in collection.templates.items():
                self._templates[template_name] = template
        else:
            # Single template
            template = PromptTemplate(**template_data)
            template_name = template.metadata.name

            # Generate name from path if not specified
            if not template_name:
                template_name = template_path.stem
                template.metadata.name = template_name

            self._templates[template_name] = template

            # Handle inheritance
            if template.inherits_from:
                self._template_inheritance[template_name] = template.inherits_from

    async def _find_template(
        self,
        template_name: str,
        role: Optional[PromptRole] = None,
        domain: Optional[str] = None,
    ) -> Optional[PromptTemplate]:
        """Find best matching template."""

        # Try exact name match first
        if template_name in self._templates:
            return self._templates[template_name]

        # Try role-based matching
        if role:
            for name, template in self._templates.items():
                if template.metadata.role == role:
                    return template

        # Try domain-based matching
        if domain:
            for name, template in self._templates.items():
                if template.metadata.domain == domain:
                    return template

        return None

    async def _compile_template(
        self, template: PromptTemplate, variables: Dict[str, Any]
    ) -> str:
        """Compile template with variable substitution."""

        # Resolve inheritance first
        resolved_template = await self._resolve_inheritance(template)

        # Validate variables
        await self._validate_variables(resolved_template, variables)

        # Compile each prompt part
        parts = []

        if resolved_template.system_prompt:
            system = self._substitute_variables(
                resolved_template.system_prompt, variables
            )
            parts.append(f"SYSTEM: {system}")

        if resolved_template.user_prompt:
            user = self._substitute_variables(resolved_template.user_prompt, variables)
            parts.append(f"USER: {user}")

        if resolved_template.assistant_prompt:
            assistant = self._substitute_variables(
                resolved_template.assistant_prompt, variables
            )
            parts.append(f"ASSISTANT: {assistant}")

        # Add examples if present
        if resolved_template.examples:
            examples_text = await self._compile_examples(
                resolved_template.examples, variables
            )
            parts.append(f"EXAMPLES:\n{examples_text}")

        # Add output format specification
        if resolved_template.expected_output_schema:
            schema_text = self._format_output_schema(
                resolved_template.expected_output_schema
            )
            parts.append(f"OUTPUT FORMAT:\n{schema_text}")

        return "\n\n".join(parts)

    async def _resolve_inheritance(self, template: PromptTemplate) -> PromptTemplate:
        """Resolve template inheritance chain."""

        if not template.inherits_from:
            return template

        # Get base template
        base_template = self._templates.get(template.inherits_from)
        if not base_template:
            logger.warning(f"Base template not found: {template.inherits_from}")
            return template

        # Recursively resolve base template
        resolved_base = await self._resolve_inheritance(base_template)

        # Merge templates (child overrides base)
        merged_template = PromptTemplate(
            metadata=template.metadata,  # Child metadata wins
            system_prompt=template.system_prompt or resolved_base.system_prompt,
            user_prompt=template.user_prompt or resolved_base.user_prompt,
            assistant_prompt=template.assistant_prompt
            or resolved_base.assistant_prompt,
            variables=resolved_base.variables + template.variables,
            examples=resolved_base.examples + template.examples,
            expected_output_schema=template.expected_output_schema
            or resolved_base.expected_output_schema,
            output_format=template.output_format or resolved_base.output_format,
            max_tokens=template.max_tokens or resolved_base.max_tokens,
            temperature=template.temperature or resolved_base.temperature,
        )

        return merged_template

    def _substitute_variables(
        self, template_text: str, variables: Dict[str, Any]
    ) -> str:
        """Substitute variables in template text."""

        result = template_text

        # Handle simple {variable} substitution
        for var_name, var_value in variables.items():
            if isinstance(var_value, (dict, list)):
                # Convert complex types to formatted strings
                var_str = self._format_complex_variable(var_value)
            else:
                var_str = str(var_value)

            result = result.replace(f"{{{var_name}}}", var_str)

        # Handle conditional blocks {{#if variable}}...{{/if}}
        result = self._process_conditional_blocks(result, variables)

        # Handle loops {{#each items}}...{{/each}}
        result = self._process_loop_blocks(result, variables)

        return result

    def _format_complex_variable(self, value: Any) -> str:
        """Format complex variables (dict, list) for templates."""

        if isinstance(value, dict):
            lines = []
            for key, val in value.items():
                lines.append(f"- {key}: {val}")
            return "\n".join(lines)

        elif isinstance(value, list):
            lines = []
            for i, item in enumerate(value, 1):
                if isinstance(item, dict):
                    lines.append(f"{i}. {item}")
                else:
                    lines.append(f"{i}. {item}")
            return "\n".join(lines)

        else:
            return str(value)

    def _process_conditional_blocks(self, text: str, variables: Dict[str, Any]) -> str:
        """Process {{#if variable}}...{{/if}} conditional blocks."""

        # Simple conditional processing
        pattern = r"\{\{#if\s+(\w+)\}\}(.*?)\{\{/if\}\}"

        def replace_conditional(match):
            var_name = match.group(1)
            block_content = match.group(2)

            if var_name in variables and variables[var_name]:
                return block_content
            else:
                return ""

        return re.sub(pattern, replace_conditional, text, flags=re.DOTALL)

    def _process_loop_blocks(self, text: str, variables: Dict[str, Any]) -> str:
        """Process {{#each items}}...{{/each}} loop blocks."""

        # Simple loop processing
        pattern = r"\{\{#each\s+(\w+)\}\}(.*?)\{\{/each\}\}"

        def replace_loop(match):
            var_name = match.group(1)
            block_template = match.group(2)

            if var_name in variables and isinstance(variables[var_name], list):
                results = []
                for item in variables[var_name]:
                    # Simple item substitution
                    item_text = block_template.replace("{{item}}", str(item))
                    results.append(item_text)
                return "\n".join(results)
            else:
                return ""

        return re.sub(pattern, replace_loop, text, flags=re.DOTALL)

    async def _validate_variables(
        self, template: PromptTemplate, variables: Dict[str, Any]
    ):
        """Validate that required variables are provided."""

        for var_def in template.variables:
            if var_def.required and var_def.name not in variables:
                if var_def.default is not None:
                    variables[var_def.name] = var_def.default
                else:
                    raise ValueError(f"Required variable missing: {var_def.name}")

    async def _compile_examples(self, examples: List, variables: Dict[str, Any]) -> str:
        """Compile few-shot examples."""

        example_parts = []

        for i, example in enumerate(examples, 1):
            if hasattr(example, "input_variables"):
                input_vars = example.input_variables
                expected_output = example.expected_output
            else:
                # Handle dict format
                input_vars = example.get("input_variables", {})
                expected_output = example.get("expected_output", "")

            example_parts.append(f"Example {i}:")

            # Format input variables
            for var_name, var_value in input_vars.items():
                example_parts.append(f"{var_name}: {var_value}")

            example_parts.append(f"Expected Output: {expected_output}")
            example_parts.append("")  # Empty line between examples

        return "\n".join(example_parts)

    def _format_output_schema(self, schema: Dict[str, Any]) -> str:
        """Format output schema for prompt."""

        import json

        return f"Please respond with valid JSON matching this schema:\n{json.dumps(schema, indent=2)}"

    def _create_cache_key(
        self,
        template_name: str,
        variables: Dict[str, Any],
        role: Optional[PromptRole],
        domain: Optional[str],
    ) -> str:
        """Create cache key for prompt compilation."""

        import hashlib

        key_data = {
            "template": template_name,
            "variables": sorted(variables.items()) if variables else [],
            "role": role.value if role else None,
            "domain": domain,
        }

        key_string = str(key_data)
        return hashlib.md5(key_string.encode()).hexdigest()[:16]

    async def _update_usage_stats(self, template_name: str, success: bool):
        """Update usage statistics for template."""

        if template_name not in self.usage_stats:
            self.usage_stats[template_name] = {
                "total_uses": 0,
                "successful_uses": 0,
                "last_used": None,
            }

        stats = self.usage_stats[template_name]
        stats["total_uses"] += 1
        if success:
            stats["successful_uses"] += 1
        stats["last_used"] = datetime.now().isoformat()

    async def _start_file_watcher(self):
        """Start file watcher for hot-reload."""

        try:
            self._observer = Observer()
            self._watcher = PromptFileWatcher(self)

            self._observer.schedule(
                self._watcher, str(self.templates_dir), recursive=True
            )

            self._observer.start()
            logger.info(f"Prompt file watcher started for {self.templates_dir}")

        except Exception as e:
            logger.error(f"Failed to start prompt file watcher: {e}")

    async def _reload_templates(self):
        """Reload templates from files."""

        try:
            # Clear current templates
            with self._lock:
                old_templates = self._templates.copy()
                self._templates.clear()
                self._collections.clear()

                # Invalidate cache
                if self._cache:
                    self._cache.invalidate()

            # Reload all templates
            await self._load_all_templates()

            logger.info("Prompt templates reloaded successfully")

        except Exception as e:
            logger.error(f"Failed to reload templates: {e}")

    async def _notify_change_listeners(
        self,
        change_type: str,
        template_path: str,
        template_name: str,
        old_template: Optional[PromptTemplate],
        new_template: Optional[PromptTemplate],
    ):
        """Notify change listeners."""

        event = PromptChangeEvent(
            change_type, template_path, template_name, old_template, new_template
        )

        for listener in self._change_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                logger.error(f"Prompt change listener failed: {e}")

    def add_change_listener(self, listener: Callable[[PromptChangeEvent], None]):
        """Add listener for prompt changes."""
        self._change_listeners.append(listener)

    async def close(self):
        """Close prompt manager and cleanup resources."""

        if self._observer:
            self._observer.stop()
            self._observer.join()

        logger.info("Prompt manager closed")


# Global prompt manager instance
_prompt_manager: Optional[PromptManager] = None


async def get_prompt_manager(config: Optional[Dict[str, Any]] = None) -> PromptManager:
    """Get or create global prompt manager."""

    global _prompt_manager

    if not _prompt_manager:
        if not config:
            raise ValueError("Configuration required for first initialization")

        _prompt_manager = PromptManager(config)
        await _prompt_manager.initialize()

    return _prompt_manager


async def get_prompt(
    template_name: str,
    variables: Optional[Dict[str, Any]] = None,
    role: Optional[PromptRole] = None,
    domain: Optional[str] = None,
) -> str:
    """Get compiled prompt using global manager."""

    manager = await get_prompt_manager()
    return await manager.get_prompt(template_name, variables, role, domain)


__all__ = [
    "PromptManager",
    "PromptChangeEvent",
    "PromptFileWatcher",
    "PromptCache",
    "get_prompt_manager",
    "get_prompt",
]
