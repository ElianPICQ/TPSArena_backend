from urllib.parse import quote

from fastapi import APIRouter, Request, Response

from bff import upstream

router = APIRouter(prefix="/auth", tags=["auth"])


async def forward(request: Request, path: str, method: str = "POST") -> Response:
    return await upstream.forward(request, request.app.state.auth_client, "auth", path, method)


@router.post("/register")
async def register(request: Request) -> Response:
    return await forward(request, "/register")


@router.post("/verify-email")
async def verify_email(request: Request) -> Response:
    return await forward(request, "/verify-email")


@router.post("/verify-email/resend")
async def resend_verification_code(request: Request) -> Response:
    return await forward(request, "/verify-email/resend")


@router.post("/login")
async def login(request: Request) -> Response:
    return await forward(request, "/login")


@router.post("/refresh")
async def refresh(request: Request) -> Response:
    return await forward(request, "/refresh")


@router.post("/logout")
async def logout(request: Request) -> Response:
    return await forward(request, "/logout")


@router.post("/password-reset/request")
async def request_password_reset(request: Request) -> Response:
    return await forward(request, "/password-reset/request")


@router.post("/password-reset/confirm")
async def confirm_password_reset(request: Request) -> Response:
    return await forward(request, "/password-reset/confirm")


@router.get("/me")
async def get_me(request: Request) -> Response:
    return await forward(request, "/me", "GET")


@router.post("/me/delete")
async def delete_me(request: Request) -> Response:
    return await forward(request, "/me/delete")


@router.get("/users")
async def get_users(request: Request) -> Response:
    return await forward(request, "/users", "GET")


@router.get("/users/by-username/{username}")
async def get_user_by_username(username: str, request: Request) -> Response:
    return await forward(request, f"/users/by-username/{quote(username, safe='')}", "GET")
