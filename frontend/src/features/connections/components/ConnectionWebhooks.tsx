import { CheckCircle2, CircleAlert, FlaskConical, Pencil, Plus, Power, Trash2, Webhook } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { Connection } from '@/domain/connection'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { LoadingState } from '@/shared/components/LoadingState'
import { Toast } from '@/shared/components/Toast'
import { createWebhook, deleteWebhook, listWebhooks, setWebhookEnabled, testWebhook, updateWebhook, type WebhookInput, type WebhookRecord, type WebhookTestResult } from '@/features/webhooks/api/webhooksApi'
import { WebhookForm } from '@/features/webhooks/components/WebhookForm'
import { ConnectionWebhookActivity } from './ConnectionWebhookActivity'
import { WebhookTestPanel } from './WebhookTestPanel'

function dateTime(value: string | null): string {
  return value ? new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'Sin actividad'
}

function eventLabel(filters: Record<string, boolean>): string {
  const labels = { business: 'Negocio', transport: 'Transporte', operational: 'Operativo' }
  const active = Object.entries(filters).filter(([, enabled]) => enabled).map(([key]) => labels[key as keyof typeof labels] || key)
  return active.length ? active.join(' · ') : 'Sin eventos'
}

function authLabel(webhook: WebhookRecord): string {
  return webhook.authType === 'NONE' ? 'Sin autenticación' : `${webhook.authType} · configurada`
}

function healthLabel(webhook: WebhookRecord): string {
  if (!webhook.enabled) return 'Inactivo'
  return ({ healthy: 'Saludable', degraded: 'Atención requerida', unhealthy: 'Con error' } as Record<string, string>)[webhook.healthStatus] || webhook.healthStatus
}

