import { useEffect, useMemo, useState } from 'react'
import { Copy, FlaskConical, Pencil, Plus, Power, Save, Trash2 } from 'lucide-react'
import type { GatewayConfig } from '../lib/config'
import { api, ApiError } from '../lib/api'
import type { Instance, InstanceWebhook, WebhookAuthType } from '../types'

interface Props {
  config: GatewayConfig
  instances: Instance[]
  onToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

interface FormState {
  url: string
  enabled: boolean
  authType: WebhookAuthType
  token: string
  apiKeyHeader: string
  apiKey: string
  username: string
  password: string
  customHeadersText: string
}

const AUTH_OPTIONS: WebhookAuthType[] = ['NONE', 'BEARER', 'API_KEY', 'BASIC', 'CUSTOM_HEADERS']

function parseHeaders(text: string): Record<string, string> {
  const trimmed = text.trim()
  if (!trimmed) return {}
  const parsed = JSON.parse(trimmed)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Headers JSON invalido')
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(parsed)) {
    const k = String(key || '').trim()
    if (!k) continue
    out[k] = String(value ?? '').trim()
  }
  return out
}

function toForm(item?: InstanceWebhook): FormState {
  const auth = item?.authConfig || {}
  return {
    url: item?.url || '',
    enabled: item?.enabled ?? true,
    authType: item?.authType || 'NONE',
    token: auth.token || '',
    apiKeyHeader: auth.headerName || 'x-api-key',
    apiKey: auth.apiKey || '',
    username: auth.username || '',
    password: auth.password || '',
    customHeadersText: JSON.stringify(item?.customHeaders || {}, null, 2),
  }
}

