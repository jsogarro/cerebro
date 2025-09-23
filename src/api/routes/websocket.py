"""
WebSocket API routes for real-time communication.

This module provides WebSocket endpoints for streaming real-time updates
to various clients including web browsers and CLI tools.
"""

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from structlog import get_logger

from src.api.websocket.auth import (
    WebSocketAuthError,
    authenticate_websocket_connection,
    verify_project_access,
)
from src.api.websocket.connection_manager import websocket_manager
from src.models.websocket_messages import (
    SubscriptionRequest,
    WSMessage,
    WSMessageType,
)

logger = get_logger()
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(None, description="JWT authentication token"),
):
    """
    General purpose WebSocket endpoint for real-time updates.

    Clients can subscribe to various project and user events through this endpoint.
    Authentication is handled via query parameter or first message.
    """
    client_id = None

    try:
        # Get User-Agent for client type detection
        user_agent = websocket.headers.get("user-agent")

        # Authenticate connection
        user_id, client_type = await authenticate_websocket_connection(
            token=token,
            user_agent=user_agent,
        )

        # Establish connection
        client_id = await websocket_manager.connect(
            websocket=websocket,
            client_type=client_type,
            user_id=user_id,
        )

        logger.info(
            "WebSocket connection established",
            client_id=client_id,
            user_id=user_id,
            client_type=client_type,
        )

        # Handle messages
        while True:
            try:
                # Receive message from client
                data = await websocket.receive_text()
                message_data = json.loads(data)

                # Handle subscription requests
                if message_data.get("type") == "subscription":
                    subscription_request = SubscriptionRequest(
                        **message_data.get("data", {})
                    )
                    response = await websocket_manager.handle_subscription_request(
                        client_id, subscription_request
                    )

                    # Send response back to client
                    response_message = WSMessage(
                        type=WSMessageType.INFO,
                        data=response.model_dump(),
                    )
                    await websocket_manager.connections[client_id].send_message(
                        response_message
                    )

                # Handle heartbeat responses
                elif message_data.get("type") == "heartbeat_response":
                    websocket_manager.connections[client_id].update_heartbeat()

                else:
                    logger.warning(
                        "Unknown WebSocket message type",
                        client_id=client_id,
                        message_type=message_data.get("type"),
                    )

            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON received from WebSocket client",
                    client_id=client_id,
                )

            except Exception as e:
                logger.error(
                    "Error processing WebSocket message",
                    client_id=client_id,
                    error=str(e),
                )

    except WebSocketAuthError as e:
        logger.warning(
            "WebSocket authentication failed",
            error=e.message,
        )
        await websocket.close(code=e.code, reason=e.message)

    except WebSocketDisconnect:
        logger.info(
            "WebSocket client disconnected",
            client_id=client_id,
        )

    except Exception as e:
        logger.error(
            "WebSocket connection error",
            client_id=client_id,
            error=str(e),
        )

    finally:
        # Clean up connection
        if client_id:
            await websocket_manager.disconnect(client_id)


@router.websocket("/ws/projects/{project_id}")
async def project_websocket_endpoint(
    websocket: WebSocket,
    project_id: UUID,
    token: str | None = Query(None, description="JWT authentication token"),
):
    """
    Project-specific WebSocket endpoint for real-time project updates.

    Automatically subscribes the client to updates for the specified project.
    Ideal for project-specific dashboards and CLI monitoring.
    """
    client_id = None

    try:
        # Get User-Agent for client type detection
        user_agent = websocket.headers.get("user-agent")

        # Authenticate connection
        user_id, client_type = await authenticate_websocket_connection(
            token=token,
            user_agent=user_agent,
        )

        # Verify project access
        if not await verify_project_access(user_id, str(project_id)):
            raise WebSocketAuthError("Access denied to project")

        # Establish connection
        client_id = await websocket_manager.connect(
            websocket=websocket,
            client_type=client_type,
            user_id=user_id,
        )

        # Auto-subscribe to project
        websocket_manager.subscribe_to_project(client_id, project_id)

        logger.info(
            "Project WebSocket connection established",
            client_id=client_id,
            project_id=str(project_id),
            user_id=user_id,
            client_type=client_type,
        )

        # Send initial project state (if available)
        # TODO: Send current project status and progress

        # Handle messages (mainly heartbeats and additional subscriptions)
        while True:
            try:
                data = await websocket.receive_text()
                message_data = json.loads(data)

                # Handle heartbeat responses
                if message_data.get("type") == "heartbeat_response":
                    websocket_manager.connections[client_id].update_heartbeat()

                # Handle additional subscription requests
                elif message_data.get("type") == "subscription":
                    subscription_request = SubscriptionRequest(
                        **message_data.get("data", {})
                    )
                    response = await websocket_manager.handle_subscription_request(
                        client_id, subscription_request
                    )

                    response_message = WSMessage(
                        type=WSMessageType.INFO,
                        data=response.model_dump(),
                    )
                    await websocket_manager.connections[client_id].send_message(
                        response_message
                    )

            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON received from project WebSocket client",
                    client_id=client_id,
                    project_id=str(project_id),
                )

            except Exception as e:
                logger.error(
                    "Error processing project WebSocket message",
                    client_id=client_id,
                    project_id=str(project_id),
                    error=str(e),
                )

    except WebSocketAuthError as e:
        logger.warning(
            "Project WebSocket authentication failed",
            project_id=str(project_id),
            error=e.message,
        )
        await websocket.close(code=e.code, reason=e.message)

    except WebSocketDisconnect:
        logger.info(
            "Project WebSocket client disconnected",
            client_id=client_id,
            project_id=str(project_id),
        )

    except Exception as e:
        logger.error(
            "Project WebSocket connection error",
            client_id=client_id,
            project_id=str(project_id),
            error=str(e),
        )

    finally:
        # Clean up connection
        if client_id:
            await websocket_manager.disconnect(client_id)


