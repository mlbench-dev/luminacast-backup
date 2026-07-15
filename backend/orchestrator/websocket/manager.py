"""
WebSocket Connection Manager.
Handles client connections, authentication, and message broadcasting.
"""
import json
import logging
from typing import Optional
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from config import settings

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for live stream sessions."""

    def __init__(self):
        # session_id -> list of (websocket, user_id)
        self._session_connections: dict[str, list[tuple[WebSocket, str]]] = {}
        # user_id -> list of websockets
        self._user_connections: dict[str, list[WebSocket]] = {}
        # Internal service connections
        self._internal_connections: list[WebSocket] = []

    async def authenticate(self, websocket: WebSocket, token: str) -> Optional[dict]:
        """Authenticate a WebSocket connection using JWT token."""
        try:
            payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])
            return payload
        except JWTError:
            return None

    async def authenticate_internal(self, token: str) -> bool:
        """Authenticate internal service (chat-monitor) connection."""
        return token == settings.INTERNAL_SERVICE_SECRET

    async def connect_session(self, websocket: WebSocket, session_id: str, user_id: str):
        """Register a client connection for a stream session."""
        await websocket.accept()

        if session_id not in self._session_connections:
            self._session_connections[session_id] = []
        self._session_connections[session_id].append((websocket, user_id))

        if user_id not in self._user_connections:
            self._user_connections[user_id] = []
        self._user_connections[user_id].append(websocket)

    async def connect_internal(self, websocket: WebSocket):
        """Register an internal service connection."""
        await websocket.accept()
        self._internal_connections.append(websocket)

    def disconnect_session(self, websocket: WebSocket, session_id: str, user_id: str):
        """Remove a client connection."""
        if session_id in self._session_connections:
            self._session_connections[session_id] = [
                (ws, uid) for ws, uid in self._session_connections[session_id]
                if ws != websocket
            ]
            if not self._session_connections[session_id]:
                del self._session_connections[session_id]

        if user_id in self._user_connections:
            self._user_connections[user_id] = [
                ws for ws in self._user_connections[user_id] if ws != websocket
            ]
            if not self._user_connections[user_id]:
                del self._user_connections[user_id]

    def disconnect_internal(self, websocket: WebSocket):
        """Remove an internal service connection."""
        self._internal_connections = [ws for ws in self._internal_connections if ws != websocket]

    async def broadcast_to_session(self, session_id: str, message: dict):
        """Send message to all clients connected to a session."""
        if session_id not in self._session_connections:
            return

        dead = []
        for ws, user_id in self._session_connections[session_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append((ws, user_id))

        for ws, user_id in dead:
            self.disconnect_session(ws, session_id, user_id)

    async def broadcast_to_user(self, user_id: str, message: dict):
        """Send message to all connections of a specific user."""
        if user_id not in self._user_connections:
            return

        dead = []
        for ws in self._user_connections[user_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._user_connections[user_id].remove(ws)

    async def broadcast_to_internal(self, message: dict):
        """Send message to all internal service connections."""
        dead = []
        for ws in self._internal_connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._internal_connections.remove(ws)

    def get_session_operators(self, session_id: str) -> list[dict]:
        """Get list of operators connected to a session."""
        if session_id not in self._session_connections:
            return []
        return [
            {"user_id": user_id, "status": "online"}
            for _, user_id in self._session_connections[session_id]
        ]


ws_manager = ConnectionManager()
