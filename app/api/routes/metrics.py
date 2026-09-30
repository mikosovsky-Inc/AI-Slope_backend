import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.api.dependencies import DbSession
from app.core.config import Settings, get_settings
from app.modules.observability.metrics import exposition


def authorize_metrics(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
):
    if not settings.metrics_enabled:
        raise HTTPException(404, "Not found")
    expected = settings.metrics_token.get_secret_value() if settings.metrics_token else ""
    scheme, _, supplied = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not expected
        or not secrets.compare_digest(supplied.encode(), expected.encode())
    ):
        raise HTTPException(
            401, "Metrics credential required", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(dependencies=[Depends(authorize_metrics)])


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request, db: DbSession) -> Response:
    return Response(
        exposition(db, request.app.state.http_metrics),
        headers={"Content-Type": CONTENT_TYPE_LATEST, "Cache-Control": "no-store"},
    )
