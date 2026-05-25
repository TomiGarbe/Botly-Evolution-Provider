from typing import Any, Literal
from pydantic import BaseModel, Field


class CreateInstanceRequest(BaseModel):
    instance_name: str = Field(..., pattern=r"^[a-z0-9_]{1,64}$")
    qrcode: bool = True
    token: str | None = None
    # Configura el webhook automáticamente al crear la instancia
    auto_configure_webhook: bool = True


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


WebhookAuthType = Literal["NONE", "BEARER", "API_KEY", "BASIC", "CUSTOM_HEADERS"]


class WebhookConfigRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    enabled: bool = True
    authType: WebhookAuthType = "NONE"
    authConfig: dict[str, str] = Field(default_factory=dict)
    customHeaders: dict[str, str] = Field(default_factory=dict)
    eventFilters: dict[str, bool] = Field(default_factory=lambda: {"business": True, "transport": False, "operational": False})


class WebhookEnabledRequest(BaseModel):
    enabled: bool
