from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.models.canonical_outbound import CanonicalOutboundMessage
from app.services.canonical_outbound import CanonicalOutboundError, CanonicalOutboundService


router = APIRouter(prefix="/v1/outbound", tags=["canonical-outbound"])


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_canonical_outbound_message(
    body: CanonicalOutboundMessage,
    request: Request,
    contract_version: str = Header(..., alias="X-Botly-Contract-Version"),
):
    if contract_version != "canonical-v1":
        raise HTTPException(status_code=400, detail={"code": "unsupported_contract_version", "message": "X-Botly-Contract-Version must be canonical-v1."})
    try:
        return await CanonicalOutboundService().deliver(
            message=body,
            authenticated_instance=getattr(request.state, "auth_instance", None),
        )
    except CanonicalOutboundError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
