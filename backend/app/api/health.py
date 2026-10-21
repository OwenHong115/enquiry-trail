"""Health routes (S7a skeleton; behaviour arrives in Green)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/health", tags=["health"])


class HealthOut(BaseModel):
    status: str


@router.get("/live", response_model=HealthOut)
def live():
    return HealthOut(status="ok")
