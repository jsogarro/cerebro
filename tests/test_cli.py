"""
Tests for Research Platform CLI.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import yaml
from click.testing import CliRunner

from src.cli.client import APIError, ResearchAPIClient
from src.cli.config import CLIConfig
from src.cli.main import cli
from src.models.research_project import (
    ResearchProgress,
    ResearchProject,
    ResearchStatus,
)


@pytest.fixture
def runner():
    """Create CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_config():
    """Create mock configuration."""
    return CLIConfig(
        api_url="http://test-api.local",
        output_format="json",
        verbose=False,
        color=False,
    )


@pytest.fixture
def sample_project():
    """Create sample project."""
    return ResearchProject(
        id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        title="Test Research Project",
        query={
            "text": "Test query",
            "domains": ["AI", "Ethics"],
            "depth_level": "comprehensive",
        },
        user_id="test-user",
        status=ResearchStatus.IN_PROGRESS,
    )


@pytest.fixture
def sample_progress():
    """Create sample progress."""
    return ResearchProgress(
        project_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        total_tasks=10,
        completed_tasks=5,
        in_progress_tasks=2,
        pending_tasks=3,
        failed_tasks=0,
        progress_percentage=50.0,
    )


class TestCLICommands:
    """Test CLI commands."""

    def test_cli_version(self, runner):
        """Test version command."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_cli_help(self, runner):
        """Test help command."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Research Platform CLI" in result.output

    @patch("src.cli.main.ResearchAPIClient")
    def test_health_command(self, mock_client_class, runner):
        """Test health check command."""
        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.health_check.return_value = {"status": "healthy"}
        mock_client.readiness_check.return_value = {
            "status": "ready",
            "checks": {"database": "ok", "redis": "ok"},
        }
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["health"])
        assert result.exit_code == 0
        assert "API is healthy" in result.output
        assert "API is ready" in result.output

    def test_config_show(self, runner):
        """Test config show command."""
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "Current Configuration" in result.output

    def test_config_set(self, runner):
        """Test config set command."""
        result = runner.invoke(
            cli, ["config", "set", "api_url", "http://new-api.local"]
        )
        assert result.exit_code == 0
        assert "Set api_url = http://new-api.local" in result.output


class TestProjectCommands:
    """Test project-related commands."""

    @patch("src.cli.commands.projects.ResearchAPIClient")
    def test_create_project(self, mock_client_class, runner, sample_project):
        """Test create project command."""
        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.create_project.return_value = sample_project
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            [
                "projects",
                "create",
                "--title",
                "Test Project",
                "--query",
                "Test query",
                "--domains",
                "AI,Ethics",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        assert "Created project" in result.output

        # Verify API call
        mock_client.create_project.assert_called_once()

    @patch("src.cli.commands.projects.ResearchAPIClient")
    def test_get_project(self, mock_client_class, runner, sample_project):
        """Test get project command."""
        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get_project.return_value = sample_project
        mock_client_class.return_value = mock_client

        project_id = "550e8400-e29b-41d4-a716-446655440000"
        result = runner.invoke(
            cli,
            [
                "projects",
                "get",
                project_id,
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == project_id

    @patch("src.cli.commands.projects.ResearchAPIClient")
    def test_list_projects(self, mock_client_class, runner, sample_project):
        """Test list projects command."""
        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.list_projects.return_value = [sample_project]
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            [
                "projects",
                "list",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["title"] == "Test Research Project"

    @patch("src.cli.commands.projects.ResearchAPIClient")
    def test_get_progress(self, mock_client_class, runner, sample_progress):
        """Test get progress command."""
        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get_project_progress.return_value = sample_progress
        mock_client_class.return_value = mock_client

        project_id = "550e8400-e29b-41d4-a716-446655440000"
        result = runner.invoke(
            cli,
            [
                "projects",
                "progress",
                project_id,
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["progress_percentage"] == 50.0

    @patch("src.cli.commands.projects.ResearchAPIClient")
    def test_cancel_project(self, mock_client_class, runner):
        """Test cancel project command."""
        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.cancel_project.return_value = None
        mock_client_class.return_value = mock_client

        project_id = "550e8400-e29b-41d4-a716-446655440000"
        result = runner.invoke(
            cli,
            [
                "projects",
                "cancel",
                project_id,
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "cancelled" in result.output

    @patch("src.cli.commands.projects.ResearchAPIClient")
    def test_create_project_from_file(
        self, mock_client_class, runner, sample_project, tmp_path
    ):
        """Test creating projects from file."""
        # Create test file
        test_file = tmp_path / "test_projects.yaml"
        test_data = [
            {
                "title": "Test Project",
                "query_text": "Test query",
                "domains": ["AI", "Ethics"],
                "user_id": "test-user",
            }
        ]

        with open(test_file, "w") as f:
            yaml.dump(test_data, f)

        # Setup mock
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.create_project.return_value = sample_project
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            [
                "projects",
                "create",
                "--file",
                str(test_file),
            ],
        )

        assert result.exit_code == 0
        assert "Creating 1 project(s)" in result.output
        assert "Summary: 1 created, 0 failed" in result.output


class TestCLIConfig:
    """Test CLI configuration."""

    def test_config_from_env(self, monkeypatch):
        """Test loading config from environment."""
        monkeypatch.setenv("RESEARCH_API_URL", "http://env-api.local")
        monkeypatch.setenv("RESEARCH_OUTPUT_FORMAT", "json")
        monkeypatch.setenv("RESEARCH_VERBOSE", "true")

        config = CLIConfig.from_env()

        assert config.api_url == "http://env-api.local"
        assert config.output_format == "json"
        assert config.verbose is True

    def test_config_save_to_file(self, tmp_path):
        """Test saving config to file."""
        config = CLIConfig(
            api_url="http://test-api.local",
            output_format="yaml",
        )

        config_file = tmp_path / "test_config.env"
        config.save_to_file(config_file)

        assert config_file.exists()
        content = config_file.read_text()
        assert "RESEARCH_API_URL=http://test-api.local" in content
        assert "RESEARCH_OUTPUT_FORMAT=yaml" in content


class TestAPIClient:
    """Test API client."""

    @pytest.mark.asyncio
    async def test_client_request_success(self):
        """Test successful API request."""
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "ok"}
            mock_request.return_value = mock_response

            client = ResearchAPIClient()
            response = await client._request("GET", "/test")

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_client_request_error(self):
        """Test API request error."""
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.json.return_value = {"detail": "Not found"}
            mock_request.return_value = mock_response

            client = ResearchAPIClient()

            with pytest.raises(APIError) as exc_info:
                await client._request("GET", "/test")

            assert exc_info.value.status_code == 404
            assert "Not found" in str(exc_info.value)
