import uuid

from fastapi import APIRouter, Request, Response

from bff import upstream

router = APIRouter(prefix="/servers", tags=["servers"])


async def forward(request: Request, path: str, method: str = "POST") -> Response:
    return await upstream.forward(request, request.app.state.serverlist_client, "serverlist", path, method)


@router.get("")
async def list_servers(request: Request) -> Response:
    """Public server list. Query: build_version (required), region (optional)."""
    return await forward(request, "/servers", "GET")


@router.post("/{server_id}/join-ticket")
async def create_join_ticket(server_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/servers/{server_id}/join-ticket")
