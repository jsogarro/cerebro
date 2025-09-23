"""
WebSocket package for real-time communication.
"""

from .connection_manager import ConnectionManager, websocket_manager

__all__ = ["ConnectionManager", "websocket_manager"]
