"""B5 Core -> Gateway outbound contract.

This DTO deliberately has no Evolution fields.  Provider-specific mapping is
owned by Gateway delivery handlers only.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


ProviderCode = Literal["evolution", "meta"]
ChannelType = Literal["whatsapp", "instagram"]
MessageKind = Literal["text", "image", "video", "audio", "document", "sticker", "reaction", "unknown"]


class _CanonicalModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CanonicalTrace(_CanonicalModel):
    request_id: StrictStr | None = Field(default=None, alias="requestId", max_length=255)
    correlation_id: StrictStr | None = Field(default=None, alias="correlationId", max_length=255)


class CanonicalAttachment(_CanonicalModel):
    kind: MessageKind
    provider_media_id: StrictStr | None = Field(default=None, alias="providerMediaId", max_length=255)
    url: StrictStr | None = Field(default=None, max_length=4096)
    mime_type: StrictStr | None = Field(default=None, alias="mimeType", max_length=255)
    filename: StrictStr | None = Field(default=None, max_length=1024)
    size: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalOutboundChannel(_CanonicalModel):
    id: UUID
    channel_type: ChannelType = Field(alias="type")
    provider: ProviderCode


class CanonicalRecipient(_CanonicalModel):
    external_id: StrictStr = Field(alias="externalId", min_length=1, max_length=255)


class CanonicalOutboundPayload(_CanonicalModel):
    kind: MessageKind
    content: StrictStr | None = None
    attachments: list[CanonicalAttachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _requires_content_for_text(self) -> "CanonicalOutboundPayload":
        if self.kind == "text" and not (self.content or "").strip():
            raise ValueError("message.content is required for text messages")
        return self


class CanonicalOutboundMessage(_CanonicalModel):
    channel: CanonicalOutboundChannel
    recipient: CanonicalRecipient
    message: CanonicalOutboundPayload
    idempotency_key: StrictStr = Field(alias="idempotencyKey", min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace: CanonicalTrace = Field(default_factory=CanonicalTrace)

    @model_validator(mode="after")
    def _reject_sensitive_metadata(self) -> "CanonicalOutboundMessage":
        _assert_no_secrets(self.metadata)
        return self


def _assert_no_secrets(value: object) -> None:
    if not isinstance(value, dict):
        return
    forbidden = {"access_token", "refresh_token", "api_key", "apikey", "authorization", "password", "secret"}
    for key, nested in value.items():
        if str(key).strip().lower().replace("-", "_") in forbidden:
            raise ValueError("Canonical metadata must not contain credentials.")
        _assert_no_secrets(nested)
