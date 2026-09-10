import { gatewayRequest } from '@/shared/lib/gatewayClient'

export interface ConnectionWebhook {
  configured: boolean
  enabled: boolean
  url: string | null
  id: string | null
  lastDeliveryAt: string | null
  lastError: string | null
  successfulDeliveries: number
  failedDeliveries: number
  authType: WebhookSecurityType
  authHeaderName: string | null
  queryParamName: string | null
  customHeaderName: string | null
  hasAuthSecret: boolean
}

export type WebhookSecurityType = 'NONE' | 'BEARER' | 'API_KEY' | 'CUSTOM_HEADERS' | 'QUERY_PARAM'

export interface ConnectionWebhookSecurityInput {
  authType: WebhookSecurityType
  authConfig?: Record<string, string>
  customHeaders?: Record<string, string>
}

export interface ConnectionApiKey {
  enabled: boolean
  hasApiKey: boolean
  maskedApiKey: string | null
  createdAt: string | null
  canRevealApiKey: boolean
  apiKey?: string
}

export interface ConnectionActivity {
  id: string
  occurredAt: number
  description: string
  status: string
  severity: string
  technical: Record<string, string>
}

export interface ConnectionStatusSummary {
  connected: boolean
  lastActivityAt: string | null
  lastHeartbeatAt: string | null
}

export interface ConnectionIntegrationEndpoints {
  messageApiUrl: string
  metaWebhookUrl: string
}

export interface WebhookDelivery {
  id: string
  webhookId: string | null
  timestamp: number
  webhookName: string | null
  destinationUrl: string | null
  operation: string | null
  semanticStatus: string | null
  eventType: string | null
  eventId: string | null
  requestId: string | null
  correlationId: string | null
  messageId: string | null
  conversationId: string | null
  isTest: boolean
  status: string
  success: boolean
  statusCode: number
  durationMs: number
  attemptCount: number
  retryCount: number
  error: string | null
  errorType: string | null
  errorDetail: { code?: string | null; category?: string | null; message?: string | null; retryable?: boolean | null } | null
  request: {
    method?: string | null
    url?: string | null
    query?: Record<string, unknown>
    headers?: Record<string, unknown>
    payloadSummary?: {
      event?: string
      type?: string
      subtype?: string
      direction?: 'inbound' | 'outbound' | string
      recipient?: string
      messageType?: string
      textPreview?: string
      mediaKind?: string
      mediaCaption?: string
    }
    payloadPreview?: string
    payloadTruncated?: boolean
  }
  response: { status?: number | null; headers?: Record<string, unknown>; bodyPreview?: string }
  attempts: Array<Record<string, unknown>>
  metadata: Record<string, unknown>
}

export interface WebhookDeliveryMetrics {
  totalDeliveries: number
  successfulDeliveries: number
  failedDeliveries: number
  retries: number
  averageResponseTimeMs: number
}

export interface ConnectionWebhookActivity {
  id: string
  timestamp: number
  direction: 'inbound' | 'outbound'
  source: 'provider' | 'gateway' | string
  destination: 'gateway' | 'core' | 'external_webhook' | string
  eventType: string | null
  status: string
  httpStatus: number | null
  durationMs: number | null
  eventId: string | null
  providerMessageId: string | null
  requestId: string | null
  correlationId: string | null
  error: string | null
  attemptCount: number
}

export interface ConnectionDiagnosticCheck {
  code: string
  label: string
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
  lastVerifiedAt: string | null
  message: string
  action: string | null
}

export interface ConnectionDiagnostics {
  summary: {
    status: ConnectionDiagnosticCheck['status']
    lastVerifiedAt: string | null
    lastHeartbeatAt: string | null
    lastMessageSentAt: number | null
    lastMessageReceivedAt: number | null
    lastWebhookSuccessAt: string | null
    lastError: string | null
  }
  checks: ConnectionDiagnosticCheck[]
  technical: {
    phoneNumberId: string | null
    businessId: string | null
    wabaId: string | null
    provider: string | null
    channel: string | null
    apiVersion: string | null
    lastSynchronizedAt: string | null
  }
}

