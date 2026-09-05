from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreateInstanceRequest(BaseModel):
    instance_name: str = Field(..., pattern=r"^[a-z0-9_]{1,64}$")
    connection_type: Literal["baileys", "cloud"] = "baileys"
    qrcode: bool = True
    token: str | None = None
    phone_number_id: str | None = None
    business_id: str | None = None
    # Configura el webhook automáticamente al crear la instancia
    auto_configure_webhook: bool = True

    @model_validator(mode="after")
    def validate_connection_payload(self) -> "CreateInstanceRequest":
        if self.connection_type == "cloud":
            missing = [
                field
                for field, value in {
                    "token": self.token,
                    "phone_number_id": self.phone_number_id,
                    "business_id": self.business_id,
                }.items()
                if not str(value or "").strip()
            ]
            if missing:
                raise ValueError(f"Campos obligatorios para WhatsApp Oficial: {', '.join(missing)}")
            self.qrcode = False
        return self


class CreateClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Client name is required")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value or None


class UpdateClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Client name cannot be null")
        if not value:
            raise ValueError("Client name is required")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateClientRequest":
        if not self.model_fields_set:
            raise ValueError("At least one client field must be provided")
        return self


class CreateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(..., min_length=1, max_length=128)
    channel: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="WhatsApp", min_length=1, max_length=160)
    provider: Literal["meta", "evolution"] = "meta"


class CreateConnectionSetupRequest(CreateConnectionRequest):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ChannelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class UpdateChannelSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: dict[str, ChannelSettingsUpdate] = Field(..., min_length=1)


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class UpdateProviderSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderSettingsUpdate] = Field(..., min_length=1)


class UpdateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Connection name cannot be null")
        if not value:
            raise ValueError("Connection name is required")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "UpdateConnectionRequest":
        if not self.model_fields_set:
            raise ValueError("At least one connection field must be provided")
        return self


class CoreChannelBindingRequest(BaseModel):
    """A browser selects only a safe Core Channel identifier.

    Gateway resolves the dispatch credential server-to-server through Core's
    control-plane and never accepts one from an interactive client.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    core_channel_id: str = Field(..., min_length=1, max_length=256)


WebhookAuthType = Literal["NONE", "BEARER", "API_KEY", "BASIC", "CUSTOM_HEADERS", "QUERY_PARAM"]


class ConnectionWebhookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(..., min_length=8, max_length=2048)
    auth_type: WebhookAuthType | None = None
    auth_config: dict[str, str] | None = None
    custom_headers: dict[str, str] | None = None


class ConnectionQuickMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # ``recipient`` is the provider-neutral UI field. ``number`` remains
    # accepted for existing WhatsApp clients while the connection service owns
    # any provider-specific normalization.
    recipient: str | None = Field(default=None, min_length=1, max_length=512)
    number: str | None = Field(default=None, min_length=5, max_length=40)
    text: str = Field(..., min_length=1, max_length=4096)

    @model_validator(mode="after")
    def require_recipient(self) -> "ConnectionQuickMessageRequest":
        if not (self.recipient or self.number):
            raise ValueError("recipient is required")
        return self

    @property
    def external_id(self) -> str:
        return str(self.recipient or self.number or "")


class InstagramOutboundMessageRequest(BaseModel):
    """A text send whose recipient is an opaque Instagram external ID."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_id: str = Field(..., min_length=1, max_length=512)
    text: str = Field(..., min_length=1, max_length=4096)


class SendTextRequest(BaseModel):
    number: str = Field(..., min_length=5)
    text: str = Field(..., min_length=1, max_length=4096)


class SendMediaRequest(BaseModel):
    number: str
    media_url: str
    mediatype: Literal["image", "video", "audio", "document", "pdf", "file"]
    mimetype: str
    file_name: str
    caption: str = ""


class SendUploadedMediaRequest(BaseModel):
    number: str
    file_id: str
    mediatype: Literal["image", "video", "audio", "document", "pdf", "file"]
    caption: str = ""


UnifiedMessageType = Literal["text", "image", "video", "audio", "document", "file", "pdf"]


class SendMessageRequest(BaseModel):
    number: str = Field(..., min_length=5)
    type: UnifiedMessageType
    text: str | None = Field(default=None, max_length=4096)
    caption: str | None = Field(default=None, max_length=4096)
    mediaUrl: str | None = None
    base64: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActivityTestRecordRequest(BaseModel):
    test_type: str = Field(min_length=1, max_length=60)
    result: Literal["started", "passed", "warning", "failed"] = "started"
    correlation_id: str = Field(min_length=1, max_length=120)
    operator: str | None = Field(default=None, max_length=120)
    duration_ms: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=500)


class ButtonItem(BaseModel):
    display_text: str
    id: str


class SendButtonsRequest(BaseModel):
    number: str
    title: str
    description: str
    footer: str = ""
    buttons: list[ButtonItem]


class ListRow(BaseModel):
    title: str
    description: str = ""
    row_id: str


class ListSection(BaseModel):
    title: str
    rows: list[ListRow]


class SendListRequest(BaseModel):
    number: str
    title: str
    description: str
    button_text: str
    footer_text: str = ""
    sections: list[ListSection]


class CheckNumbersRequest(BaseModel):
    numbers: list[str]


class WebhookConfigRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    url: str = Field(..., min_length=8, max_length=2048)
    enabled: bool = True
    authType: WebhookAuthType = "NONE"
    authConfig: dict[str, str] = Field(default_factory=dict)
    customHeaders: dict[str, str] = Field(default_factory=dict)
    eventFilters: dict[str, bool] = Field(default_factory=lambda: {"business": True, "transport": False, "operational": False})


class WebhookEnabledRequest(BaseModel):
    enabled: bool


class WebhookCenterCreateRequest(WebhookConfigRequest):
    connection_id: str = Field(..., min_length=1, max_length=128)


class WebhookCenterPatchRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    enabled: bool | None = None
    authType: WebhookAuthType | None = None
    authConfig: dict[str, str] | None = None
    customHeaders: dict[str, str] | None = None
    eventFilters: dict[str, bool] | None = None


class WebhookTestRequest(BaseModel):
    """Optional JSON override for a manual delivery using the real webhook contract."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] | None = None


class ManualWebhookActionRequest(BaseModel):
    """Optional operator context for a manual delivery action; never request identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    reason: str | None = Field(default=None, max_length=500)
    confirm_current_target: bool = Field(default=False, alias="confirmCurrentTarget")
