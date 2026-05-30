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
  content?: unknown
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
  interaction?: {
    interactionType?: 'button' | 'list' | 'template' | 'flow' | 'unknown'
    id?: string | null
    title?: string | null
    description?: string | null
    payload?: Record<string, unknown>
    rawSelection?: Record<string, unknown>
  } | null
  context?: {
    quoted?: {
      messageId?: string | null
      sender?: string | null
      chatJid?: string | null
      type?: string | null
      text?: string | null
      mediaType?: string | null
      preview?: string | null
    } | null
    interactiveOrigin?: {
      messageType?: string
    } | null
    targetMessage?: {
      messageId?: string | null
      sender?: string | null
      chatJid?: string | null
    } | null
    targetPollId?: string | null
  } | null
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
    downloadSource?: 'provider-url' | 'decrypted' | 'cache' | string
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
  metadata?: {
    hasLocation?: boolean
    hasContacts?: boolean
    hasPoll?: boolean
    removeReaction?: boolean
    liveLocation?: boolean
    pollOptionCount?: number
    ephemeral?: boolean
    edited?: boolean
    revoked?: boolean
    fromBusiness?: boolean
    newsletter?: boolean
    statusMessage?: boolean
  }
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
  eventFilters?: {
    business?: boolean
    transport?: boolean
    operational?: boolean
  }
  createdAt?: string | null
  updatedAt?: string | null
  lastUsedAt?: string | null
  lastStatus?: string | null
  lastError?: string | null
  lastSuccessAt?: string | null
  lastFailureAt?: string | null
  lastStatusCode?: number | null
  lastLatencyMs?: number | null
  avgLatencyMs?: number | null
  consecutiveFailures?: number
  healthStatus?: 'healthy' | 'degraded' | 'unhealthy'
  unhealthy?: boolean
  successCount?: number
  failureCount?: number
  retryCount?: number
  unhealthyCount?: number
  dispatchHistory?: WebhookDispatchLog[]
}

export interface WebhookDispatchLog {
  timestamp: number
  status: string
  dispatchId?: string
  messageId?: string | null
  eventSubtype?: string | null
  retryCount?: number
  responseCode?: number
  durationMs?: number
  error?: string | null
  webhookUrl?: string
  request?: {
    method?: string
    headers?: Record<string, string>
    payloadSummary?: Record<string, unknown>
    payloadSizeBytes?: number
  }
  response?: {
    headers?: Record<string, string>
    bodyPreview?: string
  }
}
