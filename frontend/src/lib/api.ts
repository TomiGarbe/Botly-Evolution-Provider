import type { GatewayConfig } from './config'
import type { Instance, InstanceApiKey, InstanceState, QRResponse, InstanceWebhook, WebhookAuthType, WebhookDispatchLog } from '../types'

const DEFAULT_TIMEOUT_MS = 10000
const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504])

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

const VALID_STATUS = new Set(['open', 'connecting', 'close'])

function normalizeStatus(raw: unknown): Instance['status'] {
  const value = String(raw ?? '').toLowerCase()
  return VALID_STATUS.has(value) ? (value as Instance['status']) : 'close'
}

function isSafeInstanceName(value: unknown): value is string {
  return typeof value === 'string' && /^[a-z0-9_]{1,64}$/.test(value)
}

function normalizeInstance(raw: unknown): Instance | null {
  if (!raw || typeof raw !== 'object') return null
  const data = raw as Record<string, unknown>
  const name = data.name
  if (!isSafeInstanceName(name)) {
    console.warn('Dropping invalid instance payload from backend', raw)
    return null
  }

  const idValue = typeof data.id === 'string' && data.id.trim() ? data.id.trim() : name
  return {
    id: idValue,
    name,
    status: normalizeStatus(data.status),
    profileName: typeof data.profileName === 'string' ? data.profileName : undefined,
    phone: typeof data.phone === 'string' ? data.phone : undefined,
    avatarUrl: typeof data.avatarUrl === 'string' ? data.avatarUrl : undefined,
    lastSeen: typeof data.lastSeen === 'string' ? data.lastSeen : undefined,
    createdAt: typeof data.createdAt === 'string' ? data.createdAt : undefined,
  }
}

function assertInstanceName(name: string): void {
  if (!isSafeInstanceName(name)) {
    throw new ApiError(400, `Nombre de instancia invalido: "${name}"`)
  }
}

async function request<T>(
  cfg: GatewayConfig,
  method: string,
  path: string,
  body?: unknown,
  options?: { retries?: number; timeoutMs?: number }
): Promise<T> {
  const retries = options?.retries ?? (method === 'GET' ? 1 : 0)
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs)

    try {
      const res = await fetch(`${cfg.url.replace(/\/$/, '')}${path}`, {
        method,
        headers: {
          'X-API-Key': cfg.apiKey,
          'Content-Type': 'application/json',
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      })

      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const err = new ApiError(res.status, data?.detail ?? `HTTP ${res.status}`)
        if (attempt < retries && RETRYABLE_STATUS.has(res.status)) {
          await new Promise(resolve => window.setTimeout(resolve, 300 * (attempt + 1)))
          continue
        }
        throw err
      }

      return data as T
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === 'AbortError'
      if (attempt < retries && (isAbort || !(error instanceof ApiError))) {
        await new Promise(resolve => window.setTimeout(resolve, 300 * (attempt + 1)))
        continue
      }

      if (isAbort) {
        throw new ApiError(504, 'Timeout del gateway')
      }
      throw error
    } finally {
      window.clearTimeout(timeout)
    }
  }

  throw new ApiError(500, 'Request failed')
}

