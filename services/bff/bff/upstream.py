import json
import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def unavailable(service: str) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"error": f"{service}_unavailable", "message": f"The {service} service is unavailable."}
    )


async def send(
    request: Request, client: httpx.AsyncClient, service: str, path: str, method: str
) -> httpx.Response | JSONResponse:
    """Send the request (body, query string, player's JWT) to a service.

    Returns the service's response, or a ready-made 503 if it can't be reached.
    """
    headers = {"content-type": "application/json"}
    if request.client:
        # Lets services rate limit by the player's IP instead of the BFF's.
        headers["x-forwarded-for"] = request.client.host
    if authorization := request.headers.get("authorization"):
        # The BFF forwards the player's JWT; each service checks it.
        headers["authorization"] = authorization
    try:
        return await client.request(
            method, path, params=request.query_params, content=await request.body(), headers=headers
        )
    except httpx.RequestError as exc:
        log.warning("%s unreachable on %s: %s", service, path, exc)
        return unavailable(service)


def relay(upstream: httpx.Response) -> Response:
    """The service's answer, unchanged. Empty answers (e.g. 204) are passed through without a body."""
    if not upstream.content:
        return Response(status_code=upstream.status_code)
    try:
        content = upstream.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=502, content={"error": "bad_gateway", "message": "Invalid upstream response."})
    return JSONResponse(status_code=upstream.status_code, content=content)


async def forward(request: Request, client: httpx.AsyncClient, service: str, path: str, method: str) -> Response:
    """Send the request to a service and return its answer unchanged.

    The service owns validation, so its error bodies reach the client as-is.
    """
    upstream = await send(request, client, service, path, method)
    return upstream if isinstance(upstream, Response) else relay(upstream)
