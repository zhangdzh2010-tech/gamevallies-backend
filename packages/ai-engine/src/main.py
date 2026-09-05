"""FastAPI application for PlayForge AI Engine"""

import asyncio
import os
from .services.fc_runtime import FCTransportBoundary
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from .config.settings import settings
from .api.endpoints.generate import router as generate_router
from .services.websocket_manager import manager
from .api.models import GenerateProgress
from .services.llm_client import _build_openai_compatible_chat_url

# Lifecycle events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    if os.getenv("FC_DEPLOYMENT") == "true":
        from redis.asyncio import Redis
        if not settings.REDIS_URL:
            raise RuntimeError("FC requires Redis task persistence")
        client = Redis.from_url(settings.REDIS_URL)
        try:
            await client.ping()
        finally:
            await client.aclose()
    # Startup
    print(f"Starting PlayForge AI Engine in {settings.ENVIRONMENT} mode")
    print(f"LLM Mode: {settings.LLM_MODE}")
    if settings.LLM_MODE == "real" and settings.LLM_API_KEY and settings.LLM_BASE_URL:
        print(f"LLM Base URL: {settings.LLM_BASE_URL}")
        print(f"LLM Chat Endpoint: {_build_openai_compatible_chat_url(settings.LLM_BASE_URL)}")
        print(f"LLM Model: {settings.LLM_MODEL}")
    yield
    # Shutdown
    print("Shutting down PlayForge AI Engine")


# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description="AI Engine for PlayForge game generation",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(FCTransportBoundary)

# Include routers
app.include_router(generate_router)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "status": "running",
        "environment": settings.ENVIRONMENT
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "PlayForge AI Engine"
    }


@app.websocket("/ws/generation/{game_id}")
async def websocket_generation(websocket: WebSocket, game_id: str):
    """
    WebSocket endpoint for real-time generation progress.
    
    Args:
        websocket: WebSocket connection
        game_id: Game ID for tracking progress
    """
    await manager.connect(game_id, websocket)
    try:
        while True:
            # Keep connection alive and listen for messages
            data = await websocket.receive_text()
            
            # Handle any incoming messages (e.g., cancel requests)
            if "cancel" in data.lower():
                await manager.send_error(game_id, "Generation cancelled by client")
                break
    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
    except Exception as e:
        manager.disconnect(game_id, websocket)
        print(f"WebSocket error for game {game_id}: {str(e)}")


@app.post("/api/v1/ai/health")
async def detailed_health():
    """Detailed health check"""
    return {
        "status": "healthy",
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "environment": settings.ENVIRONMENT,
        "llm_mode": settings.LLM_MODE,
        "active_connections": sum(len(conns) for conns in manager.active_connections.values())
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=settings.ENVIRONMENT == "development"
    )

