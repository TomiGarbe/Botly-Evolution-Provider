import type { Channel } from '../channel/model'
import type { Provider } from '../provider/model'

export interface ConnectionCapabilities {
  supportsMessaging: boolean
  supportsWebhook: boolean
  supportsMedia: boolean
  supportsQr: boolean
  supportsReconnect: boolean
  supportsApiKey: boolean
  supportsOfficialApi: boolean
  supportsTemplates: boolean
}

export interface ConnectionStatus {
  state: 'pending' | 'connected' | 'connecting' | 'disconnected'
  lifecycle: string | null
  health: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
}

export interface ConnectionClient {
  id: string
  name: string
}

export interface Connection {
  id: string
  clientId: string
  name: string
  displayName: string | null
  address: string | null
  provider: Provider
  channel: Channel
  status: ConnectionStatus
  capabilities: ConnectionCapabilities
  webhook: { supported: boolean }
  apiKey: { supported: boolean }
  client: ConnectionClient | null
  lastActivityAt: string | null
  createdAt: string | null
  updatedAt: string | null
  runtimeName: string | null
  providerAccount: {
    provider: string
    channelType: string
    providerAccountId: string
    metadata: { username?: string; displayName?: string; accountType?: string }
  } | null
  coreChannel: { channelId: string; name: string | null; configured: boolean } | null
  readiness: ConnectionReadiness | null
}

export interface ConnectionReadiness {
  state: string
  ready: boolean
  configured?: boolean
  authenticated?: boolean
  accountDiscovered?: boolean
  credentialValid?: boolean
  requiredScopesPresent?: boolean
  coreBindingPresent?: boolean
  coreCredentialValid?: boolean
  coreDeliveryReady?: boolean
  missingScopes?: string[]
  tokenExpiry?: string
}

/** Compatibility alias while provider-specific API routes are retired gradually. */
export type InstagramReadiness = ConnectionReadiness

export interface CreateConnectionInput {
  clientId: string
  channel: string
  name: string
  provider: 'meta' | 'evolution'
}
