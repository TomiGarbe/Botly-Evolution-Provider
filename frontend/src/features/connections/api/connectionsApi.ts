import type { Connection, CreateConnectionInput, InstagramReadiness } from '@/domain/connection'
import { gatewayRequest } from '@/shared/lib/gatewayClient'

interface ApiConnection {
  id: string
  client_id: string
  name: string
  display_name: string | null
  address: string | null
  provider: { id: string; display_name: string }
  channel: { id: string; display_name: string; icon?: string | null }
  status: { state: Connection['status']['state']; lifecycle: string | null; health: Connection['status']['health'] }
  capabilities: {
    supports_messaging: boolean
    supports_webhook: boolean
    supports_media: boolean
    supports_qr: boolean
    supports_reconnect: boolean
    supports_api_key: boolean
    supports_official_api: boolean
    supports_templates: boolean
  }
  webhook: { supported: boolean }
  api_key: { supported: boolean }
  client: { id: string; name: string } | null
  last_activity_at: string | null
  created_at: string | null
  updated_at: string | null
  runtime_name: string | null
  provider_account: Connection['providerAccount']
  core_channel: Connection['coreChannel']
  readiness: InstagramReadiness | null
}

export function toConnection(payload: ApiConnection): Connection {
  return {
    id: payload.id,
    clientId: payload.client_id,
    name: payload.name,
    displayName: payload.display_name,
    address: payload.address,
    provider: { id: payload.provider.id, displayName: payload.provider.display_name },
    channel: { id: payload.channel.id, displayName: payload.channel.display_name, icon: payload.channel.icon },
    status: payload.status,
    capabilities: {
      supportsMessaging: payload.capabilities.supports_messaging,
      supportsWebhook: payload.capabilities.supports_webhook,
      supportsMedia: payload.capabilities.supports_media,
      supportsQr: payload.capabilities.supports_qr,
      supportsReconnect: payload.capabilities.supports_reconnect,
      supportsApiKey: payload.capabilities.supports_api_key,
      supportsOfficialApi: payload.capabilities.supports_official_api,
      supportsTemplates: payload.capabilities.supports_templates,
    },
    webhook: payload.webhook,
    apiKey: payload.api_key,
    client: payload.client,
    lastActivityAt: payload.last_activity_at,
    createdAt: payload.created_at,
    updatedAt: payload.updated_at,
    runtimeName: payload.runtime_name,
    providerAccount: payload.provider_account,
    coreChannel: payload.core_channel,
    readiness: payload.readiness,
  }
}

export async function listConnections(clientId?: string): Promise<Connection[]> {
  const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : ''
  const payload = await gatewayRequest<ApiConnection[]>(`/connections${query}`, { cache: 'no-store' })
  return payload.map(toConnection)
}

export async function getConnection(connectionId: string): Promise<Connection> {
  const payload = await gatewayRequest<ApiConnection>(`/connections/${encodeURIComponent(connectionId)}`)
  return toConnection(payload)
}

export async function createConnection(input: CreateConnectionInput): Promise<Connection> {
  const payload = await gatewayRequest<ApiConnection>('/connections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: input.clientId, channel: input.channel, name: input.name, provider: input.provider }),
  })
  return toConnection(payload)
}

export async function getConnectionQr(connectionId: string): Promise<{ qrcode?: { base64?: string; code?: string } }> {
  return gatewayRequest<{ qrcode?: { base64?: string; code?: string } }>(`/connections/${encodeURIComponent(connectionId)}/qr`)
}

export async function updateConnectionName(connectionId: string, name: string): Promise<Connection> {
  const payload = await gatewayRequest<ApiConnection>(`/connections/${encodeURIComponent(connectionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return toConnection(payload)
}

export async function deleteConnection(connectionId: string): Promise<void> {
  await gatewayRequest<void>(`/connections/${encodeURIComponent(connectionId)}`, { method: 'DELETE' })
}

export async function getInstagramReadiness(connectionId: string): Promise<InstagramReadiness> {
  return gatewayRequest<InstagramReadiness>(`/connections/${encodeURIComponent(connectionId)}/instagram/readiness`, { cache: 'no-store' })
}

export async function disconnectInstagram(connectionId: string): Promise<Connection> {
  const payload = await gatewayRequest<ApiConnection>(`/connections/${encodeURIComponent(connectionId)}/instagram/disconnect`, { method: 'POST' })
  return toConnection(payload)
}
