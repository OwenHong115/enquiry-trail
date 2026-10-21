"""Auth routes (S7a skeleton; behaviour arrives in Green)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class SessionOut(BaseModel):
    operator_id: uuid.UUID
    username: str
    csrf_token: str
    expires_at: datetime


@router.post("/login", response_model=SessionOut)
def login(payload: LoginInput, session: Session = Depends(get_session)):
    raise NotImplementedError


@router.get("/session", response_model=SessionOut)
def get_session_info(session: Session = Depends(get_session)):
    raise NotImplementedError


@router.post("/logout", status_code=204)
def logout(session: Session = Depends(get_session)):
    raise NotImplementedError