export default function WebhooksManager({ config, instances, onToast }: Props) {
  const [instanceName, setInstanceName] = useState('')
  const [items, setItems] = useState<InstanceWebhook[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(toForm())

  useEffect(() => {
    if (!instanceName && instances.length > 0) setInstanceName(instances[0].name)
  }, [instances, instanceName])

  const editing = useMemo(() => items.find(item => item.id === editingId) || null, [items, editingId])

  const load = async () => {
    if (!instanceName) return
    setLoading(true)
    try {
      const res = await api.webhooks.listByInstance(config, instanceName)
      setItems(Array.isArray(res.items) ? res.items : [])
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error cargando webhooks', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [instanceName])

  const resetForm = () => {
    setEditingId(null)
    setForm(toForm())
  }

  const onEdit = (item: InstanceWebhook) => {
    setEditingId(item.id)
    setForm(toForm(item))
  }

  const onSubmit = async () => {
    if (!instanceName) return
    try {
      const customHeaders = parseHeaders(form.customHeadersText)
      const authConfig: Record<string, string> = {}
      if (form.authType === 'BEARER') authConfig.token = form.token.trim()
      if (form.authType === 'API_KEY') {
        authConfig.headerName = form.apiKeyHeader.trim() || 'x-api-key'
        authConfig.apiKey = form.apiKey.trim()
      }
      if (form.authType === 'BASIC') {
        authConfig.username = form.username
        authConfig.password = form.password
      }

      setSaving(true)
      if (editingId) {
        await api.webhooks.update(config, instanceName, editingId, {
          url: form.url.trim(),
          enabled: form.enabled,
          authType: form.authType,
          authConfig,
          customHeaders,
        })
        onToast('Webhook actualizado', 'success')
      } else {
        await api.webhooks.create(config, instanceName, {
          url: form.url.trim(),
          enabled: form.enabled,
          authType: form.authType,
          authConfig,
          customHeaders,
        })
        onToast('Webhook creado', 'success')
      }
      await load()
      resetForm()
    } catch (error) {
      onToast(error instanceof Error ? error.message : 'Error guardando webhook', 'error')
    } finally {
      setSaving(false)
    }
  }

  const onDelete = async (item: InstanceWebhook) => {
    if (!instanceName) return
    if (!confirm('Eliminar webhook?')) return
    try {
      await api.webhooks.remove(config, instanceName, item.id)
      onToast('Webhook eliminado', 'success')
      await load()
      if (editingId === item.id) resetForm()
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error eliminando webhook', 'error')
    }
  }

  const onToggle = async (item: InstanceWebhook) => {
    if (!instanceName) return
    try {
      await api.webhooks.toggleEnabled(config, instanceName, item.id, !item.enabled)
      await load()
      onToast(item.enabled ? 'Webhook deshabilitado' : 'Webhook habilitado', 'success')
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error cambiando estado', 'error')
    }
  }

  const onTest = async (item: InstanceWebhook) => {
    if (!instanceName) return
    try {
      const res = await api.webhooks.test(config, instanceName, item.id)
      onToast(res.ok ? `Test OK (${res.status})` : `Test fail (${res.status}) ${res.error || ''}`, res.ok ? 'success' : 'error')
      await load()
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error testeando webhook', 'error')
    }
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
      <div className="xl:col-span-2 border border-zinc-800 bg-zinc-900 rounded-xl overflow-hidden">
        <div className="p-4 border-b border-zinc-800 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-zinc-400">Instancia</span>
            <select
              value={instanceName}
              onChange={e => setInstanceName(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 rounded-md px-2 py-1.5 text-sm"
            >
              {instances.map(inst => <option key={inst.id} value={inst.name}>{inst.name}</option>)}
            </select>
          </div>
          <button onClick={resetForm} className="text-xs text-zinc-300 border border-zinc-700 rounded-md px-2 py-1 flex items-center gap-1"><Plus size={13} />Nuevo</button>
        </div>

        <div className="divide-y divide-zinc-800">
          {loading ? <p className="p-4 text-sm text-zinc-500">Cargando...</p> : items.length === 0 ? <p className="p-4 text-sm text-zinc-500">Sin webhooks</p> : items.map(item => (
            <div key={item.id} className="p-4 flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-zinc-100 truncate">{item.url}</p>
                  <p className="text-xs text-zinc-500">{item.authType} • {item.enabled ? 'enabled' : 'disabled'}</p>
                </div>
                <div className="flex items-center gap-1">
                  <button onClick={() => navigator.clipboard.writeText(item.url)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Copy URL"><Copy size={13} /></button>
                  <button onClick={() => onTest(item)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Test webhook"><FlaskConical size={13} /></button>
                  <button onClick={() => onToggle(item)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Enable/Disable"><Power size={13} /></button>
                  <button onClick={() => onEdit(item)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Editar"><Pencil size={13} /></button>
                  <button onClick={() => onDelete(item)} className="p-1.5 border border-red-900 rounded-md text-red-300" title="Eliminar"><Trash2 size={13} /></button>
                </div>
              </div>
              <div className="text-xs text-zinc-500">
                <span>last status: {item.lastStatus || '-'}</span>
                <span className="mx-2">|</span>
                <span>last error: {item.lastError || '-'}</span>
                <span className="mx-2">|</span>
                <span>last run: {item.lastUsedAt || '-'}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="border border-zinc-800 bg-zinc-900 rounded-xl p-4 flex flex-col gap-3">
        <h3 className="text-sm font-semibold">{editing ? 'Editar webhook' : 'Nuevo webhook'}</h3>
        <input value={form.url} onChange={e => setForm(v => ({ ...v, url: e.target.value }))} placeholder="https://tu-sistema.com/webhook" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
        <label className="flex items-center gap-2 text-sm text-zinc-300"><input type="checkbox" checked={form.enabled} onChange={e => setForm(v => ({ ...v, enabled: e.target.checked }))} /> Enabled</label>
        <select value={form.authType} onChange={e => setForm(v => ({ ...v, authType: e.target.value as WebhookAuthType }))} className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm">
          {AUTH_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
        </select>

        {form.authType === 'BEARER' && <input value={form.token} onChange={e => setForm(v => ({ ...v, token: e.target.value }))} placeholder="Bearer token" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />}
        {form.authType === 'API_KEY' && (
          <>
            <input value={form.apiKeyHeader} onChange={e => setForm(v => ({ ...v, apiKeyHeader: e.target.value }))} placeholder="Header name" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
            <input value={form.apiKey} onChange={e => setForm(v => ({ ...v, apiKey: e.target.value }))} placeholder="API key" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
          </>
        )}
        {form.authType === 'BASIC' && (
          <>
            <input value={form.username} onChange={e => setForm(v => ({ ...v, username: e.target.value }))} placeholder="Username" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
            <input value={form.password} onChange={e => setForm(v => ({ ...v, password: e.target.value }))} placeholder="Password" type="password" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
          </>
        )}

        <textarea value={form.customHeadersText} onChange={e => setForm(v => ({ ...v, customHeadersText: e.target.value }))} rows={6} className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm font-mono" placeholder='{"x-env":"prod"}' />
        <button disabled={saving || !instanceName} onClick={onSubmit} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-md py-2 text-sm font-medium flex items-center justify-center gap-2"><Save size={14} />{saving ? 'Guardando...' : 'Guardar webhook'}</button>
      </div>
    </div>
  )
}
