export type ConnectionStatus = 'open' | 'connecting' | 'close'

export interface Instance {
  id: string
  name: string
  status: ConnectionStatus
  profileName?: string
  phone?: string
  avatarUrl?: string
  lastSeen?: string
  createdAt?: string
}

export interface InstanceApiKey {
  instanceId: string
  createdAt?: string | null
  lastUsedAt?: string | null
  enabled: boolean
  hasApiKey: boolean
  maskedApiKey?: string | null
  apiKey?: string | null
}

export interface InstanceState {
  id: string
  name: string
  status: ConnectionStatus
  stale?: boolean
}

export interface QRResponse {
  base64?: string
  code?: string
  count?: number
  fetchedAt?: number
  nextRecommendedRefreshAt?: number
  qrcode?: {
    base64?: string
    code?: string
    count?: number
  } | null
  instance?: Instance
}

export type ToastType = 'success' | 'error' | 'info'

export interface Toast {
  id: string
  message: string
  type: ToastType
}

export interface PipelineEvent {
  id?: string
  layer?: 'business' | 'operational' | 'technical'
  event: string
  instance: string
  timestamp: number
  direction?: 'inbound' | 'outbound' | 'system'
  type?: 'message' | 'delivery' | 'error'
  messageType?: string
  sender?: string
  recipient?: string
  text?: string
  content?: string
  status?: string
  fromMe?: boolean
  fromBot?: boolean
  forwarding?: {
    status?: string
    attempt?: number
  } | null
  error?: {
    type?: string
    message?: string
  } | null
  message?: {
    id?: string
    from?: string
    fromMe?: boolean
    kind?: string
    text?: string
  }
  media?: {
    id: string
    kind?: string
    mimeType?: string
    fileName?: string
    fileSize?: number
    duration?: number
    caption?: string
    isVoiceNote?: boolean
    url?: string
    directPath?: string
  } | null
  meta?: {
    requestId?: string
    conversationId?: string
  }
  pipeline?: {
    stage?: string
    status?: string
    requestId?: string
    conversationId?: string
    messageId?: string
  }
  details?: Record<string, unknown>
}

export type WebhookAuthType = 'NONE' | 'BEARER' | 'API_KEY' | 'BASIC' | 'CUSTOM_HEADERS'

export interface InstanceWebhook {
  id: string
  instanceId: string
  url: string
  enabled: boolean
  authType: WebhookAuthType
  authConfig: Record<string, string>
  customHeaders: Record<string, string>
  createdAt?: string | null
  updatedAt?: string | null
  lastUsedAt?: string | null
  lastStatus?: string | null
  lastError?: string | null
}