export const api = {
  instances: {
    list: async (cfg: GatewayConfig) => {
      const raw = await request<unknown[]>(cfg, 'GET', '/instances/')
      return (Array.isArray(raw) ? raw : [])
        .map(normalizeInstance)
        .filter((item): item is Instance => item !== null)
    },

    create: async (cfg: GatewayConfig, instanceName: string) => {
      assertInstanceName(instanceName)
      const raw = await request<unknown>(cfg, 'POST', '/instances/', {
        instance_name: instanceName,
        qrcode: true,
        auto_configure_webhook: true,
      })
      const parsed = normalizeInstance(raw)
      if (!parsed) throw new ApiError(502, 'Respuesta invalida al crear instancia')
      return parsed
    },

    state: async (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      const raw = await request<InstanceState>(cfg, 'GET', `/instances/${name}/state`)
      return { ...raw, status: normalizeStatus(raw.status) }
    },

    qr: (cfg: GatewayConfig, name: string, refresh = false) => {
      assertInstanceName(name)
      return request<QRResponse>(cfg, 'GET', `/instances/${name}/qr${refresh ? '?refresh=1' : ''}`)
    },

    reconnect: (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      return request<unknown>(cfg, 'POST', `/instances/${name}/reconnect`)
    },

    logout: (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      return request<unknown>(cfg, 'DELETE', `/instances/${name}/logout`)
    },

    delete: (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      return request<unknown>(cfg, 'DELETE', `/instances/${name}`)
    },
    getApiKey: (cfg: GatewayConfig, name: string, reveal = false) => {
      assertInstanceName(name)
      return request<InstanceApiKey>(cfg, 'GET', `/instances/${name}/api-key${reveal ? '?reveal=1' : ''}`)
    },
    regenerateApiKey: (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      return request<InstanceApiKey>(cfg, 'POST', `/instances/${name}/api-key/regenerate`)
    },
    revokeApiKey: (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      return request<InstanceApiKey>(cfg, 'DELETE', `/instances/${name}/api-key`)
    },
    enableApiKey: (cfg: GatewayConfig, name: string) => {
      assertInstanceName(name)
      return request<InstanceApiKey>(cfg, 'POST', `/instances/${name}/api-key/enable`)
    },
  },

  health: (cfg: GatewayConfig) =>
    request<{ status: string }>(cfg, 'GET', '/health', undefined, { retries: 0, timeoutMs: 5000 }),

  ready: (cfg: GatewayConfig) =>
    request<{ status: string; instances: number }>(cfg, 'GET', '/ready', undefined, { retries: 0, timeoutMs: 7000 }),

  webhooks: {
    events: <T = unknown>(cfg: GatewayConfig, instance?: string, limit = 250) =>
      request<{ items: T[] }>(
        cfg,
        'GET',
        `/webhooks/events?limit=${limit}${instance ? `&instance=${encodeURIComponent(instance)}` : ''}`
      ),
    listByInstance: (cfg: GatewayConfig, instanceName: string) => {
      assertInstanceName(instanceName)
      return request<{ items: InstanceWebhook[] }>(cfg, 'GET', `/instances/${instanceName}/webhooks`)
    },
    create: (
      cfg: GatewayConfig,
      instanceName: string,
      body: { url: string; enabled: boolean; authType: WebhookAuthType; authConfig: Record<string, string>; customHeaders: Record<string, string> }
    ) => {
      assertInstanceName(instanceName)
      return request<InstanceWebhook>(cfg, 'POST', `/instances/${instanceName}/webhooks`, body)
    },
    update: (
      cfg: GatewayConfig,
      instanceName: string,
      webhookId: string,
      body: { url: string; enabled: boolean; authType: WebhookAuthType; authConfig: Record<string, string>; customHeaders: Record<string, string> }
    ) => {
      assertInstanceName(instanceName)
      return request<InstanceWebhook>(cfg, 'PUT', `/instances/${instanceName}/webhooks/${encodeURIComponent(webhookId)}`, body)
    },
    toggleEnabled: (cfg: GatewayConfig, instanceName: string, webhookId: string, enabled: boolean) => {
      assertInstanceName(instanceName)
      return request<InstanceWebhook>(cfg, 'PATCH', `/instances/${instanceName}/webhooks/${encodeURIComponent(webhookId)}/enabled`, { enabled })
    },
    remove: (cfg: GatewayConfig, instanceName: string, webhookId: string) => {
      assertInstanceName(instanceName)
      return request<{ ok: boolean }>(cfg, 'DELETE', `/instances/${instanceName}/webhooks/${encodeURIComponent(webhookId)}`)
    },
    test: (cfg: GatewayConfig, instanceName: string, webhookId: string) => {
      assertInstanceName(instanceName)
      return request<{ ok: boolean; status: number; error?: string }>(cfg, 'POST', `/instances/${instanceName}/webhooks/${encodeURIComponent(webhookId)}/test`)
    },
    dispatches: (cfg: GatewayConfig, instanceName: string, webhookId: string, limit = 20) => {
      assertInstanceName(instanceName)
      return request<{ items: WebhookDispatchLog[] }>(
        cfg,
        'GET',
        `/instances/${instanceName}/webhooks/${encodeURIComponent(webhookId)}/dispatches?limit=${Math.max(1, Math.min(limit, 100))}`
      )
    },
    diagnose: (cfg: GatewayConfig, instanceName: string, webhookId: string) => {
      assertInstanceName(instanceName)
      return request<Record<string, unknown>>(cfg, 'POST', `/instances/${instanceName}/webhooks/${encodeURIComponent(webhookId)}/diagnose`)
    },
  },

  messages: {
    send: (
      cfg: GatewayConfig,
      instanceName: string,
      body: { number: string; type: 'text' | 'image' | 'audio' | 'video' | 'document' | 'file' | 'pdf'; text?: string; caption?: string; mediaUrl?: string; base64?: string; metadata?: Record<string, unknown> }
    ) => {
      assertInstanceName(instanceName)
      return request(cfg, 'POST', `/messages/${instanceName}`, body)
    },

    sendMultipart: (
      cfg: GatewayConfig,
      instanceName: string,
      body: { number: string; type: 'image' | 'video' | 'audio' | 'document' | 'pdf' | 'file'; caption?: string; text?: string },
      file: File,
      options?: { onProgress?: (percent: number) => void; signal?: AbortSignal }
    ) => {
      assertInstanceName(instanceName)
      return new Promise<unknown>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        const form = new FormData()
        form.append('number', body.number)
        form.append('type', body.type)
        if (body.caption) form.append('caption', body.caption)
        if (body.text) form.append('text', body.text)
        form.append('file', file)

        xhr.open('POST', `${cfg.url.replace(/\/$/, '')}/messages/${instanceName}`)
        xhr.setRequestHeader('X-API-Key', cfg.apiKey)

        xhr.upload.onprogress = event => {
          if (!event.lengthComputable || !options?.onProgress) return
          const percent = Math.max(0, Math.min(100, Math.round((event.loaded / event.total) * 100)))
          options.onProgress(percent)
        }

        xhr.onload = () => {
          let payload: unknown = null
          try {
            payload = JSON.parse(xhr.responseText || '{}')
          } catch {
            payload = {}
          }

          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(payload)
            return
          }

          const data = (payload && typeof payload === 'object' ? payload : {}) as { detail?: string }
          reject(new ApiError(xhr.status || 500, data.detail ?? `HTTP ${xhr.status || 500}`))
        }

        xhr.onerror = () => reject(new ApiError(502, 'Error enviando archivo'))
        xhr.onabort = () => reject(new ApiError(499, 'Carga cancelada'))

        if (options?.signal) {
          const onAbort = () => xhr.abort()
          if (options.signal.aborted) {
            onAbort()
            return
          }
          options.signal.addEventListener('abort', onAbort, { once: true })
        }

        xhr.send(form)
      })
    },
  },
}
