import { useEffect, useMemo, useState } from 'react'
import { Copy, FlaskConical, Pencil, Plus, Power, Save, Trash2 } from 'lucide-react'
import type { GatewayConfig } from '../lib/config'
import { api, ApiError } from '../lib/api'
import type { Instance, InstanceWebhook, PipelineEvent, WebhookAuthType, WebhookDeliveryMetrics, WebhookDispatchLog } from '../types'

interface Props {
  config: GatewayConfig
  instances: Instance[]
  onToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

interface FormState {
  name: string
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
const OUTCOME_OPTIONS: Array<'all' | 'success' | 'failed'> = ['all', 'success', 'failed']

function authString(value: string | boolean | undefined, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

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
    name: item?.name || '',
    url: item?.url || '',
    enabled: item?.enabled ?? true,
    authType: item?.authType || 'NONE',
    token: '',
    apiKeyHeader: authString(auth.headerName, 'x-api-key'),
    apiKey: '',
    username: authString(auth.username),
    password: '',
    customHeadersText: JSON.stringify(item?.customHeaders || {}, null, 2),
  }
}

function hasStoredSecret(item: InstanceWebhook | null, key: 'token' | 'apiKey' | 'password'): boolean {
  const auth = item?.authConfig || {}
  const marker = `has${key.charAt(0).toUpperCase()}${key.slice(1)}`
  return Boolean(auth[marker] || auth[key])
}

function formatNumber(value: number | undefined | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '0'
}

function formatDuration(value: number | undefined | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return `${value.toFixed(value >= 100 ? 0 : 2)} ms`
}

function statusTone(success: boolean | undefined): string {
  if (success) return 'text-emerald-300 border-emerald-900/60 bg-emerald-950/30'
  return 'text-red-300 border-red-900/60 bg-red-950/30'
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
      <p className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-zinc-100">{value}</p>
    </div>
  )
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="mt-1 whitespace-pre-wrap break-all rounded-md bg-zinc-950/80 p-2 text-[11px] text-zinc-300">{JSON.stringify(value, null, 2)}</pre>
}