@router.websocket("/ws/cli/{project_id}")
async def cli_websocket_endpoint(
    websocket: WebSocket,
    project_id: UUID,
    token: str | None = Query(None, description="JWT authentication token"),
    format: str = Query("text", description="Output format for CLI"),
):
    """
    CLI-optimized WebSocket endpoint for command-line tools.

    Provides simplified, text-based messaging optimized for terminal output.
    Includes progress bars, formatted status updates, and minimal JSON overhead.
    """
    client_id = None

    try:
        # Force client type to CLI
        user_id, _ = await authenticate_websocket_connection(
            token=token,
            user_agent="research-cli",  # Force CLI detection
        )

        # Verify project access
        if not await verify_project_access(user_id, str(project_id)):
            raise WebSocketAuthError("Access denied to project")

        # Establish connection with CLI type
        client_id = await websocket_manager.connect(
            websocket=websocket,
            client_type="cli",
            user_id=user_id,
        )

        # Auto-subscribe to project
        websocket_manager.subscribe_to_project(client_id, project_id)

        logger.info(
            "CLI WebSocket connection established",
            client_id=client_id,
            project_id=str(project_id),
            user_id=user_id,
            format=format,
        )

        # Send CLI-friendly welcome message
        welcome_message = WSMessage(
            type=WSMessageType.INFO,
            project_id=project_id,
            data={
                "message": f"Streaming updates for project {project_id}",
                "format": format,
            },
        )
        await websocket_manager.connections[client_id].send_message(welcome_message)

        # Handle messages (mainly heartbeats)
        while True:
            try:
                # Set a timeout for CLI connections to be more responsive
                data = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                message_data = json.loads(data)

                # Handle heartbeat responses
                if message_data.get("type") == "heartbeat_response":
                    websocket_manager.connections[client_id].update_heartbeat()

            except TimeoutError:
                # Timeout is expected for CLI connections
                continue

            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON received from CLI WebSocket client",
                    client_id=client_id,
                    project_id=str(project_id),
                )

            except Exception as e:
                logger.error(
                    "Error processing CLI WebSocket message",
                    client_id=client_id,
                    project_id=str(project_id),
                    error=str(e),
                )

    except WebSocketAuthError as e:
        logger.warning(
            "CLI WebSocket authentication failed",
            project_id=str(project_id),
            error=e.message,
        )
        await websocket.close(code=e.code, reason=e.message)

    except WebSocketDisconnect:
        logger.info(
            "CLI WebSocket client disconnected",
            client_id=client_id,
            project_id=str(project_id),
        )

    except Exception as e:
        logger.error(
            "CLI WebSocket connection error",
            client_id=client_id,
            project_id=str(project_id),
            error=str(e),
        )

    finally:
        # Clean up connection
        if client_id:
            await websocket_manager.disconnect(client_id)


# Health endpoint for WebSocket service
@router.get("/ws/health")
async def websocket_health():
    """Get WebSocket service health and statistics."""
    stats = websocket_manager.get_stats()

    return {
        "status": "healthy",
        "websocket_stats": stats,
        "timestamp": "2024-01-01T00:00:00Z",  # Will be updated with actual timestamp
    }