export interface ConnectionAvailability {
  diagnostic: 'verify_availability'
  provider: string
  available: boolean
  runtimeAvailable: boolean
  deepProviderHealthChecked: boolean
  limitation: string | null
  diagnostics: ConnectionDiagnostics
}

export interface WebhookConfigurationVerification {
  diagnostic: 'verify_webhook_configuration'
  connectivity_checked: false
  configuration_valid: boolean
  webhook: Record<string, unknown>
  checks: Array<{ code: string; ok: boolean; message: string }>
}

interface ApiConnectionApiKey {
  enabled: boolean
  has_api_key: boolean
  masked_api_key: string | null
  created_at: string | null
  can_reveal_api_key: boolean
  api_key?: string
}

function toApiKey(payload: ApiConnectionApiKey): ConnectionApiKey {
  return {
    enabled: payload.enabled,
    hasApiKey: payload.has_api_key,
    maskedApiKey: payload.masked_api_key,
    createdAt: payload.created_at,
    canRevealApiKey: payload.can_reveal_api_key,
    apiKey: payload.api_key,
  }
}

export async function getConnectionWebhook(connectionId: string): Promise<ConnectionWebhook> {
  const payload = await gatewayRequest<{
    configured: boolean
    enabled: boolean
    url: string | null
    id: string | null
    last_delivery_at: string | null
    last_error: string | null
    successful_deliveries: number
    failed_deliveries: number
    auth_type: WebhookSecurityType
    auth_header_name: string | null
    query_param_name: string | null
    custom_header_name: string | null
    has_auth_secret: boolean
  }>(`/connections/${encodeURIComponent(connectionId)}/webhook`)
  return {
    configured: payload.configured,
    enabled: payload.enabled,
    url: payload.url,
    id: payload.id,
    lastDeliveryAt: payload.last_delivery_at,
    lastError: payload.last_error,
    successfulDeliveries: payload.successful_deliveries,
    failedDeliveries: payload.failed_deliveries,
    authType: payload.auth_type,
    authHeaderName: payload.auth_header_name,
    queryParamName: payload.query_param_name,
    customHeaderName: payload.custom_header_name,
    hasAuthSecret: payload.has_auth_secret,
  }
}

export async function verifyConnectionWebhookConfiguration(connectionId: string): Promise<WebhookConfigurationVerification> {
  return gatewayRequest<WebhookConfigurationVerification>(`/connections/${encodeURIComponent(connectionId)}/webhook/configuration`)
}

export async function listConnectionWebhookDeliveries(connectionId: string): Promise<{ items: WebhookDelivery[]; metrics: WebhookDeliveryMetrics }> {
  const payload = await gatewayRequest<{ items: WebhookDelivery[]; metrics: WebhookDeliveryMetrics }>(`/connections/${encodeURIComponent(connectionId)}/webhook/deliveries?limit=100`)
  return { items: Array.isArray(payload.items) ? payload.items : [], metrics: payload.metrics }
}

export async function listConnectionWebhookActivity(connectionId: string): Promise<ConnectionWebhookActivity[]> {
  const payload = await gatewayRequest<{ items: Array<{
    id: string; timestamp: number; direction: 'inbound' | 'outbound'; source: string; destination: string; event_type: string | null; status: string
    http_status: number | null; duration_ms: number | null; event_id: string | null; provider_message_id: string | null
    request_id: string | null; correlation_id: string | null; error: string | null; attempt_count: number
  }> }>(`/connections/${encodeURIComponent(connectionId)}/webhook/activity?limit=100`)
  return (Array.isArray(payload.items) ? payload.items : []).map((item) => ({
    id: item.id, timestamp: item.timestamp, direction: item.direction, source: item.source, destination: item.destination,
    eventType: item.event_type, status: item.status, httpStatus: item.http_status, durationMs: item.duration_ms,
    eventId: item.event_id, providerMessageId: item.provider_message_id, requestId: item.request_id,
    correlationId: item.correlation_id, error: item.error, attemptCount: item.attempt_count,
  }))
}