function DispatchEntry({ row }: { row: WebhookDispatchLog }) {
  return (
    <div className="rounded-md border border-zinc-800 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-full border px-2 py-0.5 ${statusTone(row.success)}`}>{row.success ? 'success' : 'failed'}</span>
        <span className="text-zinc-300">{new Date(row.timestamp).toLocaleString()}</span>
        <span className="text-zinc-500">code={row.statusCode ?? row.responseCode ?? '-'}</span>
        <span className="text-zinc-500">attempts={row.attemptCount ?? row.attempts?.length ?? 1}</span>
        <span className="text-zinc-500">retries={row.retryCount ?? 0}</span>
        <span className="text-zinc-500">{formatDuration(row.durationMs)}</span>
      </div>
      <div className="mt-2 text-xs text-zinc-400">
        <p>webhook={row.webhookName || '-'} | event={row.eventType || row.eventSubtype || '-'} | message={row.messageId || '-'} | conversation={row.conversationId || '-'}</p>
        <p>dispatch={row.dispatchId || '-'} | errorType={row.errorType || '-'} | destination={row.destinationUrl || '-'}</p>
        {row.error && <p className="mt-1 text-red-300">error: {row.error}</p>}
      </div>
      <details className="mt-2 text-xs text-zinc-400">
        <summary className="cursor-pointer">Payload enviado</summary>
        <div className="mt-2">
          <p className="text-zinc-500">Resumen</p>
          <JsonBlock value={row.request?.payloadSummary || {}} />
          <p className="mt-2 text-zinc-500">Payload truncado</p>
          <pre className="mt-1 whitespace-pre-wrap break-all rounded-md bg-zinc-950/80 p-2 text-[11px] text-zinc-300">{row.request?.payloadPreview || '-'}</pre>
          {row.request?.payloadTruncated ? <p className="mt-1 text-[11px] text-amber-300">El payload completo fue truncado para observabilidad.</p> : null}
        </div>
      </details>
      <details className="mt-2 text-xs text-zinc-400">
        <summary className="cursor-pointer">Respuesta recibida</summary>
        <div className="mt-2">
          <p className="text-zinc-500">Headers</p>
          <JsonBlock value={row.response?.headers || {}} />
          <p className="mt-2 text-zinc-500">Body resumido</p>
          <pre className="mt-1 whitespace-pre-wrap break-all rounded-md bg-zinc-950/80 p-2 text-[11px] text-zinc-300">{row.response?.bodyPreview || '-'}</pre>
        </div>
      </details>
      {Array.isArray(row.attempts) && row.attempts.length > 0 ? (
        <details className="mt-2 text-xs text-zinc-400">
          <summary className="cursor-pointer">Intentos ({row.attempts.length})</summary>
          <div className="mt-2 space-y-2">
            {row.attempts.map(attempt => (
              <div key={`${row.dispatchId || row.timestamp}-${attempt.attempt}`} className="rounded-md border border-zinc-800 bg-zinc-950/40 p-2">
                <p>
                  attempt={attempt.attempt} | {attempt.success ? 'success' : 'failed'} | code={attempt.statusCode ?? '-'} | {formatDuration(attempt.durationMs)}
                </p>
                <p>errorType={attempt.errorType || '-'} | error={attempt.error || '-'}</p>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  )
}

export default function WebhooksManager({ config, instances, onToast }: Props) {
  const [instanceName, setInstanceName] = useState('')
  const [items, setItems] = useState<InstanceWebhook[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(toForm())
  const [dispatchLogs, setDispatchLogs] = useState<Record<string, WebhookDispatchLog[]>>({})
  const [recentDeliveries, setRecentDeliveries] = useState<WebhookDispatchLog[]>([])
  const [deliveryMetrics, setDeliveryMetrics] = useState<WebhookDeliveryMetrics | null>(null)
  const [outcomeFilter, setOutcomeFilter] = useState<'all' | 'success' | 'failed'>('all')
  const [diagResult, setDiagResult] = useState<Record<string, unknown> | null>(null)
  const [evolutionAuthEvents, setEvolutionAuthEvents] = useState<PipelineEvent[]>([])

  useEffect(() => {
    if (!instanceName && instances.length > 0) setInstanceName(instances[0].name)
  }, [instances, instanceName])

  const editing = useMemo(() => items.find(item => item.id === editingId) || null, [items, editingId])

  const loadOverview = async (selectedOutcome: 'all' | 'success' | 'failed' = outcomeFilter) => {
    if (!instanceName) return
    try {
      const res = await api.webhooks.deliveries(config, instanceName, 30, selectedOutcome)
      setRecentDeliveries(Array.isArray(res.items) ? res.items : [])
      setDeliveryMetrics(res.metrics || null)
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error cargando observabilidad de webhooks', 'error')
    }
  }

  const load = async () => {
    if (!instanceName) return
    setLoading(true)
    try {
      const [webhooksRes, deliveriesRes] = await Promise.all([
        api.webhooks.listByInstance(config, instanceName),
        api.webhooks.deliveries(config, instanceName, 30, outcomeFilter),
      ])
      setItems(Array.isArray(webhooksRes.items) ? webhooksRes.items : [])
      setRecentDeliveries(Array.isArray(deliveriesRes.items) ? deliveriesRes.items : [])
      setDeliveryMetrics(deliveriesRes.metrics || null)
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error cargando webhooks', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [instanceName])

  useEffect(() => {
    void loadOverview(outcomeFilter)
  }, [outcomeFilter])

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
      if (form.authType === 'BEARER') {
        const token = form.token.trim()
        if (!editingId && !token) throw new Error('Bearer token es obligatorio.')
        if (!token && !hasStoredSecret(editing, 'token')) throw new Error('Bearer token es obligatorio.')
        if (token) authConfig.token = token
      }
      if (form.authType === 'API_KEY') {
        authConfig.headerName = form.apiKeyHeader.trim() || 'x-api-key'
        const apiKey = form.apiKey.trim()
        if (!editingId && !apiKey) throw new Error('API key es obligatoria.')
        if (!apiKey && !hasStoredSecret(editing, 'apiKey')) throw new Error('API key es obligatoria.')
        if (apiKey) authConfig.apiKey = apiKey
      }
      if (form.authType === 'BASIC') {
        authConfig.username = form.username
        const password = form.password.trim()
        if (!form.username.trim()) throw new Error('Username es obligatorio.')
        if (!editingId && !password) throw new Error('Password es obligatorio.')
        if (!password && !hasStoredSecret(editing, 'password')) throw new Error('Password es obligatorio.')
        if (password) authConfig.password = password
      }

      setSaving(true)
      const body = {
        name: form.name.trim() || undefined,
        url: form.url.trim(),
        enabled: form.enabled,
        authType: form.authType,
        authConfig,
        customHeaders,
      }
      if (editingId) {
        await api.webhooks.update(config, instanceName, editingId, body)
        onToast('Webhook actualizado', 'success')
      } else {
        await api.webhooks.create(config, instanceName, body)
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
      const dispatches = await api.webhooks.dispatches(config, instanceName, item.id, 20)
      setDispatchLogs(prev => ({ ...prev, [item.id]: dispatches.items || [] }))
      await load()
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error testeando webhook', 'error')
    }
  }

  const onLoadDispatches = async (item: InstanceWebhook) => {
    if (!instanceName) return
    try {
      const res = await api.webhooks.dispatches(config, instanceName, item.id, 20)
      setDispatchLogs(prev => ({ ...prev, [item.id]: Array.isArray(res.items) ? res.items : [] }))
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error cargando dispatches', 'error')
    }
  }

  const onDiagnose = async (item: InstanceWebhook) => {
    if (!instanceName) return
    try {
      const res = await api.webhooks.diagnose(config, instanceName, item.id)
      setDiagResult(res)
      onToast('Diagnostico ejecutado', 'info')
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error ejecutando diagnostico', 'error')
    }
  }

  const onLoadEvolutionAuth = async () => {
    try {
      const res = await api.webhooks.events<PipelineEvent>(config, instanceName || undefined, 200)
      const entries = Array.isArray(res.items) ? res.items : []
      const filtered = entries.filter(event => event.layer === 'operational' && event.pipeline?.stage === 'evolution_auth')
      setEvolutionAuthEvents(filtered.slice(0, 40))
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'Error cargando auth evolution', 'error')
    }
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
      <div className="xl:col-span-2 border border-zinc-800 bg-zinc-900 rounded-xl overflow-hidden">
        <div className="p-4 border-b border-zinc-800 bg-zinc-950/60">
          <p className="text-xs text-zinc-300">Internal Evolution Webhook: Evolution -&gt; Gateway /webhooks/evolution (auth por EVOLUTION_API_KEY).</p>
          <p className="text-xs text-zinc-400 mt-1">External Bot Webhooks: Gateway -&gt; Bot URL (auth por authType de cada webhook).</p>
        </div>

        <div className="p-4 border-b border-zinc-800 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-zinc-400">Instancia</span>
            <select
              value={instanceName}
              onChange={e => setInstanceName(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 rounded-md px-2 py-1.5 text-sm min-w-0"
            >
              {instances.map(inst => <option key={inst.id} value={inst.name}>{inst.name}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <select value={outcomeFilter} onChange={e => setOutcomeFilter(e.target.value as 'all' | 'success' | 'failed')} className="bg-zinc-950 border border-zinc-800 rounded-md px-2 py-1.5 text-xs">
              {OUTCOME_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
            </select>
            <button onClick={resetForm} className="text-xs text-zinc-300 border border-zinc-700 rounded-md px-2 py-1 flex items-center gap-1"><Plus size={13} />Nuevo</button>
          </div>
        </div>

        <div className="p-4 border-b border-zinc-800">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <StatCard label="Total deliveries" value={formatNumber(deliveryMetrics?.totalDeliveries)} />
            <StatCard label="Successful" value={formatNumber(deliveryMetrics?.successfulDeliveries)} />
            <StatCard label="Failed" value={formatNumber(deliveryMetrics?.failedDeliveries)} />
            <StatCard label="Retries" value={formatNumber(deliveryMetrics?.retries)} />
            <StatCard label="Avg response" value={formatDuration(deliveryMetrics?.averageResponseTimeMs)} />
          </div>
          <details className="mt-4 text-xs text-zinc-300 border border-zinc-800 rounded-md p-3">
            <summary className="cursor-pointer">Ultimos envios ({recentDeliveries.length})</summary>
            <div className="mt-3 space-y-3">
              {recentDeliveries.length === 0 ? <p className="text-zinc-500">Sin deliveries registrados.</p> : recentDeliveries.map(row => <DispatchEntry key={`${row.dispatchId || row.timestamp}-${row.webhookId || 'hook'}`} row={row} />)}
            </div>
          </details>
        </div>

        <div className="p-4 border-b border-zinc-800">
          <button onClick={onLoadEvolutionAuth} className="text-xs text-zinc-300 border border-zinc-700 rounded-md px-2 py-1">
            Cargar Evolution Auth Events
          </button>
          {evolutionAuthEvents.length > 0 ? (
            <details className="mt-3 text-xs text-zinc-300 border border-zinc-800 rounded-md p-2">
              <summary className="cursor-pointer">Internal Evolution Events ({evolutionAuthEvents.length})</summary>
              <div className="mt-2 space-y-2">
                {evolutionAuthEvents.map(item => (
                  <div key={item.id || String(item.timestamp)} className="border border-zinc-800 rounded-md p-2">
                    <p>{new Date(item.timestamp).toLocaleString()} | instance={item.instance || '-'} | status={item.pipeline?.status || '-'}</p>
                    <p>source={String((item.details || {}).source || '-')} expected={String((item.details || {}).expectedGlobalPrefix || '-')} received={String((item.details || {}).receivedPrefix || '-')}</p>
                    <p>event={item.event || '-'} mode={String((item.details || {}).acceptedMode || '-')}</p>
                  </div>
                ))}
              </div>
            </details>
          ) : null}
        </div>

        <div className="divide-y divide-zinc-800">
          {loading ? <p className="p-4 text-sm text-zinc-500">Cargando...</p> : items.length === 0 ? <p className="p-4 text-sm text-zinc-500">Sin webhooks</p> : items.map(item => (
            <div key={item.id} className="p-4 flex flex-col gap-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-zinc-100 truncate">{item.name || item.url}</p>
                  <p className="text-xs text-zinc-500 truncate">{item.url}</p>
                  <p className="text-xs text-zinc-500 mt-1">{item.authType} | {item.enabled ? 'enabled' : 'disabled'} | health={item.healthStatus || 'healthy'}</p>
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  <button onClick={() => navigator.clipboard.writeText(item.url)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Copy URL"><Copy size={13} /></button>
                  <button onClick={() => onTest(item)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Test webhook"><FlaskConical size={13} /></button>
                  <button onClick={() => onLoadDispatches(item)} className="px-2 py-1.5 border border-zinc-700 rounded-md text-zinc-300 text-xs">Dispatches</button>
                  <button onClick={() => onDiagnose(item)} className="px-2 py-1.5 border border-zinc-700 rounded-md text-zinc-300 text-xs">Diagnose</button>
                  <button onClick={() => onToggle(item)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Enable/Disable"><Power size={13} /></button>
                  <button onClick={() => onEdit(item)} className="p-1.5 border border-zinc-700 rounded-md text-zinc-300" title="Editar"><Pencil size={13} /></button>
                  <button onClick={() => onDelete(item)} className="p-1.5 border border-red-900 rounded-md text-red-300" title="Eliminar"><Trash2 size={13} /></button>
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
                <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-2">ok: {formatNumber(item.successCount)}</div>
                <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-2">failed: {formatNumber(item.failureCount)}</div>
                <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-2">retries: {formatNumber(item.retryCount)}</div>
                <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-2">last: {formatDuration(item.lastLatencyMs)}</div>
                <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-2">avg: {formatDuration(item.avgLatencyMs)}</div>
              </div>
              <div className="text-xs text-zinc-500">
                <span>last status: {item.lastStatus || '-'}</span>
                <span className="mx-2">|</span>
                <span>last code: {item.lastStatusCode ?? '-'}</span>
                <span className="mx-2">|</span>
                <span>last error: {item.lastError || '-'}</span>
                <span className="mx-2">|</span>
                <span>last run: {item.lastUsedAt || '-'}</span>
                <span className="mx-2">|</span>
                <span>filters: b={String(item.eventFilters?.business ?? true)} t={String(item.eventFilters?.transport ?? false)} o={String(item.eventFilters?.operational ?? false)}</span>
              </div>
              {(dispatchLogs[item.id] || []).length > 0 ? (
                <details className="text-xs text-zinc-400 bg-zinc-950/60 border border-zinc-800 rounded-md p-3">
                  <summary className="cursor-pointer">Ultimos dispatches ({dispatchLogs[item.id].length})</summary>
                  <div className="mt-3 space-y-3">
                    {(dispatchLogs[item.id] || []).map(row => (
                      <DispatchEntry key={`${row.dispatchId || row.timestamp}-${item.id}`} row={{ ...row, webhookId: row.webhookId || item.id, webhookName: row.webhookName || item.name || item.url }} />
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="border border-zinc-800 bg-zinc-900 rounded-xl p-4 flex flex-col gap-3 min-w-0">
        <h3 className="text-sm font-semibold">{editing ? 'Editar webhook' : 'Nuevo webhook'}</h3>
        <input value={form.name} onChange={e => setForm(v => ({ ...v, name: e.target.value }))} placeholder="Nombre visible del webhook" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
        <input value={form.url} onChange={e => setForm(v => ({ ...v, url: e.target.value }))} placeholder="https://tu-sistema.com/webhook" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
        <label className="flex items-center gap-2 text-sm text-zinc-300"><input type="checkbox" checked={form.enabled} onChange={e => setForm(v => ({ ...v, enabled: e.target.checked }))} /> Enabled</label>
        <select value={form.authType} onChange={e => setForm(v => ({ ...v, authType: e.target.value as WebhookAuthType }))} className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm">
          {AUTH_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
        </select>

        {form.authType === 'BEARER' ? <input value={form.token} onChange={e => setForm(v => ({ ...v, token: e.target.value }))} placeholder={editing ? 'Dejar vacio para conservar el token actual' : 'Bearer token'} type="password" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" /> : null}
        {form.authType === 'API_KEY' ? (
          <>
            <input value={form.apiKeyHeader} onChange={e => setForm(v => ({ ...v, apiKeyHeader: e.target.value }))} placeholder="Header name" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
            <input value={form.apiKey} onChange={e => setForm(v => ({ ...v, apiKey: e.target.value }))} placeholder={editing ? 'Dejar vacio para conservar la API key actual' : 'API key'} type="password" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
          </>
        ) : null}
        {form.authType === 'BASIC' ? (
          <>
            <input value={form.username} onChange={e => setForm(v => ({ ...v, username: e.target.value }))} placeholder="Username" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
            <input value={form.password} onChange={e => setForm(v => ({ ...v, password: e.target.value }))} placeholder={editing ? 'Dejar vacio para conservar el password actual' : 'Password'} type="password" className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm" />
          </>
        ) : null}

        <textarea value={form.customHeadersText} onChange={e => setForm(v => ({ ...v, customHeadersText: e.target.value }))} rows={6} className="bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 text-sm font-mono" placeholder='{"x-env":"prod"}' />
        <button disabled={saving || !instanceName} onClick={onSubmit} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-md py-2 text-sm font-medium flex items-center justify-center gap-2"><Save size={14} />{saving ? 'Guardando...' : 'Guardar webhook'}</button>
        {diagResult ? (
          <details className="text-xs text-zinc-300 border border-zinc-800 rounded-md p-2">
            <summary className="cursor-pointer">Diagnostico red (gateway -&gt; bot)</summary>
            <pre className="mt-2 whitespace-pre-wrap">{JSON.stringify(diagResult, null, 2)}</pre>
          </details>
        ) : null}
      </div>
    </div>
  )
}
