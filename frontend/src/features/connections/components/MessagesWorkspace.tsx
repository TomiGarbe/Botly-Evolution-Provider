import { ArrowDownLeft, ArrowUpRight, Check, CheckCheck, FileText, Image, MessageCircle, Mic, RefreshCw, Search, Send, SlidersHorizontal, Video, X } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import type { MessageKind, TimelineMessage } from '../api/messagesApi'
import { listInstagramTimelineMessages, listTimelineMessages, sendInstagramWorkspaceMessage, sendWorkspaceMessage } from '../api/messagesApi'
import { SafeJsonViewer } from '@/features/observability/components/SafeJsonViewer'
import { EmptyState } from '@/shared/components/EmptyState'
import { LoadingState } from '@/shared/components/LoadingState'
import { Toast } from '@/shared/components/Toast'
import { Input, Select, Textarea } from '@/shared/components/FormControls'

const MAX_UPLOAD_BYTES = 25 * 1024 * 1024
type DateRange = 'all' | 'today' | '24h' | '7d' | '30d' | 'custom'
type AdvancedFilters = Pick<TimelineMessage, 'providerMessageId' | 'conversationId' | 'channelId' | 'correlationId' | 'requestId' | 'eventId' | 'deliveryId'>
type AdvancedFilterKey = keyof AdvancedFilters

const types: Array<{ value: MessageKind; label: string; accept: string }> = [
  { value: 'image', label: 'Imagen', accept: 'image/*' },
  { value: 'audio', label: 'Audio', accept: 'audio/*' },
  { value: 'document', label: 'Documento', accept: '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip' },
  { value: 'video', label: 'Video', accept: 'video/*' },
]

const advancedFilterFields: Array<{ key: AdvancedFilterKey; label: string; placeholder: string }> = [
  { key: 'providerMessageId', label: 'Provider Message ID', placeholder: 'ID del proveedor' },
  { key: 'conversationId', label: 'Conversation ID', placeholder: 'ID de conversación' },
  { key: 'channelId', label: 'Channel ID', placeholder: 'ID de canal' },
  { key: 'correlationId', label: 'Correlation ID', placeholder: 'ID de correlación' },
  { key: 'requestId', label: 'Request ID', placeholder: 'ID de solicitud' },
  { key: 'eventId', label: 'Event ID', placeholder: 'ID de evento' },
  { key: 'deliveryId', label: 'Delivery ID', placeholder: 'ID de entrega' },
]

const emptyAdvancedFilters: AdvancedFilters = {
  providerMessageId: null, conversationId: null, channelId: null, correlationId: null, requestId: null, eventId: null, deliveryId: null,
}

function phone(value: string | null): string {
  return String(value || '').replace(/\D/g, '')
}

