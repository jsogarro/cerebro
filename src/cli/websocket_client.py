"""
WebSocket client for CLI real-time streaming.

This module provides WebSocket connectivity for the CLI to receive
real-time updates instead of polling the API.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import websockets
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, TaskID
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.legacy.client import WebSocketClientProtocol

from src.cli.config import config
from src.cli.formatters import (
    OutputFormatter,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from src.models.websocket_messages import WSMessage, WSMessageType
from src.utils.serialization import deserialize, serialize_to_str

logger = logging.getLogger(__name__)


class CLIWebSocketClient:
    """WebSocket client optimized for CLI usage."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        verbose: bool = False,
    ):
        self.base_url = (
            (base_url or config.api_url)
            .replace("http://", "ws://")
            .replace("https://", "wss://")
        )
        self.token = token or config.auth_token
        self.verbose = verbose or config.verbose
        self.websocket: WebSocketClientProtocol | None = None
        self.console = Console()
        self._shutdown = False
        self._connected = False

    async def __aenter__(self) -> "CLIWebSocketClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            self.console.print(f"[dim]WS: {message}[/dim]")

    async def connect(self, endpoint: str) -> bool:
        """
        Connect to WebSocket endpoint.

        Args:
            endpoint: WebSocket endpoint path (e.g., "/ws/projects/123")

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Build WebSocket URL
            ws_url = f"{self.base_url.rstrip('/')}{endpoint}"

            # Add authentication if available
            if self.token:
                ws_url += f"?token={self.token}"

            self._log(f"Connecting to {ws_url}")

            # Add CLI user agent
            headers = {"User-Agent": "research-cli/0.1.0"}

            # Connect to WebSocket
            ws_connection: Any = await websockets.connect(
                ws_url,
                extra_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            )
            self.websocket = ws_connection

            self._connected = True
            self._log("WebSocket connection established")

            return True

        except InvalidStatus as e:
            status_code = getattr(e, 'status_code', getattr(e.response, 'status_code', 0))
            if status_code == 401:
                print_error("Authentication failed. Please check your token.")
            elif status_code == 403:
                print_error(
                    "Access denied. You don't have permission to access this project."
                )
            else:
                print_error(f"Connection failed with status {status_code}")
            return False

        except Exception as e:
            if self.verbose:
                print_error(f"WebSocket connection failed: {e}")
            else:
                print_error("Connection failed. Use --verbose for details.")
            return False

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._shutdown = True
        self._connected = False

        if self.websocket:
            try:
                await self.websocket.close()
                self._log("WebSocket connection closed")
            except Exception as e:
                self._log(f"Error closing WebSocket: {e}")
            finally:
                self.websocket = None

    async def send_heartbeat_response(self) -> None:
        """Send heartbeat response to server."""
        if self.websocket and self._connected:
            try:
                heartbeat_msg = {
                    "type": "heartbeat_response",
                    "timestamp": datetime.utcnow().isoformat(),
                }
                await self.websocket.send(serialize_to_str(heartbeat_msg))
            except Exception as e:
                self._log(f"Failed to send heartbeat: {e}")

    async def stream_progress(
        self,
        project_id: UUID,
        formatter: OutputFormatter,
        message_handler: Callable[[WSMessage], None] | None = None,
    ) -> bool:
        """
        Stream real-time progress updates for a project.

        Args:
            project_id: Project ID to monitor
            formatter: Output formatter for displaying updates
            message_handler: Optional custom message handler
        """
        # Connect to project-specific endpoint
        endpoint = f"/ws/cli/{project_id}"

        if not await self.connect(endpoint):
            return False

        print_info(f"🔗 Streaming updates for project {project_id} (Ctrl+C to stop)")

        # Initialize progress tracking
        _progress = Progress()
        _progress_task: TaskID | None = None

        try:
            with Live(console=self.console, refresh_per_second=2) as live:
                current_status = "Connecting..."
                progress_percentage = 0.0

                if self.websocket is None:
                    return False
                async for message in self.websocket:
                    if self._shutdown:
                        break

                    try:
                        # Parse message
                        message_data = deserialize(message)
                        ws_message = WSMessage(**message_data)

                        # Handle heartbeat
                        if ws_message.type == WSMessageType.HEARTBEAT:
                            await self.send_heartbeat_response()
                            continue

                        # Handle custom message handler
                        if message_handler:
                            message_handler(ws_message)
                            continue

                        # Handle different message types
                        if ws_message.type == WSMessageType.PROGRESS:
                            progress_data = ws_message.data
                            progress_percentage = progress_data.get(
                                "progress_percentage", 0.0
                            )
                            current_agent = progress_data.get(
                                "current_agent", "Unknown"
                            )
                            current_status = f"Progress: {progress_percentage:.1f}% - {current_agent}"

                            # Update live display
                            live.update(
                                Panel(
                                    f"[green]{current_status}[/green]\n"
                                    f"Completed: {progress_data.get('completed_tasks', 0)}/{progress_data.get('total_tasks', 0)} tasks",
                                    title=f"Project {project_id}",
                                    border_style="green",
                                )
                            )

                        elif ws_message.type == WSMessageType.AGENT_STARTED:
                            agent_data = ws_message.data
                            agent_type = agent_data.get("agent_type", "Unknown")
                            task_desc = agent_data.get(
                                "task_description", "Processing..."
                            )
                            current_status = f"🚀 Started: {agent_type} - {task_desc}"

                            live.update(
                                Panel(
                                    f"[yellow]{current_status}[/yellow]\n"
                                    f"Progress: {progress_percentage:.1f}%",
                                    title=f"Project {project_id}",
                                    border_style="yellow",
                                )
                            )

                        elif ws_message.type == WSMessageType.AGENT_COMPLETED:
                            agent_data = ws_message.data
                            agent_type = agent_data.get("agent_type", "Unknown")
                            result_summary = agent_data.get("result_summary", "Done")
                            current_status = (
                                f"✅ Completed: {agent_type} - {result_summary}"
                            )

                            live.update(
                                Panel(
                                    f"[green]{current_status}[/green]\n"
                                    f"Progress: {progress_percentage:.1f}%",
                                    title=f"Project {project_id}",
                                    border_style="green",
                                )
                            )

                        elif ws_message.type == WSMessageType.AGENT_FAILED:
                            agent_data = ws_message.data
                            agent_type = agent_data.get("agent_type", "Unknown")
                            error_msg = agent_data.get("error_message", "Unknown error")
                            current_status = f"❌ Failed: {agent_type} - {error_msg}"

                            live.update(
                                Panel(
                                    f"[red]{current_status}[/red]\n"
                                    f"Progress: {progress_percentage:.1f}%",
                                    title=f"Project {project_id}",
                                    border_style="red",
                                )
                            )

                        elif ws_message.type == WSMessageType.PROJECT_COMPLETED:
                            print_success("🎉 Project completed!")
                            break

                        elif ws_message.type == WSMessageType.PROJECT_FAILED:
                            error_msg = ws_message.data.get(
                                "error_message", "Unknown error"
                            )
                            print_error(f"❌ Project failed: {error_msg}")
                            break

                        elif ws_message.type == WSMessageType.PROJECT_CANCELLED:
                            print_warning("⚠️ Project was cancelled")
                            break

                        elif ws_message.type == WSMessageType.ERROR:
                            error_msg = ws_message.data.get("message", "Unknown error")
                            print_error(f"❌ Error: {error_msg}")

                        elif ws_message.type == WSMessageType.WARNING:
                            warning_msg = ws_message.data.get("message", "Warning")
                            print_warning(f"⚠️ Warning: {warning_msg}")

                        elif ws_message.type == WSMessageType.INFO:
                            info_msg = ws_message.data.get("message", "Info")
                            if self.verbose:
                                print_info(f"ℹ️ {info_msg}")  # noqa: RUF001

                    except json.JSONDecodeError as e:
                        self._log(f"Invalid JSON received: {e}")

                    except Exception as e:
                        self._log(f"Error processing message: {e}")

        except ConnectionClosed:
            if not self._shutdown:
                print_warning("Connection lost. Reconnecting...")
                # Could implement retry logic here

        except KeyboardInterrupt:
            print_info("Stopped streaming")

        except Exception as e:
            if self.verbose:
                print_error(f"Streaming error: {e}")
            else:
                print_error("Streaming error. Use --verbose for details.")

        finally:
            await self.disconnect()

        return True

    async def stream_logs(
        self,
        project_id: UUID,
        log_level: str = "INFO",
    ) -> bool:
        """
        Stream real-time logs for a project.

        Args:
            project_id: Project ID to monitor
            log_level: Minimum log level to display
        """
        # Connect to project-specific endpoint
        endpoint = f"/ws/cli/{project_id}?format=logs"

        if not await self.connect(endpoint):
            return False

        print_info(f"📋 Streaming logs for project {project_id} (level: {log_level})")

        try:
            if self.websocket is None:
                return False
            async for message in self.websocket:
                if self._shutdown:
                    break

                try:
                    message_data = deserialize(message)
                    ws_message = WSMessage(**message_data)

                    # Handle heartbeat
                    if ws_message.type == WSMessageType.HEARTBEAT:
                        await self.send_heartbeat_response()
                        continue

                    # Display log messages
                    if (
                        hasattr(ws_message, "formatted_text")
                        and ws_message.formatted_text
                    ):
                        self.console.print(ws_message.formatted_text)
                    else:
                        # Fallback to basic message display
                        timestamp = ws_message.timestamp.strftime("%H:%M:%S")
                        self.console.print(
                            f"[dim]{timestamp}[/dim] {ws_message.type}: {ws_message.data}"
                        )

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    if self.verbose:
                        self._log(f"Error processing log message: {e}")

        except KeyboardInterrupt:
            print_info("Stopped streaming logs")

        finally:
            await self.disconnect()

        return True

    async def test_connection(self) -> bool:
        """Test WebSocket connection to the server."""
        try:
            endpoint = "/ws/health"
            ws_url = f"{self.base_url.rstrip('/')}{endpoint}"

            if self.token:
                ws_url += f"?token={self.token}"

            async with websockets.connect(ws_url, ping_interval=None) as websocket:
                # Send a test message
                test_msg = {
                    "type": "test",
                    "timestamp": datetime.utcnow().isoformat(),
                }
                await websocket.send(serialize_to_str(test_msg))

                # Wait for response (with timeout)
                try:
                    raw_response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    response = raw_response if isinstance(raw_response, str) else raw_response.decode('utf-8')
                    self._log(f"Test response: {response}")
                    return True
                except TimeoutError:
                    return False

        except Exception as e:
            if self.verbose:
                print_error(f"Connection test failed: {e}")
            return False


# Convenience functions for CLI usage
async def stream_project_progress(
    project_id: UUID,
    formatter: OutputFormatter,
    token: str | None = None,
    verbose: bool = False,
) -> bool:
    """
    Convenience function to stream project progress.

    Args:
        project_id: Project ID to monitor
        formatter: Output formatter
        token: Authentication token
        verbose: Enable verbose logging

    Returns:
        True if streaming was successful
    """
    async with CLIWebSocketClient(token=token, verbose=verbose) as client:
        result: bool = await client.stream_progress(project_id, formatter)
        return result


async def test_websocket_connection(
    token: str | None = None,
    verbose: bool = False,
) -> bool:
    """
    Test WebSocket connection to server.

    Args:
        token: Authentication token
        verbose: Enable verbose logging

    Returns:
        True if connection successful
    """
    client = CLIWebSocketClient(token=token, verbose=verbose)
    return await client.test_connection()