export function ConnectionWebhooks({ connection }: { connection: Connection }) {
  const [items, setItems] = useState<WebhookRecord[]>([])
  const [editing, setEditing] = useState<WebhookRecord | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [webhookToTest, setWebhookToTest] = useState<WebhookRecord | null>(null)
  const [webhookToDelete, setWebhookToDelete] = useState<WebhookRecord | null>(null)
  const [testResult, setTestResult] = useState<{ webhookId: string; result: WebhookTestResult } | null>(null)
  const [timelineRefresh, setTimelineRefresh] = useState(0)

  const supported = connection.capabilities.supportsWebhook
  const load = useCallback(async () => {
    if (!supported) { setItems([]); setIsLoading(false); return }
    setError(null); setIsLoading(true)
    try { setItems(await listWebhooks(connection.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudieron cargar los webhooks de esta conexión.') }
    finally { setIsLoading(false) }
  }, [connection.id, supported])
  useEffect(() => { void load() }, [load])

  async function save(input: Omit<WebhookInput, 'connectionId'> & { connectionId?: string }) {
    setRunning('save'); setError(null)
    try {
      if (editing) await updateWebhook(editing.id, input)
      else await createWebhook({ ...input, connectionId: connection.id } as WebhookInput)
      setEditing(null); setIsCreating(false); setNotice(editing ? 'Webhook actualizado.' : 'Webhook creado.'); await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'No se pudo guardar el webhook.')
    } finally { setRunning(null) }
  }

  async function toggle(webhook: WebhookRecord) {
    setRunning(`toggle-${webhook.id}`); setError(null)
    try { await setWebhookEnabled(webhook.id, !webhook.enabled); setNotice(webhook.enabled ? 'Webhook desactivado.' : 'Webhook activado.'); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo actualizar el webhook.') }
    finally { setRunning(null) }
  }

  async function runTest(webhook: WebhookRecord, payload: Record<string, unknown>) {
    setRunning(`test-${webhook.id}`); setError(null)
    try {
      const result = await testWebhook(webhook.id, payload)
      setTestResult({ webhookId: webhook.id, result })
      setTimelineRefresh((current) => current + 1)
      setNotice(result.ok ? 'Prueba enviada correctamente. La actividad fue actualizada.' : 'La prueba terminó con un error. Revisá la actividad registrada.')
      setWebhookToTest(null)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'No se pudo ejecutar la prueba.')
    } finally { setRunning(null) }
  }

  async function remove() {
    if (!webhookToDelete) return
    const webhook = webhookToDelete
    setRunning(`delete-${webhook.id}`); setError(null)
    try { await deleteWebhook(webhook.id); setEditing(null); setNotice('Webhook eliminado.'); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo eliminar el webhook.') }
    finally { setRunning(null); setWebhookToDelete(null) }
  }

  if (!supported) return <section className="connection-section workspace-webhooks"><div className="connection-section-heading"><div><h3>Webhooks</h3><p>Esta conexión no admite la configuración de webhooks.</p></div></div><EmptyState icon={Webhook} title="Webhooks no disponibles" description="El canal o proveedor actual no expone esta capacidad." /></section>

  return <section className="connection-section workspace-webhooks">
    <div className="connection-section-heading"><div><h3>Webhooks</h3><p>Destinos configurados para esta conexión.</p></div><div className="connection-inline-actions"><button type="button" className="client-button-primary" onClick={() => { setEditing(null); setIsCreating(true) }} disabled={isLoading}><Plus size={15} aria-hidden="true" /> Crear webhook</button></div></div>
    <Toast message={error} tone="error" onDismiss={() => setError(null)} /><Toast message={notice} onDismiss={() => setNotice(null)} />
    {isCreating || editing ? <WebhookForm key={editing?.id || 'create'} webhook={editing || undefined} connections={[connection]} connectionId={connection.id} isSubmitting={running === 'save'} onCancel={() => { setIsCreating(false); setEditing(null) }} onSubmit={save} /> : null}
    {isLoading ? <LoadingState label="Cargando webhooks…" lines={3} /> : null}
    {!isLoading && error ? <div className="clients-state clients-state-error" role="alert"><p>{error}</p><button type="button" onClick={() => void load()}>Reintentar</button></div> : null}
    {!isLoading && !error && items.length === 0 && !isCreating ? <EmptyState icon={Webhook} title="No hay webhooks configurados." description="Creá un destino para comenzar a recibir entregas de esta conexión." action={<button type="button" className="client-button-primary" onClick={() => setIsCreating(true)}><Plus size={15} /> Crear webhook</button>} /> : null}
    {!isLoading && !error && items.length > 0 ? <div className="connection-webhooks-list">{items.map((webhook) => <article key={webhook.id} className="connection-webhook-card"><div className="connection-webhook-heading"><div><div><h4>{webhook.name}</h4><span className={webhook.enabled ? 'webhook-enabled is-active' : 'webhook-enabled'}>{webhook.enabled ? 'Activo' : 'Inactivo'}</span></div><p>{healthLabel(webhook)}</p></div><div className="connection-webhook-actions"><button type="button" className="client-button-secondary" onClick={() => { setIsCreating(false); setEditing(webhook) }}><Pencil size={15} aria-hidden="true" /> Editar</button><button type="button" className="client-button-secondary" onClick={() => void toggle(webhook)} disabled={running === `toggle-${webhook.id}`}><Power size={15} aria-hidden="true" /> {webhook.enabled ? 'Desactivar' : 'Activar'}</button></div></div><dl className="connection-webhook-summary"><div><dt>Endpoint</dt><dd><code>{webhook.url}</code></dd></div><div><dt>Autenticación</dt><dd>{authLabel(webhook)}</dd></div><div><dt>Eventos</dt><dd>{eventLabel(webhook.eventFilters)}</dd></div><div><dt>Última entrega</dt><dd>{dateTime(webhook.lastUsedAt)}</dd></div></dl><div className="connection-webhook-card-actions"><button type="button" className="client-button-primary" disabled={!webhook.enabled || running === `test-${webhook.id}`} onClick={() => setWebhookToTest(webhook)}><FlaskConical size={15} aria-hidden="true" /> Probar webhook</button><button type="button" className="client-button-danger" disabled={running === `delete-${webhook.id}`} onClick={() => setWebhookToDelete(webhook)}><Trash2 size={15} aria-hidden="true" /> Eliminar</button></div>{testResult?.webhookId === webhook.id ? <div className={`connection-webhook-test-result ${testResult.result.ok ? 'is-success' : 'is-error'}`} role="status">{testResult.result.ok ? <CheckCircle2 size={18} aria-hidden="true" /> : <CircleAlert size={18} aria-hidden="true" />}<span>{testResult.result.ok ? 'Entrega de prueba exitosa' : 'La entrega de prueba falló'}{testResult.result.status ? ` · HTTP ${testResult.result.status}` : ''}{testResult.result.latencyMs !== null ? ` · ${Math.round(testResult.result.latencyMs)} ms` : ''}{testResult.result.error ? ` · ${testResult.result.error}` : ''}</span></div> : null}</article>)}</div> : null}
    {webhookToTest ? <WebhookTestPanel webhooks={items} selected={webhookToTest} isSubmitting={running === `test-${webhookToTest.id}`} onSelect={setWebhookToTest} onCancel={() => setWebhookToTest(null)} onSubmit={runTest} /> : null}
    <ConfirmDialog isOpen={Boolean(webhookToDelete)} title="¿Eliminar este webhook?" description="Dejará de recibir nuevas entregas. El historial técnico se conserva según la retención configurada." confirmLabel="Eliminar webhook" submittingLabel="Eliminando…" isSubmitting={running === `delete-${webhookToDelete?.id}`} onCancel={() => setWebhookToDelete(null)} onConfirm={() => void remove()} />
    <ConnectionWebhookActivity connectionId={connection.id} refreshToken={timelineRefresh} />
  </section>
}