function time(value: number): string {
  return new Intl.DateTimeFormat('es-AR', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function dateTime(value: number): string {
  return new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function typeIcon(kind: string) {
  if (kind === 'image') return <Image size={16} aria-hidden="true" />
  if (kind === 'audio') return <Mic size={16} aria-hidden="true" />
  if (kind === 'video') return <Video size={16} aria-hidden="true" />
  return <FileText size={16} aria-hidden="true" />
}

function statusLabel(status: string | null): string | null {
  if (!status) return null
  const labels: Record<string, string> = { sent: 'Enviado', accepted: 'Aceptado', delivered: 'Entregado', read: 'Leído', failed: 'No enviado', received: 'Recibido' }
  return labels[status.toLowerCase()] || status
}

function MessageStatus({ status }: { status: string | null }) {
  const label = statusLabel(status)
  if (!label) return <span className="workspace-message-status">Sin estado</span>
  const normalized = status?.toLowerCase()
  const Icon = normalized === 'read' || normalized === 'delivered' ? CheckCheck : Check
  return <span className={`workspace-message-status ${normalized === 'failed' ? 'is-error' : 'is-success'}`}><Icon size={13} aria-hidden="true" /> {label}</span>
}

function contentPreview(message: TimelineMessage): string {
  if (message.text) return message.text
  if (message.media?.fileName) return message.media.fileName
  return message.media ? `Archivo ${message.kind}` : 'Mensaje sin contenido legible.'
}

function normalized(value: unknown): string {
  return String(value || '').trim().toLocaleLowerCase()
}

function includes(value: unknown, query: string): boolean {
  return normalized(value).includes(query)
}

function rangeStart(range: DateRange, customFrom: string): number | null {
  const now = Date.now()
  if (range === 'today') { const today = new Date(); today.setHours(0, 0, 0, 0); return today.getTime() }
  if (range === '24h') return now - 24 * 60 * 60 * 1000
  if (range === '7d') return now - 7 * 24 * 60 * 60 * 1000
  if (range === '30d') return now - 30 * 24 * 60 * 60 * 1000
  if (range === 'custom' && customFrom) return new Date(`${customFrom}T00:00:00`).getTime()
  return null
}

function rangeEnd(range: DateRange, customTo: string): number | null {
  if (range !== 'custom' || !customTo) return null
  return new Date(`${customTo}T23:59:59.999`).getTime()
}

function IdentifierList({ message }: { message: TimelineMessage }) {
  const identifiers = [
    ['Message ID', message.messageId], ['Provider message ID', message.providerMessageId], ['Conversation ID', message.conversationId],
    ['Channel ID', message.channelId], ['Connection ID', message.connectionId], ['Correlation ID', message.correlationId],
    ['Request ID', message.requestId], ['Event ID', message.eventId], ['Delivery ID', message.deliveryId], ['Outbound attempt ID', message.outboundAttemptId],
  ].filter(([, value]) => Boolean(value)) as Array<[string, string]>
  if (!identifiers.length) return null
  return <section className="workspace-message-detail-section"><h4>Identificadores</h4><dl className="workspace-message-identifiers">{identifiers.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}</dl></section>
}

export function MessagesWorkspace({ runtimeName, connectionId, messageId, instagram = false }: { runtimeName: string | null; connectionId?: string; messageId?: string | null; instagram?: boolean }) {
  const [messages, setMessages] = useState<TimelineMessage[]>([])
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [direction, setDirection] = useState<'all' | TimelineMessage['direction']>('all')
  const [status, setStatus] = useState('all')
  const [kind, setKind] = useState('all')
  const [dateRange, setDateRange] = useState<DateRange>('all')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [advancedFilters, setAdvancedFilters] = useState<AdvancedFilters>(emptyAdvancedFilters)
  const [number, setNumber] = useState('')
  const [text, setText] = useState('')
  const [attachmentType, setAttachmentType] = useState<MessageKind | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [caption, setCaption] = useState('')
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(Boolean(runtimeName || (instagram && connectionId)))
  const [isSending, setIsSending] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async (quiet = false) => {
    if (!runtimeName && !(instagram && connectionId)) return
    if (!quiet) setIsLoading(true)
    try {
      const next = instagram && connectionId ? await listInstagramTimelineMessages(connectionId) : await listTimelineMessages(runtimeName as string)
      setMessages(next)
      setSelectedMessageId((current) => current && next.some((message) => message.id === current) ? current : (messageId ? next.find((message) => message.messageId === messageId)?.id || null : null))
    } catch {
      if (!quiet) setError('No pudimos actualizar los mensajes. Intentá nuevamente.')
    } finally {
      if (!quiet) setIsLoading(false)
    }
  }, [connectionId, instagram, messageId, runtimeName])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!runtimeName && !(instagram && connectionId)) return
    const interval = window.setInterval(() => { void load(true) }, 5000)
    return () => window.clearInterval(interval)
  }, [connectionId, instagram, load, runtimeName])
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])

  const availableStatuses = useMemo(() => [...new Set(messages.map((message) => message.status).filter((value): value is string => Boolean(value)))].sort(), [messages])
  const availableKinds = useMemo(() => [...new Set(messages.map((message) => message.kind).filter(Boolean))].sort(), [messages])
  const activeFilters = useMemo(() => {
    const filters: Array<{ key: string; label: string }> = []
    if (search.trim()) filters.push({ key: 'search', label: `Búsqueda: ${search.trim()}` })
    if (direction !== 'all') filters.push({ key: 'direction', label: direction === 'inbound' ? 'Entrantes' : 'Salientes' })
    if (status !== 'all') filters.push({ key: 'status', label: `Estado: ${statusLabel(status) || status}` })
    if (kind !== 'all') filters.push({ key: 'kind', label: `Tipo: ${kind}` })
    if (dateRange !== 'all') filters.push({ key: 'dateRange', label: `Fecha: ${{ today: 'Hoy', '24h': 'Últimas 24 horas', '7d': 'Últimos 7 días', '30d': 'Últimos 30 días', custom: 'Personalizado' }[dateRange]}` })
    for (const { key, label } of advancedFilterFields) if (advancedFilters[key]) filters.push({ key, label: `${label}: ${advancedFilters[key]}` })
    return filters
  }, [advancedFilters, dateRange, direction, kind, search, status])
  const activeFilterCount = activeFilters.length
  const visibleMessages = useMemo(() => {
    const query = normalized(search)
    const start = rangeStart(dateRange, customFrom)
    const end = rangeEnd(dateRange, customTo)
    return messages.filter((message) => {
      if (messageId && message.messageId !== messageId) return false
      if (direction !== 'all' && message.direction !== direction) return false
      if (status !== 'all' && normalized(message.status) !== normalized(status)) return false
      if (kind !== 'all' && normalized(message.kind) !== normalized(kind)) return false
      if (start !== null && message.timestamp < start) return false
      if (end !== null && message.timestamp > end) return false
      if (query && ![
        message.text, message.messageId, message.providerMessageId, message.conversationId, message.channelId, message.correlationId,
        message.requestId, message.eventId, message.deliveryId, message.outboundAttemptId, message.sender, message.recipient, message.provider,
      ].some((value) => includes(value, query))) return false
      return advancedFilterFields.every(({ key }) => !advancedFilters[key] || includes(message[key], normalized(advancedFilters[key])))
    })
  }, [advancedFilters, customFrom, customTo, dateRange, direction, kind, messageId, messages, search, status])
  const selectedMessage = visibleMessages.find((message) => message.id === selectedMessageId) || null

  function clearFilters() {
    setSearch(''); setDirection('all'); setStatus('all'); setKind('all'); setDateRange('all'); setCustomFrom(''); setCustomTo(''); setAdvancedFilters(emptyAdvancedFilters)
  }

  function removeFilter(key: string) {
    if (key === 'search') return setSearch('')
    if (key === 'direction') return setDirection('all')
    if (key === 'status') return setStatus('all')
    if (key === 'kind') return setKind('all')
    if (key === 'dateRange') { setDateRange('all'); setCustomFrom(''); setCustomTo(''); return }
    if (advancedFilterFields.some((field) => field.key === key)) setAdvancedFilters((current) => ({ ...current, [key]: null }))
  }

  function chooseFile(next: File | null) {
    setError(null)
    if (!next) return setFile(null)
    if (next.size > MAX_UPLOAD_BYTES) { setFile(null); setError('El archivo supera el límite de 25 MB.'); return }
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(next)
    setPreviewUrl(next.type.startsWith('image/') || next.type.startsWith('audio/') || next.type.startsWith('video/') ? URL.createObjectURL(next) : null)
  }

  function clearAttachment() {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setPreviewUrl(null); setFile(null); setAttachmentType(null); setCaption('')
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!runtimeName && !(instagram && connectionId)) return
    const target = instagram ? number.trim() : phone(number)
    if (instagram ? !target : target.length < 8) return setError(instagram ? 'Ingresá el External ID de Instagram.' : 'Ingresá un número de WhatsApp válido, con código de país.')
    if (!attachmentType && !text.trim()) return setError('Escribí un mensaje antes de enviarlo.')
    if (!instagram && attachmentType && !file) return setError('Seleccioná el archivo que querés enviar.')
    setError(null); setNotice(null); setIsSending(true); setProgress(0)
    try {
      if (instagram && connectionId) await sendInstagramWorkspaceMessage(connectionId, target, text.trim())
      else await sendWorkspaceMessage(runtimeName as string, attachmentType ? { number: target, type: attachmentType, file: file || undefined, caption } : { number: target, type: 'text', text: text.trim() }, setProgress)
      setText(''); clearAttachment(); setNotice('Mensaje enviado.'); await load(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'No se pudo enviar el mensaje.')
    } finally {
      setIsSending(false); setProgress(0)
    }
  }

  if (!runtimeName && !(instagram && connectionId)) return <section className="connection-section"><h3>Mensajes</h3><p className="connection-section-value">La conexión todavía no está lista para mensajería.</p></section>

  return <section className="connection-section workspace-messages">
    <div className="connection-section-heading"><div><h3>Mensajes</h3><p>Un mensaje lógico por envío o recepción; el detalle conserva su evidencia técnica.</p></div><div className="connection-inline-actions workspace-message-actions"><button type="button" className="client-button-secondary" onClick={() => void load()} disabled={isLoading}><RefreshCw size={15} aria-hidden="true" /> Actualizar</button></div></div>
    <Toast message={error} tone="error" onDismiss={() => setError(null)} />
    <Toast message={notice} tone="success" onDismiss={() => setNotice(null)} />
    <div className="workspace-message-controls">
      <div className="workspace-message-toolbar"><label className="workspace-message-search"><Search size={17} aria-hidden="true" /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar mensaje, ID o correlación…" aria-label="Buscar mensajes" /></label><button type="button" className={`client-button-secondary workspace-message-filter-toggle ${filtersOpen ? 'is-active' : ''}`} onClick={() => setFiltersOpen((value) => !value)} aria-expanded={filtersOpen}><SlidersHorizontal size={15} aria-hidden="true" /> Filtros{activeFilterCount ? ` · ${activeFilterCount}` : ''}</button></div>
      {activeFilters.length ? <div className="workspace-message-active-filters" aria-label="Filtros activos"><span>Filtros:</span>{activeFilters.map((filter) => <button key={filter.key} type="button" onClick={() => removeFilter(filter.key)}>{filter.label}<X size={13} aria-hidden="true" /><span className="sr-only">Quitar filtro</span></button>)}<button type="button" className="workspace-message-clear-filters" onClick={clearFilters}>Limpiar filtros</button></div> : null}
      {filtersOpen ? <section className="workspace-message-filter-panel" aria-label="Filtros de mensajes"><div className="workspace-message-filter-fields"><label><span>Dirección</span><Select value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}><option value="all">Todas</option><option value="inbound">Entrantes</option><option value="outbound">Salientes</option></Select></label><label><span>Estado</span><Select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">Todos</option>{availableStatuses.map((value) => <option key={value} value={value}>{statusLabel(value) || value}</option>)}</Select></label><label><span>Tipo</span><Select value={kind} onChange={(event) => setKind(event.target.value)}><option value="all">Todos</option>{availableKinds.map((value) => <option key={value} value={value}>{value}</option>)}</Select></label><label><span>Fecha</span><Select value={dateRange} onChange={(event) => setDateRange(event.target.value as DateRange)}><option value="all">Todas</option><option value="today">Hoy</option><option value="24h">Últimas 24 horas</option><option value="7d">Últimos 7 días</option><option value="30d">Últimos 30 días</option><option value="custom">Personalizado</option></Select></label></div>
        {dateRange === 'custom' ? <div className="workspace-message-custom-dates"><label><span>Desde</span><Input type="date" value={customFrom} onChange={(event) => setCustomFrom(event.target.value)} /></label><label><span>Hasta</span><Input type="date" value={customTo} onChange={(event) => setCustomTo(event.target.value)} /></label></div> : null}
        <details className="workspace-message-advanced-filters"><summary>Filtros avanzados</summary><div>{advancedFilterFields.map((field) => <label key={field.key}><span>{field.label}</span><Input value={advancedFilters[field.key] || ''} onChange={(event) => setAdvancedFilters((current) => ({ ...current, [field.key]: event.target.value || null }))} placeholder={field.placeholder} /></label>)}</div></details>
      </section> : null}
    </div>
    <div className="workspace-message-layout">
      <section className="workspace-message-list" aria-label="Lista de mensajes" aria-live="polite">
        {isLoading ? <LoadingState label="Cargando mensajes…" lines={4} /> : null}
        {!isLoading && visibleMessages.length === 0 ? <EmptyState icon={MessageCircle} title="No encontramos mensajes." description={activeFilterCount ? 'Probá ajustar o limpiar los filtros.' : 'Enviá un mensaje de prueba para verificar esta conexión.'} /> : null}
        {!isLoading ? <ol>{visibleMessages.map((message) => <li key={message.id}><button type="button" className={`workspace-message-row ${selectedMessage?.id === message.id ? 'is-selected' : ''}`} onClick={() => setSelectedMessageId(message.id)} aria-pressed={selectedMessage?.id === message.id}><span className={`workspace-message-direction workspace-message-direction-${message.direction}`}>{message.direction === 'inbound' ? <ArrowDownLeft size={16} aria-hidden="true" /> : <ArrowUpRight size={16} aria-hidden="true" />}{message.direction === 'inbound' ? 'Recibido' : 'Enviado'}</span><span className="workspace-message-row-main"><strong>{contentPreview(message)}</strong><span>{message.kind}</span></span><span className="workspace-message-row-meta"><time dateTime={new Date(message.timestamp).toISOString()}>{time(message.timestamp)}</time><MessageStatus status={message.status} /></span></button></li>)}</ol> : null}
      </section>
      <section className="workspace-message-detail" aria-label="Detalle de mensaje">
        {!selectedMessage ? <EmptyState icon={MessageCircle} title="Seleccioná un mensaje" description="Vas a ver su contenido, estado, identificadores y payload estructurado." /> : <><div className="workspace-message-detail-heading"><div><span className={`workspace-message-direction workspace-message-direction-${selectedMessage.direction}`}>{selectedMessage.direction === 'inbound' ? <ArrowDownLeft size={16} aria-hidden="true" /> : <ArrowUpRight size={16} aria-hidden="true" />}{selectedMessage.direction === 'inbound' ? 'Entrante' : 'Saliente'}</span><h4>{selectedMessage.kind}</h4><time dateTime={new Date(selectedMessage.timestamp).toISOString()}>{dateTime(selectedMessage.timestamp)}</time></div><MessageStatus status={selectedMessage.status} /></div><section className="workspace-message-detail-section"><h4>Contenido</h4>{selectedMessage.media ? <div className="workspace-media-summary">{typeIcon(selectedMessage.kind)}<span>{selectedMessage.media.fileName || `Archivo ${selectedMessage.kind}`}</span></div> : null}<p>{contentPreview(selectedMessage)}</p></section><section className="workspace-message-detail-section"><h4>Datos del mensaje</h4><dl className="workspace-message-identifiers"><div><dt>Dirección</dt><dd>{selectedMessage.direction === 'inbound' ? 'Entrante' : 'Saliente'}</dd></div><div><dt>Tipo</dt><dd>{selectedMessage.kind}</dd></div>{selectedMessage.sender ? <div><dt>Remitente</dt><dd>{selectedMessage.sender}</dd></div> : null}{selectedMessage.recipient ? <div><dt>Destinatario</dt><dd>{selectedMessage.recipient}</dd></div> : null}</dl></section><IdentifierList message={selectedMessage} /><section className="workspace-message-detail-section"><h4>Payload</h4><SafeJsonViewer value={selectedMessage.payload} emptyLabel="No hay payload disponible para este mensaje." /></section></>}
      </section>
    </div>
    <form className="workspace-composer" onSubmit={submit}>
      <label><span>{instagram ? 'Recipient External ID' : 'Número de destino'}</span><Input inputMode={instagram ? 'text' : 'numeric'} value={number} onChange={(event) => setNumber(event.target.value)} placeholder={instagram ? 'Instagram scoped user ID' : '549…'} disabled={isSending} required /></label>
      {attachmentType ? <div className="workspace-attachment"><div className="workspace-attachment-heading"><span>{types.find((type) => type.value === attachmentType)?.label}</span><button type="button" onClick={clearAttachment} disabled={isSending} aria-label="Quitar archivo"><X size={16} /></button></div><input type="file" accept={types.find((type) => type.value === attachmentType)?.accept} onChange={(event) => chooseFile(event.target.files?.[0] || null)} disabled={isSending} />{file ? <p>{file.name} · {Math.ceil(file.size / 1024)} KB</p> : null}{previewUrl && attachmentType === 'image' ? <img src={previewUrl} alt="Vista previa del archivo" /> : null}{previewUrl && attachmentType === 'audio' ? <audio controls src={previewUrl} /> : null}{previewUrl && attachmentType === 'video' ? <video controls src={previewUrl} /> : null}<Input value={caption} onChange={(event) => setCaption(event.target.value)} placeholder="Descripción opcional" maxLength={4096} disabled={isSending} /></div> : <label><span>Mensaje</span><Textarea value={text} onChange={(event) => setText(event.target.value)} rows={3} maxLength={4096} placeholder="Escribí un mensaje…" disabled={isSending} /></label>}
      <div className="workspace-composer-actions">{!instagram ? <div className="workspace-file-types"><span>Adjuntar</span>{types.map((type) => <button key={type.value} type="button" onClick={() => { setAttachmentType(type.value); setText('') }} disabled={isSending} title={type.label}>{typeIcon(type.value)}<span>{type.label}</span></button>)}</div> : <span className="connection-endpoint-note">Instagram usa identificadores externos opacos, nunca teléfonos.</span>}<button className="client-button-primary" type="submit" disabled={isSending}><Send size={15} aria-hidden="true" /> {isSending ? 'Enviando…' : 'Enviar'}</button></div>
      {isSending && attachmentType ? <div className="workspace-progress"><span style={{ width: `${progress}%` }} /><small>Subiendo archivo: {progress}%</small></div> : null}
    </form>
  </section>
}
