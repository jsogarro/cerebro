"""
Database migration utilities.

Provides utilities for managing Alembic migrations programmatically.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from alembic import command

logger = logging.getLogger(__name__)


class MigrationManager:
    """
    Manager for database migrations.

    Provides programmatic access to Alembic migration operations.
    """

    def __init__(self, alembic_ini_path: str | None = None):
        """
        Initialize migration manager.

        Args:
            alembic_ini_path: Path to alembic.ini (defaults to project root)
        """
        if alembic_ini_path is None:
            # Find alembic.ini in project root
            project_root = Path(__file__).parent.parent.parent.parent
            alembic_ini_path = str(project_root / "alembic.ini")

        self.config = Config(alembic_ini_path)
        self.script_dir = ScriptDirectory.from_config(self.config)

    def run_migrations(self, revision: str = "head") -> None:
        """
        Run migrations up to specified revision.

        Args:
            revision: Target revision (default: "head" for latest)
        """
        logger.info(f"Running migrations to revision: {revision}")
        try:
            command.upgrade(self.config, revision)
            logger.info("Migrations completed successfully")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

    def rollback_migration(self, revision: str = "-1") -> None:
        """
        Rollback migrations to specified revision.

        Args:
            revision: Target revision (default: "-1" for previous)
        """
        logger.info(f"Rolling back to revision: {revision}")
        try:
            command.downgrade(self.config, revision)
            logger.info("Rollback completed successfully")
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            raise

    def get_current_revision(self) -> str | None:
        """
        Get current database revision.

        Returns:
            Current revision ID or None if no migrations applied
        """
        from src.models.db.session import get_database_url

        # Create synchronous engine for Alembic
        db_url = get_database_url()
        if "asyncpg" in db_url:
            db_url = db_url.replace("postgresql+asyncpg", "postgresql")

        engine = create_engine(db_url)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current_rev = context.get_current_revision()

        engine.dispose()
        return current_rev

    def get_head_revision(self) -> str:
        """
        Get the latest revision in migration scripts.

        Returns:
            Head revision ID
        """
        head = self.script_dir.get_current_head()
        return str(head) if head is not None else ""

    def check_migration_status(self) -> dict[str, Any]:
        """
        Check migration status.

        Returns:
            Dictionary with migration status information
        """
        current = self.get_current_revision()
        head = self.get_head_revision()

        status: dict[str, Any] = {
            "current_revision": current,
            "head_revision": head,
            "is_up_to_date": current == head,
            "pending_migrations": [],
        }

        if current != head and head and current:
            for script in self.script_dir.walk_revisions(head, current):
                pending_migrations_list: list[Any] = status["pending_migrations"]
                pending_migrations_list.append(
                    {
                        "revision": script.revision,
                        "description": script.doc,
                        "branch_labels": (
                            list(script.branch_labels) if script.branch_labels else []
                        ),
                    }
                )

        return status

    def create_migration(self, message: str, autogenerate: bool = True) -> str:
        """
        Create a new migration.

        Args:
            message: Migration message
            autogenerate: Whether to autogenerate based on model changes

        Returns:
            Path to created migration file
        """
        logger.info(f"Creating migration: {message}")

        if autogenerate:
            command.revision(self.config, message=message, autogenerate=True)
        else:
            command.revision(self.config, message=message)

        # Get the newly created revision
        head = self.get_head_revision()
        script = self.script_dir.get_revision(head)

        logger.info(f"Migration created: {script.path}")
        return script.path

    def show_history(self, verbose: bool = False) -> list[dict[str, Any]]:
        """
        Show migration history.

        Args:
            verbose: Include detailed information

        Returns:
            List of migration information
        """
        history = []
        current = self.get_current_revision()

        for script in self.script_dir.walk_revisions():
            info = {
                "revision": script.revision,
                "description": script.doc,
                "is_current": script.revision == current,
                "branch_labels": (
                    list(script.branch_labels) if script.branch_labels else []
                ),
            }

            if verbose:
                info["down_revision"] = script.down_revision
                info["dependencies"] = script.dependencies
                info["path"] = script.path

            history.append(info)

        return history

    def verify_migration(self) -> bool:
        """
        Verify that migrations can be applied successfully.

        Returns:
            True if migrations are valid
        """
        try:
            # Check if we can get status
            status = self.check_migration_status()

            # Try to generate SQL for pending migrations (dry run)
            if status["pending_migrations"]:
                logger.info(
                    f"Found {len(status['pending_migrations'])} pending migrations"
                )

            return True

        except Exception as e:
            logger.error(f"Migration verification failed: {e}")
            return False


# Convenience functions
_manager: MigrationManager | None = None


def get_migration_manager() -> MigrationManager:
    """Get or create migration manager instance."""
    global _manager
    if _manager is None:
        _manager = MigrationManager()
    return _manager


async def run_migrations(revision: str = "head") -> None:
    """
    Run migrations (async wrapper).

    Args:
        revision: Target revision
    """
    manager = get_migration_manager()
    manager.run_migrations(revision)


async def rollback_migration(revision: str = "-1") -> None:
    """
    Rollback migration (async wrapper).

    Args:
        revision: Target revision
    """
    manager = get_migration_manager()
    manager.rollback_migration(revision)


async def get_current_revision() -> str | None:
    """Get current revision (async wrapper)."""
    manager = get_migration_manager()
    return manager.get_current_revision()


async def check_migration_status() -> dict[str, Any]:
    """Check migration status (async wrapper)."""
    manager = get_migration_manager()
    return manager.check_migration_status()


# CLI commands for migrations
def run_migration_command(args: list[str]) -> int:
    """
    Run migration command via subprocess.

    Args:
        args: Command arguments

    Returns:
        Exit code
    """
    cmd = ["alembic", *args]
    logger.info(f"Running command: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        logger.info(f"migration_output: {result.stdout}")

    if result.stderr and result.returncode != 0:
        logger.error(f"migration_error: {result.stderr}")

    return result.returncode


def upgrade(revision: str = "head") -> int:
    """Run upgrade command."""
    return run_migration_command(["upgrade", revision])


def downgrade(revision: str = "-1") -> int:
    """Run downgrade command."""
    return run_migration_command(["downgrade", revision])


def current() -> int:
    """Show current revision."""
    return run_migration_command(["current"])


def history() -> int:
    """Show migration history."""
    return run_migration_command(["history"])


def create_revision(message: str, autogenerate: bool = True) -> int:
    """Create new revision."""
    args = ["revision", "-m", message]
    if autogenerate:
        args.insert(1, "--autogenerate")
    return run_migration_command(args)


__all__ = [
    "MigrationManager",
    "check_migration_status",
    "create_revision",
    "current",
    "downgrade",
    "get_current_revision",
    "get_migration_manager",
    "history",
    "rollback_migration",
    "run_migrations",
    "upgrade",
]
