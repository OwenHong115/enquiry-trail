"""Review-resolution route.

The request model mirrors the ReviewResolve contract. Session and
CSRF are enforced via the shared require_operator dependency; the
resolution itself is the S4a review service, and the 200 response
reuses the enquiry-detail builder.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_operator
from app.api.enquiries import EnquiryDetailOut, build_enquiry_detail
from app.api.errors import ApiError, ErrorEnvelope
from app.db.session import get_session
from app.services.errors import EnquiryNotFound, VersionConflict
from app.services.review import resolve_review

router = APIRouter(prefix="/api/v1/enquiries", tags=["reviews"])


class ReviewResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    resolution_note: str = Field(min_length=10, max_length=500)


@router.post(
    "/{enquiry_id}/review/resolve",
    response_model=EnquiryDetailOut,
    # Schema metadata only: enforcement stays in require_operator.
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def resolve_review_route(
    enquiry_id: uuid.UUID,
    payload: ReviewResolveIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    resolved = require_operator(request, session, csrf_token)
    # Auth reads opened a read transaction; end it so the service
    # can own its single write transaction.
    session.rollback()
    try:
        resolve_review(
            session,
            enquiry_id,
            expected_version=payload.expected_version,
            operator_id=resolved.operator_id,
            resolution_note=payload.resolution_note,
            clock=request.app.state.clock,
        )
    except EnquiryNotFound as err:
        raise ApiError(404, "not_found", "Enquiry not found") from err
    except VersionConflict as err:
        raise ApiError(
            409, "version_conflict", "Enquiry version conflict"
        ) from err

    detail = build_enquiry_detail(session, enquiry_id)
    if detail is None:  # pragma: no cover - resolved a moment ago
        raise ApiError(404, "not_found", "Enquiry not found")
    return detail