export async function getConnectionIntegrationEndpoints(connectionId: string): Promise<ConnectionIntegrationEndpoints> {
  const payload = await gatewayRequest<{ message_api_url: string; meta_webhook_url: string }>(`/connections/${encodeURIComponent(connectionId)}/integration-endpoints`)
  return { messageApiUrl: payload.message_api_url, metaWebhookUrl: payload.meta_webhook_url }
}

export async function updateConnectionWebhook(connectionId: string, url: string, security: ConnectionWebhookSecurityInput): Promise<ConnectionWebhook> {
  const payload = await gatewayRequest<{
    configured: boolean
    enabled: boolean
    url: string | null
    id: string | null
    last_delivery_at: string | null
    last_error: string | null
    successful_deliveries: number
    failed_deliveries: number
    auth_type: WebhookSecurityType
    auth_header_name: string | null
    query_param_name: string | null
    custom_header_name: string | null
    has_auth_secret: boolean
  }>(`/connections/${encodeURIComponent(connectionId)}/webhook`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, auth_type: security.authType, auth_config: security.authConfig, custom_headers: security.customHeaders }),
  })
  return {
    configured: payload.configured,
    enabled: payload.enabled,
    url: payload.url,
    id: payload.id,
    lastDeliveryAt: payload.last_delivery_at,
    lastError: payload.last_error,
    successfulDeliveries: payload.successful_deliveries,
    failedDeliveries: payload.failed_deliveries,
    authType: payload.auth_type,
    authHeaderName: payload.auth_header_name,
    queryParamName: payload.query_param_name,
    customHeaderName: payload.custom_header_name,
    hasAuthSecret: payload.has_auth_secret,
  }
}

export async function testConnectionWebhook(connectionId: string): Promise<{ ok: boolean; status: number; error: string | null }> {
  return gatewayRequest(`/connections/${encodeURIComponent(connectionId)}/webhook/test`, { method: 'POST' })
}

export async function getConnectionApiKey(connectionId: string, reveal = false): Promise<ConnectionApiKey> {
  const query = reveal ? '?reveal=true' : ''
  return toApiKey(await gatewayRequest<ApiConnectionApiKey>(`/connections/${encodeURIComponent(connectionId)}/api-key${query}`))
}

export async function regenerateConnectionApiKey(connectionId: string): Promise<ConnectionApiKey> {
  return toApiKey(await gatewayRequest<ApiConnectionApiKey>(`/connections/${encodeURIComponent(connectionId)}/api-key/regenerate`, { method: 'POST' }))
}

export async function reconnectConnection(connectionId: string): Promise<void> {
  await gatewayRequest(`/connections/${encodeURIComponent(connectionId)}/reconnect`, { method: 'POST' })
}

export async function listConnectionActivity(connectionId: string): Promise<ConnectionActivity[]> {
  const payload = await gatewayRequest<{ items: Array<{ id: string; occurred_at: number; description: string; status: string; severity: string; technical: Record<string, string> }> }>(`/connections/${encodeURIComponent(connectionId)}/activity`)
  return payload.items.map((item) => ({
    id: item.id,
    occurredAt: item.occurred_at,
    description: item.description,
    status: item.status,
    severity: item.severity,
    technical: item.technical,
  }))
}

export async function getConnectionStatusSummary(connectionId: string): Promise<ConnectionStatusSummary> {
  const payload = await gatewayRequest<{ connected: boolean; last_activity_at: string | null; last_heartbeat_at: string | null }>(`/connections/${encodeURIComponent(connectionId)}/status`)
  return {
    connected: payload.connected,
    lastActivityAt: payload.last_activity_at,
    lastHeartbeatAt: payload.last_heartbeat_at,
  }
}

