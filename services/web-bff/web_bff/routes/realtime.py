"""Presence and friend message history: forwarded to Real-time.

The chat itself goes over the WebSocket, which the site's nginx sends straight to Real-time
(`/ws`): it doesn't go through this service.
"""

import uuid

from fastapi import APIRouter, Request, Response

from web_bff import upstream

router = APIRouter(tags=["realtime"])


async def forward(request: Request, path: str) -> Response:
    return await upstream.forward(request, request.app.state.realtime_client, "realtime", path, "GET")


@router.get("/presence")
async def get_presence(request: Request) -> Response:
    """Query: ids (comma-separated). Only the caller's friends are answered."""
    return await forward(request, "/presence")


@router.get("/conversations/{friend_id}/messages")
async def get_conversation(friend_id: uuid.UUID, request: Request) -> Response:
    """Query: limit, before (next_cursor from the previous page)."""
    return await forward(request, f"/conversations/{friend_id}/messages")
