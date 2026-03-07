"""WebSocket connection manager for real-time progress updates"""

from fastapi import WebSocket
from typing import Dict, List
import json


class WebSocketManager:
    """Manages WebSocket connections for game generation progress"""
    
    def __init__(self):
        """Initialize connection manager"""
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, game_id: str, websocket: WebSocket):
        """
        Add a new WebSocket connection.
        
        Args:
            game_id: Unique game identifier
            websocket: WebSocket connection
        """
        await websocket.accept()
        if game_id not in self.active_connections:
            self.active_connections[game_id] = []
        self.active_connections[game_id].append(websocket)
    
    def disconnect(self, game_id: str, websocket: WebSocket):
        """
        Remove a WebSocket connection.
        
        Args:
            game_id: Unique game identifier
            websocket: WebSocket connection to remove
        """
        if game_id in self.active_connections:
            self.active_connections[game_id].remove(websocket)
            if len(self.active_connections[game_id]) == 0:
                del self.active_connections[game_id]
    
    async def send_progress(
        self, 
        game_id: str, 
        stage: str, 
        pct: int, 
        message: str,
        details: dict = None
    ):
        """
        Send progress update to all connections for a game.
        
        Args:
            game_id: Unique game identifier
            stage: Current generation stage
            pct: Progress percentage (0-100)
            message: Human-readable message
            details: Additional details
        """
        if game_id not in self.active_connections:
            return
        
        payload = {
            "type": "progress",
            "stage": stage,
            "pct": pct,
            "message": message,
            "details": details or {}
        }
        
        await self._broadcast(game_id, json.dumps(payload))
    
    async def send_complete(self, game_id: str, preview_url: str):
        """
        Send completion message to all connections.
        
        Args:
            game_id: Unique game identifier
            preview_url: URL to preview generated game
        """
        if game_id not in self.active_connections:
            return
        
        payload = {
            "type": "complete",
            "preview_url": preview_url
        }
        
        await self._broadcast(game_id, json.dumps(payload))
    
    async def send_error(self, game_id: str, error_message: str):
        """
        Send error message to all connections.
        
        Args:
            game_id: Unique game identifier
            error_message: Error message
        """
        if game_id not in self.active_connections:
            return
        
        payload = {
            "type": "error",
            "message": error_message
        }
        
        await self._broadcast(game_id, json.dumps(payload))
    
    async def _broadcast(self, game_id: str, message: str):
        """
        Broadcast message to all connections for a game.
        
        Args:
            game_id: Unique game identifier
            message: Message to broadcast
        """
        if game_id not in self.active_connections:
            return
        
        disconnected = []
        for connection in self.active_connections[game_id]:
            try:
                await connection.send_text(message)
            except Exception as e:
                # Connection likely closed
                disconnected.append(connection)
        
        # Clean up disconnected connections
        for connection in disconnected:
            self.disconnect(game_id, connection)


# Global manager instance
manager = WebSocketManager()
