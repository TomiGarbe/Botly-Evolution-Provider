import { CheckCircle2, CircleAlert, Clock3, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { EmptyState } from '@/shared/components/EmptyState'
import { LoadingState } from '@/shared/components/LoadingState'
import { listInstagramInboundDeliveries, type InstagramInboundDelivery } from '../api/connectionsApi'

function dateTime(value: number | null): string {
  if (!value) return 'Sin registro'
  return new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function delivered(status: string): boolean { return status === 'delivered' }

/** Connection-scoped evidence for Meta -> Gateway -> canonical outbox -> Core. */
export function InstagramInboundActivity({ connectionId }: { connectionId: string }) {
  const [items, setItems] = useState<InstagramInboundDelivery[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true); else setLoading(true)
    setError(null)
    try { setItems(await listInstagramInboundDeliveries(connectionId)) }
    catch { setError('No se pudo cargar la traza inbound de Instagram.') }
    finally { setLoading(false); setRefreshing(false) }
  }, [connectionId])

  useEffect(() => { void load() }, [load])

  return <section className="connection-section instagram-inbound-activity">
    <div className="connection-section-heading"><div><h3>Inbound hacia Botly Core</h3><p>Traza durable: Meta → Gateway → CanonicalInboundEvent → Outbox → Core. No se muestran credenciales ni payloads crudos.</p></div><button type="button" className="client-button-secondary" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} /> Actualizar</button></div>
    {loading ? <LoadingState label="Cargando entregas inbound…" lines={3} /> : null}
    {error ? <p className="client-form-error" role="alert">{error}</p> : null}
    {!loading && !error && !items.length ? <EmptyState icon={Clock3} title="Todavía no hay eventos inbound." description="Después de la prueba manual, cada evento recibido y su entrega hacia Core aparecerán aquí." /> : null}
    {!loading && !error && items.length ? <div className="webhook-activity-list"><ol>{items.map((item) => <li key={item.id}><article className="webhook-activity-row is-selected"><div className="webhook-activity-row-main"><strong>{item.text || item.kind || item.event_type || 'Evento inbound'}</strong><span>{item.sender_external_id || 'Remitente no disponible'} · {dateTime(item.created_at)}</span></div><div className="webhook-activity-row-meta"><span className={delivered(item.status) ? 'is-success' : 'is-error'}>{delivered(item.status) ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}{item.status}</span><span>{item.attempt_count} intento{item.attempt_count === 1 ? '' : 's'}</span></div><dl className="workspace-message-identifiers"><div><dt>Provider message ID</dt><dd><code>{item.provider_message_id || 'No disponible'}</code></dd></div><div><dt>Event ID</dt><dd><code>{item.event_id || 'No disponible'}</code></dd></div><div><dt>Adjuntos</dt><dd>{item.attachments.length ? item.attachments.map((attachment) => attachment.fileName || attachment.kind || 'Archivo').join(', ') : 'Sin adjuntos'}</dd></div><div><dt>Request / correlation</dt><dd><code>{item.request_id || '—'} / {item.correlation_id || '—'}</code></dd></div><div><dt>Entrega Core</dt><dd>{item.delivered_at ? `Entregada ${dateTime(item.delivered_at)}` : item.last_error || 'Pendiente de entrega'}</dd></div></dl></article></li>)}</ol></div> : null}
  </section>
}
