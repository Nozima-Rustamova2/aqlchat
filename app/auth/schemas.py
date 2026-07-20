from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    owner_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone_number: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class MerchantOut(BaseModel):
    id: str
    owner_name: str | None
    email: str | None
    phone_number: str | None
    webhook_slug: str
    telegram_connected: bool
    instagram_connected: bool
    vertical: str | None
    telegram_bot_username: str | None
    ui_language: str


class UpdateLanguageRequest(BaseModel):
    ui_language: Literal["uz", "ru", "en"]
