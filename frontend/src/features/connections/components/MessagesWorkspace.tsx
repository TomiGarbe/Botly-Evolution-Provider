import { ArrowDownLeft, ArrowUpRight, Check, CheckCheck, FileText, Image, MessageCircle, Mic, RefreshCw, Search, Send, SlidersHorizontal, Video } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import type { TimelineMessage } from '../api/messagesApi'
import { listConnectionTimelineMessages, sendConnectionText } from '../api/messagesApi'
import { SafeJsonViewer } from '@/features/observability/components/SafeJsonViewer'
import { EmptyState } from '@/shared/components/EmptyState'
import { LoadingState } from '@/shared/components/LoadingState'
import { Toast } from '@/shared/components/Toast'
import { Input, Select, Textarea } from '@/shared/components/FormControls'

type DateRange = 'all' | 'today' | '24h' | '7d' | '30d'
function normalized(value: unknown): string { return String(value || '').trim().toLocaleLowerCase() }
function time(value: number): string { return new Intl.DateTimeFormat('es-AR', { hour: '2-digit', minute: '2-digit' }).format(new Date(value)) }
function dateTime(value: number): string { return new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) }
function typeIcon(kind: string) { if (kind === 'image') return <Image size={16} aria-hidden="true" />; if (kind === 'audio') return <Mic size={16} aria-hidden="true" />; if (kind === 'video') return <Video size={16} aria-hidden="true" />; return <FileText size={16} aria-hidden="true" /> }
function contentPreview(message: TimelineMessage): string { return message.text || message.media?.fileName || (message.media ? `Archivo ${message.kind}` : 'Mensaje sin contenido legible.') }
function statusLabel(status: string | null): string | null { if (!status) return null; return ({ sent: 'Enviado', accepted: 'Aceptado', delivered: 'Entregado', read: 'Leído', failed: 'No enviado', received: 'Recibido' }[status.toLowerCase()] || status) }
function rangeStart(range: DateRange): number | null { const now = Date.now(); if (range === 'today') { const today = new Date(); today.setHours(0, 0, 0, 0); return today.getTime() }; if (range === '24h') return now - 86400000; if (range === '7d') return now - 604800000; if (range === '30d') return now - 2592000000; return null }

function MessageStatus({ status }: { status: string | null }) {
  const label = statusLabel(status)
  if (!label) return <span className="workspace-message-status">Sin estado</span>
  const normalizedStatus = status?.toLowerCase()
  const Icon = normalizedStatus === 'read' || normalizedStatus === 'delivered' ? CheckCheck : Check
  return <span className={`workspace-message-status ${normalizedStatus === 'failed' ? 'is-error' : 'is-success'}`}><Icon size={13} aria-hidden="true" /> {label}</span>
}
function IdentifierList({ message }: { message: TimelineMessage }) {
  const identifiers = [['Message ID', message.messageId], ['Provider message ID', message.providerMessageId], ['Conversation ID', message.conversationId], ['Channel ID', message.channelId], ['Connection ID', message.connectionId], ['Correlation ID', message.correlationId], ['Request ID', message.requestId], ['Event ID', message.eventId], ['Delivery ID', message.deliveryId], ['Outbound attempt ID', message.outboundAttemptId]].filter(([, value]) => Boolean(value)) as Array<[string, string]>
  if (!identifiers.length) return null
  return <section className="workspace-message-detail-section"><h4>Identificadores</h4><dl className="workspace-message-identifiers">{identifiers.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}</dl></section>
}