export async function getConnectionDiagnostics(connectionId: string): Promise<ConnectionDiagnostics> {
  const payload = await gatewayRequest<{
    summary: { status: ConnectionDiagnosticCheck['status']; last_verified_at: string | null; last_heartbeat_at: string | null; last_message_sent_at: number | null; last_message_received_at: number | null; last_webhook_success_at: string | null; last_error: string | null }
    checks: Array<{ code: string; label: string; status: ConnectionDiagnosticCheck['status']; last_verified_at: string | null; message: string; action: string | null }>
    technical: { phone_number_id: string | null; business_id: string | null; waba_id: string | null; provider: string | null; channel: string | null; api_version: string | null; last_synchronized_at: string | null }
  }>(`/connections/${encodeURIComponent(connectionId)}/diagnostics`)
  return {
    summary: { status: payload.summary.status, lastVerifiedAt: payload.summary.last_verified_at, lastHeartbeatAt: payload.summary.last_heartbeat_at, lastMessageSentAt: payload.summary.last_message_sent_at, lastMessageReceivedAt: payload.summary.last_message_received_at, lastWebhookSuccessAt: payload.summary.last_webhook_success_at, lastError: payload.summary.last_error },
    checks: payload.checks.map((item) => ({ code: item.code, label: item.label, status: item.status, lastVerifiedAt: item.last_verified_at, message: item.message, action: item.action })),
    technical: { phoneNumberId: payload.technical.phone_number_id, businessId: payload.technical.business_id, wabaId: payload.technical.waba_id, provider: payload.technical.provider, channel: payload.technical.channel, apiVersion: payload.technical.api_version, lastSynchronizedAt: payload.technical.last_synchronized_at },
  }
}

export async function getConnectionAvailability(connectionId: string): Promise<ConnectionAvailability> {
  const payload = await gatewayRequest<{
    diagnostic: 'verify_availability'
    provider: string
    available: boolean
    runtime_available: boolean
    deep_provider_health_checked: boolean
    limitation: string | null
    diagnostics: {
      summary: { status: ConnectionDiagnosticCheck['status']; last_verified_at: string | null; last_heartbeat_at: string | null; last_message_sent_at: number | null; last_message_received_at: number | null; last_webhook_success_at: string | null; last_error: string | null }
      checks: Array<{ code: string; label: string; status: ConnectionDiagnosticCheck['status']; last_verified_at: string | null; message: string; action: string | null }>
      technical: { phone_number_id: string | null; business_id: string | null; waba_id: string | null; provider: string | null; channel: string | null; api_version: string | null; last_synchronized_at: string | null }
    }
  }>(`/connections/${encodeURIComponent(connectionId)}/availability`)
  return {
    diagnostic: payload.diagnostic,
    provider: payload.provider,
    available: payload.available,
    runtimeAvailable: payload.runtime_available,
    deepProviderHealthChecked: payload.deep_provider_health_checked,
    limitation: payload.limitation,
    diagnostics: {
      summary: { status: payload.diagnostics.summary.status, lastVerifiedAt: payload.diagnostics.summary.last_verified_at, lastHeartbeatAt: payload.diagnostics.summary.last_heartbeat_at, lastMessageSentAt: payload.diagnostics.summary.last_message_sent_at, lastMessageReceivedAt: payload.diagnostics.summary.last_message_received_at, lastWebhookSuccessAt: payload.diagnostics.summary.last_webhook_success_at, lastError: payload.diagnostics.summary.last_error },
      checks: payload.diagnostics.checks.map((item) => ({ code: item.code, label: item.label, status: item.status, lastVerifiedAt: item.last_verified_at, message: item.message, action: item.action })),
      technical: { phoneNumberId: payload.diagnostics.technical.phone_number_id, businessId: payload.diagnostics.technical.business_id, wabaId: payload.diagnostics.technical.waba_id, provider: payload.diagnostics.technical.provider, channel: payload.diagnostics.technical.channel, apiVersion: payload.diagnostics.technical.api_version, lastSynchronizedAt: payload.diagnostics.technical.last_synchronized_at },
    },
  }
}

export async function sendConnectionQuickMessage(connectionId: string, number: string, text: string): Promise<void> {
  await gatewayRequest(`/connections/${encodeURIComponent(connectionId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ number, text }),
  })
}
