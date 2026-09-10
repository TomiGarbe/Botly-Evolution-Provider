import { CircleAlert, Clock3, RefreshCw, Search } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { EmptyState } from '@/shared/components/EmptyState'
import { Input } from '@/shared/components/FormControls'
import { LoadingState } from '@/shared/components/LoadingState'
import { listConnectionWebhookActivity, type ConnectionWebhookActivity as Activity } from '../api/connectionOperationsApi'

function dateTime(value: number): string { return new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) }
function route(item: Activity): string { return `${item.source === 'provider' ? 'Provider' : 'Gateway'} → ${item.destination === 'external_webhook' ? 'Webhook externo' : item.destination === 'core' ? 'Botly Core' : 'Gateway'}` }
function state(item: Activity): string {
  return ({ normalized: 'Normalizado', queued: 'En cola', dispatching: 'Enviando', delivered_to_core: 'Core aceptó HTTP 2xx', delivered_to_external_webhook: 'Entregado al webhook', retrying: 'Reintentando', dead_letter: 'Sin más reintentos', failed: 'Fallido' } as Record<string, string>)[item.status] || item.status
}

export function ConnectionWebhookActivity({ connectionId, refreshToken = 0 }: { connectionId: string; refreshToken?: number }) {
  const [items, setItems] = useState<Activity[]>([]); const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true); const [refreshing, setRefreshing] = useState(false); const [error, setError] = useState<string | null>(null); const [search, setSearch] = useState('')
  const load = useCallback(async (quiet = false) => {
    quiet ? setRefreshing(true) : setLoading(true); setError(null)
    try { const next = await listConnectionWebhookActivity(connectionId); setItems(next); setSelectedId((current) => current && next.some((item) => item.id === current) ? current : null) }
    catch { setError('No se pudo cargar la actividad de transporte.') }
    finally { setLoading(false); setRefreshing(false) }
  }, [connectionId])
  useEffect(() => { void load() }, [load]); useEffect(() => { if (refreshToken) void load(true) }, [load, refreshToken])
  const filtered = useMemo(() => { const query = search.trim().toLowerCase(); return query ? items.filter((item) => [route(item), item.eventType, item.status, item.eventId, item.providerMessageId, item.requestId, item.correlationId].filter(Boolean).join(' ').toLowerCase().includes(query)) : items }, [items, search])
  const selected = items.find((item) => item.id === selectedId) || null
  return <section className="connection-section webhook-activity">
    <div className="connection-section-heading"><div><h3>Actividad de transporte</h3><p>Eventos entre provider, Gateway, Core y webhooks externos. No incluye payloads ni credenciales.</p></div><button type="button" className="client-button-secondary" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw size={15} className={refreshing ? 'is-spinning' : undefined} /> Actualizar</button></div>
    <label className="webhook-activity-search"><Search size={16} /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar evento o identificador" aria-label="Buscar actividad de transporte" /></label>
    {loading ? <LoadingState label="Cargando actividad de transporte…" lines={3} /> : null}
    {!loading && error ? <div className="clients-state clients-state-error" role="alert"><p>{error}</p><button onClick={() => void load()}>Reintentar</button></div> : null}
    {!loading && !error && !items.length ? <EmptyState icon={Clock3} title="Todavía no hay actividad." description="Los eventos de transporte aparecerán aquí cuando Gateway los registre." /> : null}
    {!loading && !error && items.length ? <div className="webhook-activity-layout"><ol className="webhook-activity-list">{filtered.map((item) => <li key={item.id}><button type="button" className={`webhook-activity-row${selectedId === item.id ? ' is-selected' : ''}`} onClick={() => setSelectedId(item.id)}><div className="webhook-activity-row-main"><strong>{route(item)}</strong><span>{item.eventType || 'Evento de transporte'}</span></div><time>{dateTime(item.timestamp)}</time><div className="webhook-activity-row-meta"><span className={item.status.includes('failed') || item.status === 'dead_letter' ? 'is-error' : 'is-success'}>{state(item)}</span>{item.httpStatus ? <span>HTTP {item.httpStatus}</span> : null}</div></button></li>)}</ol><aside className="webhook-activity-detail">{!selected ? <EmptyState icon={Clock3} title="Seleccioná un evento." description="Elegí una fila para ver sus identificadores seguros." /> : <><div className="webhook-activity-detail-heading"><div><h4>{route(selected)}</h4><time>{dateTime(selected.timestamp)}</time></div>{selected.error ? <CircleAlert className="is-error" /> : null}</div><dl className="workspace-message-identifiers"><div><dt>Estado</dt><dd>{state(selected)}</dd></div><div><dt>Evento</dt><dd>{selected.eventType || 'No disponible'}</dd></div>{selected.httpStatus ? <div><dt>HTTP</dt><dd>{selected.httpStatus}</dd></div> : null}{selected.durationMs ? <div><dt>Duración</dt><dd>{Math.round(selected.durationMs)} ms</dd></div> : null}<div><dt>Event ID</dt><dd><code>{selected.eventId || '—'}</code></dd></div><div><dt>Provider message ID</dt><dd><code>{selected.providerMessageId || '—'}</code></dd></div><div><dt>Request / correlation</dt><dd><code>{selected.requestId || '—'} / {selected.correlationId || '—'}</code></dd></div>{selected.attemptCount ? <div><dt>Intentos</dt><dd>{selected.attemptCount}</dd></div> : null}</dl>{selected.error ? <p className="client-form-error">{selected.error}</p> : null}</>}</aside></div> : null}
  </section>
}