export function MessagesWorkspace({ connectionId, messageId }: { connectionId: string; messageId?: string | null }) {
  const [messages, setMessages] = useState<TimelineMessage[]>([]); const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null)
  const [search, setSearch] = useState(''); const [filtersOpen, setFiltersOpen] = useState(false); const [direction, setDirection] = useState<'all' | TimelineMessage['direction']>('all'); const [status, setStatus] = useState('all'); const [kind, setKind] = useState('all'); const [dateRange, setDateRange] = useState<DateRange>('all')
  const [recipient, setRecipient] = useState(''); const [text, setText] = useState(''); const [isLoading, setIsLoading] = useState(true); const [isSending, setIsSending] = useState(false); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null)
  const load = useCallback(async (quiet = false) => {
    if (!quiet) setIsLoading(true)
    try { const next = await listConnectionTimelineMessages(connectionId); setMessages(next); setSelectedMessageId((current) => current && next.some((message) => message.id === current) ? current : (messageId ? next.find((message) => message.messageId === messageId)?.id || null : null)) } catch { if (!quiet) setError('No pudimos actualizar los mensajes. Intentá nuevamente.') } finally { if (!quiet) setIsLoading(false) }
  }, [connectionId, messageId])
  useEffect(() => { void load() }, [load])
  useEffect(() => { const interval = window.setInterval(() => { void load(true) }, 5000); return () => window.clearInterval(interval) }, [load])
  const availableStatuses = useMemo(() => [...new Set(messages.map((message) => message.status).filter((value): value is string => Boolean(value)))].sort(), [messages])
  const availableKinds = useMemo(() => [...new Set(messages.map((message) => message.kind).filter(Boolean))].sort(), [messages])
  const visibleMessages = useMemo(() => {
    const query = normalized(search); const start = rangeStart(dateRange)
    return messages.filter((message) => {
      if (messageId && message.messageId !== messageId) return false
      if (direction !== 'all' && message.direction !== direction) return false
      if (status !== 'all' && normalized(message.status) !== normalized(status)) return false
      if (kind !== 'all' && normalized(message.kind) !== normalized(kind)) return false
      if (start !== null && message.timestamp < start) return false
      return !query || [message.text, message.messageId, message.providerMessageId, message.conversationId, message.channelId, message.correlationId, message.requestId, message.eventId, message.deliveryId, message.outboundAttemptId, message.sender, message.recipient, message.provider].some((value) => normalized(value).includes(query))
    })
  }, [dateRange, direction, kind, messageId, messages, search, status])
  const selectedMessage = visibleMessages.find((message) => message.id === selectedMessageId) || null
  const activeFilterCount = [direction !== 'all', status !== 'all', kind !== 'all', dateRange !== 'all'].filter(Boolean).length
  function clearFilters() { setSearch(''); setDirection('all'); setStatus('all'); setKind('all'); setDateRange('all') }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const target = recipient.trim()
    if (!target) return setError('Ingresá el identificador del destinatario.')
    if (!text.trim()) return setError('Escribí un mensaje antes de enviarlo.')
    setError(null); setNotice(null); setIsSending(true)
    try { await sendConnectionText(connectionId, target, text.trim()); setText(''); setNotice('Mensaje enviado.'); await load(true) } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo enviar el mensaje.') } finally { setIsSending(false) }
  }
  return <section className="connection-section workspace-messages">
    <div className="connection-section-heading"><div><h3>Mensajes</h3><p>Un mensaje lógico por envío o recepción; el detalle conserva su evidencia técnica.</p></div><div className="connection-inline-actions workspace-message-actions"><button type="button" className="client-button-secondary" onClick={() => void load()} disabled={isLoading}><RefreshCw size={15} aria-hidden="true" /> Actualizar</button></div></div>
    <Toast message={error} tone="error" onDismiss={() => setError(null)} /><Toast message={notice} tone="success" onDismiss={() => setNotice(null)} />
    <div className="workspace-message-controls"><div className="workspace-message-toolbar"><label className="workspace-message-search"><Search size={17} aria-hidden="true" /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar mensaje, ID o correlación…" aria-label="Buscar mensajes" /></label><button type="button" className={`client-button-secondary workspace-message-filter-toggle ${filtersOpen ? 'is-active' : ''}`} onClick={() => setFiltersOpen((value) => !value)} aria-expanded={filtersOpen}><SlidersHorizontal size={15} aria-hidden="true" /> Filtros{activeFilterCount ? ` · ${activeFilterCount}` : ''}</button></div>{filtersOpen ? <section className="workspace-message-filter-panel" aria-label="Filtros de mensajes"><div className="workspace-message-filter-fields"><label><span>Dirección</span><Select value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}><option value="all">Todas</option><option value="inbound">Entrantes</option><option value="outbound">Salientes</option></Select></label><label><span>Estado</span><Select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">Todos</option>{availableStatuses.map((value) => <option key={value} value={value}>{statusLabel(value) || value}</option>)}</Select></label><label><span>Tipo</span><Select value={kind} onChange={(event) => setKind(event.target.value)}><option value="all">Todos</option>{availableKinds.map((value) => <option key={value} value={value}>{value}</option>)}</Select></label><label><span>Fecha</span><Select value={dateRange} onChange={(event) => setDateRange(event.target.value as DateRange)}><option value="all">Todas</option><option value="today">Hoy</option><option value="24h">Últimas 24 horas</option><option value="7d">Últimos 7 días</option><option value="30d">Últimos 30 días</option></Select></label></div><button type="button" className="workspace-message-clear-filters" onClick={clearFilters}>Limpiar filtros</button></section> : null}</div>
    <div className="workspace-message-layout"><section className="workspace-message-list" aria-label="Lista de mensajes" aria-live="polite">{isLoading ? <LoadingState label="Cargando mensajes…" lines={4} /> : null}{!isLoading && visibleMessages.length === 0 ? <EmptyState icon={MessageCircle} title="No encontramos mensajes." description={activeFilterCount || search ? 'Probá ajustar o limpiar los filtros.' : 'Enviá un mensaje de prueba para verificar esta conexión.'} /> : null}{!isLoading ? <ol>{visibleMessages.map((message) => <li key={message.id}><button type="button" className={`workspace-message-row ${selectedMessage?.id === message.id ? 'is-selected' : ''}`} onClick={() => setSelectedMessageId(message.id)} aria-pressed={selectedMessage?.id === message.id}><span className={`workspace-message-direction workspace-message-direction-${message.direction}`}>{message.direction === 'inbound' ? <ArrowDownLeft size={16} aria-hidden="true" /> : <ArrowUpRight size={16} aria-hidden="true" />}{message.direction === 'inbound' ? 'Recibido' : 'Enviado'}</span><span className="workspace-message-row-main"><strong>{contentPreview(message)}</strong><span>{message.kind}</span></span><span className="workspace-message-row-meta"><time dateTime={new Date(message.timestamp).toISOString()}>{time(message.timestamp)}</time><MessageStatus status={message.status} /></span></button></li>)}</ol> : null}</section>
      <section className="workspace-message-detail" aria-label="Detalle de mensaje">{!selectedMessage ? <EmptyState icon={MessageCircle} title="Seleccioná un mensaje" description="Vas a ver su contenido, estado, identificadores y payload estructurado." /> : <><div className="workspace-message-detail-heading"><div><span className={`workspace-message-direction workspace-message-direction-${selectedMessage.direction}`}>{selectedMessage.direction === 'inbound' ? <ArrowDownLeft size={16} aria-hidden="true" /> : <ArrowUpRight size={16} aria-hidden="true" />}{selectedMessage.direction === 'inbound' ? 'Entrante' : 'Saliente'}</span><h4>{selectedMessage.kind}</h4><time dateTime={new Date(selectedMessage.timestamp).toISOString()}>{dateTime(selectedMessage.timestamp)}</time></div><MessageStatus status={selectedMessage.status} /></div><section className="workspace-message-detail-section"><h4>Contenido</h4>{selectedMessage.media ? <div className="workspace-media-summary">{typeIcon(selectedMessage.kind)}<span>{selectedMessage.media.fileName || `Archivo ${selectedMessage.kind}`}</span></div> : null}<p>{contentPreview(selectedMessage)}</p></section><section className="workspace-message-detail-section"><h4>Datos del mensaje</h4><dl className="workspace-message-identifiers"><div><dt>Dirección</dt><dd>{selectedMessage.direction === 'inbound' ? 'Entrante' : 'Saliente'}</dd></div><div><dt>Tipo</dt><dd>{selectedMessage.kind}</dd></div>{selectedMessage.sender ? <div><dt>Remitente</dt><dd>{selectedMessage.sender}</dd></div> : null}{selectedMessage.recipient ? <div><dt>Destinatario</dt><dd>{selectedMessage.recipient}</dd></div> : null}</dl></section><IdentifierList message={selectedMessage} /><section className="workspace-message-detail-section"><h4>Payload</h4><SafeJsonViewer value={selectedMessage.payload} emptyLabel="No hay payload disponible para este mensaje." /></section></>}</section></div>
    <form className="workspace-composer" onSubmit={submit}><label><span>Destinatario</span><Input value={recipient} onChange={(event) => setRecipient(event.target.value)} placeholder="Identificador del destinatario" disabled={isSending} required /></label><label><span>Mensaje</span><Textarea value={text} onChange={(event) => setText(event.target.value)} rows={3} maxLength={4096} placeholder="Escribí un mensaje…" disabled={isSending} /></label><div className="workspace-composer-actions"><button className="client-button-primary" type="submit" disabled={isSending}><Send size={15} aria-hidden="true" /> {isSending ? 'Enviando…' : 'Enviar'}</button></div></form>
  </section>
}
