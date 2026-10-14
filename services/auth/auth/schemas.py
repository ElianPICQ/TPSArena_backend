import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=20, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class RegisterResponse(BaseModel):
    user_id: uuid.UUID


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class EmailRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class MessageResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    # Email or username (usernames can't contain "@"). `email` is still accepted as the field name.
    identifier: str = Field(
        min_length=1, max_length=320, validation_alias=AliasChoices("identifier", "email"), description="Email or username"
    )
    password: str = Field(max_length=128)

    @field_validator("identifier")
    @classmethod
    def lower_identifier(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def is_email(self) -> bool:
        return "@" in self.identifier


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    username: str
    created_at: datetime


class PublicUser(BaseModel):
    user_id: uuid.UUID
    username: str


class UsersResponse(BaseModel):
    users: list[PublicUser]


class DeleteAccountRequest(BaseModel):
    password: str = Field(max_length=128)


class ErrorResponse(BaseModel):
    error: str
    message: str
