import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import { api, ApiError } from '../lib/api'
import type { GatewayConfig } from '../lib/config'
import type { Instance, PipelineEvent } from '../types'

function cleanNumber(value: string): string {
  return value.replace(/[^0-9]/g, '')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isLongLike(value: unknown): value is { low: unknown; high: unknown; unsigned: unknown } {
  return isRecord(value) && 'low' in value && 'high' in value && 'unsigned' in value
}

function longLikeToBigInt(value: { low: unknown; high: unknown; unsigned: unknown }): bigint | null {
  const low = Number(value.low)
  const high = Number(value.high)
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null

  let result = (BigInt(high >>> 0) << 32n) + BigInt(low >>> 0)
  if (!value.unsigned && result >= 2n ** 63n) {
    result -= 2n ** 64n
  }
  return result
}

function valueToText(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : fallback
  if (typeof value === 'boolean') return String(value)
  if (typeof value === 'bigint') return value.toString()
  if (isLongLike(value)) return longLikeToBigInt(value)?.toString() ?? fallback
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function valueToNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value)
  if (isLongLike(value)) {
    const parsed = longLikeToBigInt(value)
    if (parsed !== null && parsed <= BigInt(Number.MAX_SAFE_INTEGER) && parsed >= BigInt(Number.MIN_SAFE_INTEGER)) {
      return Number(parsed)
    }
  }
  return fallback
}

function normalizeTimestamp(value: unknown): number {
  const parsed = valueToNumber(value, Date.now())
  if (!Number.isFinite(parsed) || parsed <= 0) return Date.now()
  return parsed < 1_000_000_000_000 ? parsed * 1000 : parsed
}

function formatTimestamp(value: number): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString()
}

function sanitizeForJson(value: unknown, seen = new WeakSet<object>(), depth = 0): unknown {
  if (value === null || value === undefined) return value
  if (typeof value === 'bigint') return value.toString()
  if (typeof value === 'string') {
    return value.length > 1200 ? `${value.slice(0, 1200)}... [truncated ${value.length - 1200} chars]` : value
  }
  if (typeof value !== 'object') return value
  if (isLongLike(value)) {
    return {
      low: value.low,
      high: value.high,
      unsigned: value.unsigned,
      value: longLikeToBigInt(value)?.toString() ?? null,
    }
  }
  if (seen.has(value)) return '[Circular]'
  if (depth >= 10) return '[Max depth reached]'

  seen.add(value)
  if (Array.isArray(value)) return value.map(item => sanitizeForJson(item, seen, depth + 1))

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, sanitizeForJson(item, seen, depth + 1)])
  )
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(sanitizeForJson(value), null, 2)
  } catch {
    return valueToText(value, '{}')
  }
}

type ConsoleDirection = 'incoming' | 'outgoing' | 'system'
type EventKind = 'text' | 'media' | 'system' | 'error'
type MessageMode = 'text' | 'audio' | 'image' | 'video' | 'file'

type ConsoleMedia = {
  id?: string
  kind?: string
  mimeType?: string
  fileName?: string
  fileSize?: string
  duration?: string
  caption?: string
  isVoiceNote?: boolean
  url?: string
  directPath?: string
  downloadSource?: string
}

type ConsoleItem = {
  id: string
  timestamp: number
  instance: string
  direction: ConsoleDirection
  sender: string
  recipient: string
  messageType: string
  kind: EventKind
  text: string
  media: ConsoleMedia | null
  status: string
  fromBot: boolean
  fromMe: boolean
  forwarding: string
  error: string
  requestId: string
  interaction: PipelineEvent['interaction']
  quotedPreview: string
  badges: string[]
  raw: PipelineEvent
}

function mapMedia(media: PipelineEvent['media'], fallbackKind: string): ConsoleMedia | null {
  if (!media) return null
  return {
    id: valueToText(media.id, ''),
    kind: valueToText(media.kind ?? fallbackKind, fallbackKind),
    mimeType: valueToText(media.mimeType, ''),
    fileName: valueToText(media.fileName, ''),
    fileSize: valueToText(media.fileSize, ''),
    duration: valueToText(media.duration, ''),
    caption: valueToText(media.caption, ''),
    isVoiceNote: Boolean(media.isVoiceNote),
    url: valueToText(media.url, ''),
    directPath: valueToText(media.directPath, ''),
    downloadSource: valueToText(media.downloadSource, ''),
  }
}

function mapEvent(item: PipelineEvent, idx: number): ConsoleItem {
  const direction: ConsoleDirection =
    item.direction === 'outbound' ? 'outgoing' : item.direction === 'inbound' ? 'incoming' : 'system'
  const messageType = valueToText(item.messageType ?? item.message?.kind ?? item.event, 'unknown')
  const hasMedia = Boolean(item.media)
  const hasError = Boolean(item.error?.message) || /failed|error|dropped|throttled/i.test(valueToText(item.status ?? item.pipeline?.status, ''))
  const kind: EventKind = hasError ? 'error' : hasMedia ? 'media' : item.layer === 'operational' ? 'system' : 'text'
  const status = valueToText(item.status ?? item.pipeline?.status, 'ok')
  const forwarding = valueToText(item.forwarding?.status ?? item.pipeline?.stage, 'n/a')
  const error = valueToText(item.error?.message ?? item.details?.error, '')
  const contentText =
    typeof item.text === 'string'
      ? item.text
      : typeof item.content === 'string'
        ? item.content
        : isRecord(item.content) && 'text' in item.content
          ? valueToText(item.content.text, '')
          : ''
  const selectedTitle = valueToText(item.interaction?.title, '').trim()
  const selectedId = valueToText(item.interaction?.id, '').trim()
  const interactionLabel = selectedTitle || selectedId
  const badges: string[] = []
  if (item.metadata?.ephemeral) badges.push('ephemeral')
  if (item.metadata?.edited) badges.push('edited')
  if (item.metadata?.revoked) badges.push('revoked')
  if (item.metadata?.fromBusiness) badges.push('business')
  if (item.metadata?.newsletter) badges.push('newsletter')
  if (item.metadata?.statusMessage) badges.push('status')

  let richPreview = ''
  if (messageType === 'reaction') {
    const emoji = isRecord(item.content) ? valueToText(item.content.emoji, '') : ''
    const removed = Boolean(item.metadata?.removeReaction)
    richPreview = removed ? 'Reaction removed' : `Reaction: ${emoji || '(empty)'}`
  } else if (messageType === 'location') {
    const c = isRecord(item.content) ? item.content : {}
    const lat = valueToText(c.latitude)
    const lon = valueToText(c.longitude)
    richPreview = `Location: ${lat}, ${lon}${item.metadata?.liveLocation ? ' (live)' : ''}`
  } else if (messageType === 'contact') {
    const c = isRecord(item.content) ? item.content : {}
    const contacts = Array.isArray(c.contacts) ? c.contacts : []
    richPreview = `Contacts: ${contacts.length}`
  } else if (messageType === 'poll_create') {
    const c = isRecord(item.content) ? item.content : {}
    richPreview = `Poll: ${String(c.title ?? '')} · options:${Array.isArray(c.options) ? c.options.length : 0}`
  } else if (messageType === 'poll_update') {
    const c = isRecord(item.content) ? item.content : {}
    richPreview = `Poll vote update: ${Array.isArray(c.selectedOptions) ? c.selectedOptions.length : 0}`
  }

  return {
    id: valueToText(item.id ?? item.message?.id ?? `${valueToText(item.timestamp, 'event')}-${idx}`, `event-${idx}`),
    timestamp: normalizeTimestamp(item.timestamp),
    instance: valueToText(item.instance, 'unknown'),
    direction,
    sender: valueToText(item.sender ?? item.message?.from),
    recipient: valueToText(item.recipient),
    messageType,
    kind,
    text: interactionLabel || richPreview || contentText,
    media: mapMedia(item.media, messageType),
    status,
    fromBot: Boolean(item.fromBot) || valueToText(item.event, '').startsWith('FORWARD_'),
    fromMe: Boolean(item.fromMe ?? item.message?.fromMe),
    forwarding,
    error,
    requestId: valueToText(item.meta?.requestId ?? item.pipeline?.requestId, ''),
    interaction: item.interaction,
    quotedPreview: valueToText(item.context?.quoted?.preview, ''),
    badges,
    raw: item,
  }
}

const MAX_UPLOAD_BYTES = 25 * 1024 * 1024

const modeToMediaType: Record<Exclude<MessageMode, 'text'>, 'audio' | 'image' | 'video' | 'document'> = {
  audio: 'audio',
  image: 'image',
  video: 'video',
  file: 'document',
}

function resolveUploadMediaType(mode: Exclude<MessageMode, 'text'>, file: File): 'audio' | 'image' | 'video' | 'document' | 'pdf' | 'file' {
  if (mode !== 'file') return modeToMediaType[mode]
  if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) return 'pdf'
  return 'document'
}

const acceptedByMode: Record<Exclude<MessageMode, 'text'>, string> = {
  audio: 'audio/*',
  image: 'image/*',
  video: 'video/*',
  file: '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.json,.xml,.rtf,.bin,application/pdf,application/msword,application/vnd.,text/plain,application/zip,application/json,application/xml',
}

function supportsMode(file: File, mode: Exclude<MessageMode, 'text'>): boolean {
  if (mode === 'audio') return file.type.startsWith('audio/')
  if (mode === 'image') return file.type.startsWith('image/')
  if (mode === 'video') return file.type.startsWith('video/')
  return true
}

function previewUrlFor(file: File | null, mode: MessageMode): string | null {
  if (!file || mode === 'text' || mode === 'file') return null
  if (!supportsMode(file, mode)) return null
  return URL.createObjectURL(file)
}

function isOpenInstance(instance: Instance): boolean {
  return instance.status === 'open'
}

export default function MediaLab({
  config,
  instances,
  instancesLoading,
  instancesError,
  onToast,
}: {
  config: GatewayConfig
  instances: Instance[]
  instancesLoading: boolean
  instancesError?: string
  onToast: (msg: string, type?: 'success' | 'error' | 'info') => void
}) {
  const [instance, setInstance] = useState('')
  const [number, setNumber] = useState('')
  const [text, setText] = useState('')
  const [caption, setCaption] = useState('')
  const [mode, setMode] = useState<MessageMode>('text')
  const [file, setFile] = useState<File | null>(null)
  const [sendError, setSendError] = useState('')
  const [sendSuccess, setSendSuccess] = useState('')
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [paused, setPaused] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [onlyDirection, setOnlyDirection] = useState<'all' | ConsoleDirection>('all')
  const [onlyKind, setOnlyKind] = useState<'all' | EventKind>('all')
  const [hideSystem, setHideSystem] = useState(true)
  const [onlyBot, setOnlyBot] = useState(false)
  const [onlyErrors, setOnlyErrors] = useState(false)
  const [limit, setLimit] = useState(400)
  const [clearedAt, setClearedAt] = useState(0)
  const [expandedJson, setExpandedJson] = useState<Record<string, boolean>>({})
  const listRef = useRef<HTMLDivElement | null>(null)
  const uploadAbortRef = useRef<AbortController | null>(null)

  const openInstances = useMemo(() => instances.filter(isOpenInstance), [instances])

  useEffect(() => {
    if (!openInstances.length) {
      setInstance('')
      return
    }
    if (!openInstances.some(item => item.name === instance)) {
      setInstance(openInstances[0].name)
    }
  }, [openInstances, instance])

  const { data: eventsData, mutate: mutateEvents, isLoading } = useSWR(
    config.apiKey ? ['events', instance, limit] : null,
    () => api.webhooks.events<PipelineEvent>(config, instance || undefined, limit),
    { refreshInterval: paused ? 0 : 2500, dedupingInterval: 1000 }
  )

  const allItems = useMemo(() => {
    const mapped = (eventsData?.items ?? []).map(mapEvent)
    const deduped = new Map<string, ConsoleItem>()
    mapped.forEach(item => {
      const key = `${item.instance}:${item.id}:${item.status}:${item.messageType}:${item.timestamp}`
      if (!deduped.has(key)) deduped.set(key, item)
    })
    return [...deduped.values()]
      .filter(item => item.timestamp >= clearedAt)
      .sort((a, b) => a.timestamp - b.timestamp)
  }, [eventsData?.items, clearedAt])

  const items = useMemo(() => {
    return allItems.filter(item => {
      if (hideSystem && (item.direction === 'system' || item.kind === 'system')) return false
      if (onlyDirection !== 'all' && item.direction !== onlyDirection) return false
      if (onlyKind !== 'all' && item.kind !== onlyKind) return false
      if (onlyBot && !item.fromBot) return false
      if (onlyErrors && item.kind !== 'error') return false
      return true
    })
  }, [allItems, onlyDirection, onlyKind, onlyBot, onlyErrors, hideSystem])

  useEffect(() => {
    if (!items.length) return
    const last = items[items.length - 1]
    console.debug('[CHAT][RENDER]', {
      total: items.length,
      instance: last.instance,
      direction: last.direction,
      messageType: last.messageType,
      id: last.id,
      ts: last.timestamp,
    })
  }, [items])

  useEffect(() => {
    if (!autoScroll || !listRef.current) return
    listRef.current.scrollTop = listRef.current.scrollHeight
  }, [items, autoScroll])

  const previewUrl = useMemo(() => previewUrlFor(file, mode), [file, mode])

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  const setFileFromInput = (next: File | null) => {
    setSendError('')
    setSendSuccess('')
    if (!next) {
      setFile(null)
      return
    }
    if (next.size > MAX_UPLOAD_BYTES) {
      setFile(null)
      setSendError('El archivo supera 25MB.')
      return
    }
    if (mode !== 'text' && mode !== 'file' && !supportsMode(next, mode)) {
      setFile(null)
      setSendError(`El archivo no corresponde al modo ${mode}.`)
      return
    }
    setFile(next)
  }

  const validateCommon = (): string | null => {
    if (!instance) return 'Selecciona una instancia conectada.'
    const clean = cleanNumber(number)
    if (clean.length < 8) return 'Numero invalido. Usa formato internacional sin +.'
    return null
  }

  const sendText = async () => {
    const common = validateCommon()
    if (common) {
      setSendError(common)
      return
    }
    const trimmed = text.trim()
    if (!trimmed) {
      setSendError('Escribe un mensaje de texto.')
      return
    }

    setSending(true)
    setSendError('')
    setSendSuccess('')
    try {
      await api.messages.send(config, instance, { number: cleanNumber(number), type: 'text', text: trimmed })
      setText('')
      setSendSuccess('Texto enviado correctamente.')
      onToast('Mensaje enviado', 'success')
      await mutateEvents()
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Error al enviar mensaje'
      setSendError(message)
      onToast(message, 'error')
    } finally {
      setSending(false)
    }
  }

  const sendMedia = async () => {
    const common = validateCommon()
    if (common) {
      setSendError(common)
      return
    }
    if (mode === 'text') return
    if (!file) {
      setSendError('Selecciona un archivo.')
      return
    }
    if (!supportsMode(file, mode)) {
      setSendError('Tipo de archivo no valido para el modo elegido.')
      return
    }

    const abortController = new AbortController()
    uploadAbortRef.current = abortController

    setSending(true)
    setUploading(true)
    setUploadProgress(0)
    setSendError('')
    setSendSuccess('')

    try {
      await api.messages.sendMultipart(
        config,
        instance,
        {
          number: cleanNumber(number),
          type: resolveUploadMediaType(mode, file),
          caption: caption.trim() || undefined,
        },
        file,
        {
          signal: abortController.signal,
          onProgress: setUploadProgress,
        }
      )

      setCaption('')
      setFile(null)
      setUploadProgress(100)
      setSendSuccess('Archivo enviado correctamente.')
      onToast('Media enviada', 'success')
      await mutateEvents()
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Error al enviar archivo'
      setSendError(message)
      if (message !== 'Carga cancelada') onToast(message, 'error')
    } finally {
      setSending(false)
      setUploading(false)
      uploadAbortRef.current = null
    }
  }

  const onSubmit = async () => {
    if (sending || uploading) return
    if (mode === 'text') {
      await sendText()
      return
    }
    await sendMedia()
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-1 bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-3">
        <p className="text-sm font-semibold text-zinc-200">Consola de testing WhatsApp</p>
        <div className="space-y-1">
          <label className="text-xs text-zinc-400">Instancia conectada</label>
          <select
            className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm disabled:opacity-60"
            value={instance}
            onChange={e => setInstance(e.target.value)}
            disabled={instancesLoading || openInstances.length === 0 || sending}
          >
            <option value="">{instancesLoading ? 'Cargando instancias...' : 'Seleccionar instancia open'}</option>
            {openInstances.map(item => (
              <option key={item.id} value={item.name}>
                {item.name} | {item.status} {item.phone ? `| ${item.phone}` : ''} {item.profileName ? `| ${item.profileName}` : ''}
              </option>
            ))}
          </select>
          {instancesError ? <p className="text-xs text-red-400">{instancesError}</p> : null}
          {!instancesLoading && openInstances.length === 0 ? <p className="text-xs text-amber-400">No hay instancias conectadas en estado open.</p> : null}
        </div>

        <input
          className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm"
          placeholder="Numero destino (549...)"
          value={number}
          onChange={e => setNumber(cleanNumber(e.target.value))}
          disabled={sending}
        />

        <div className="grid grid-cols-2 sm:grid-cols-5 gap-1 rounded border border-zinc-800 p-1 bg-zinc-950">
          {(['text', 'audio', 'image', 'video', 'file'] as MessageMode[]).map(item => (
            <button
              key={item}
              onClick={() => {
                setMode(item)
                setSendError('')
                setSendSuccess('')
              }}
              disabled={sending}
              className={`text-xs rounded px-2 py-1 ${mode === item ? 'bg-blue-600 text-white' : 'text-zinc-300 hover:bg-zinc-800'}`}
            >
              {item === 'text' ? 'Texto' : item === 'audio' ? 'Audio' : item === 'image' ? 'Imagen' : item === 'video' ? 'Video' : 'Archivo'}
            </button>
          ))}
        </div>

        {mode === 'text' ? (
          <textarea
            className="w-full h-28 bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm resize-none"
            placeholder="Escribe un mensaje..."
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void onSubmit()
              }
            }}
            disabled={sending}
          />
        ) : (
          <div
            className="space-y-2 rounded border border-dashed border-zinc-700 p-3"
            onDragOver={e => e.preventDefault()}
            onDrop={e => {
              e.preventDefault()
              const next = e.dataTransfer.files?.[0] ?? null
              setFileFromInput(next)
            }}
          >
            <input
              type="file"
              accept={acceptedByMode[mode]}
              onChange={e => setFileFromInput(e.target.files?.[0] ?? null)}
              disabled={sending}
              className="block w-full text-xs text-zinc-400 file:mr-3 file:rounded file:border-0 file:bg-blue-600 file:px-3 file:py-1.5 file:text-xs file:text-white"
            />
            <p className="text-xs text-zinc-500">Drag and drop simple habilitado.</p>
            {file ? <p className="text-xs text-zinc-300 break-all">{file.name} ({Math.round(file.size / 1024)} KB)</p> : null}
            {previewUrl && mode === 'image' ? <img src={previewUrl} alt="preview" className="max-h-44 rounded border border-zinc-800" /> : null}
            {previewUrl && mode === 'audio' ? <audio controls src={previewUrl} className="w-full" /> : null}
            {previewUrl && mode === 'video' ? <video controls src={previewUrl} className="max-h-52 w-full rounded border border-zinc-800" /> : null}
            <input
              className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm"
              placeholder="Caption opcional"
              value={caption}
              onChange={e => setCaption(e.target.value)}
              disabled={sending}
            />
            {uploading ? (
              <div className="space-y-1">
                <div className="h-2 bg-zinc-800 rounded overflow-hidden">
                  <div className="h-full bg-blue-600 transition-all" style={{ width: `${uploadProgress}%` }} />
                </div>
                <p className="text-xs text-zinc-400">Upload: {uploadProgress}%</p>
              </div>
            ) : null}
          </div>
        )}

        {sendError ? <p className="text-xs text-red-400">{sendError}</p> : null}
        {sendSuccess ? <p className="text-xs text-emerald-400">{sendSuccess}</p> : null}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <button
            onClick={() => void onSubmit()}
            disabled={sending || !instance || openInstances.length === 0}
            className="w-full px-3 py-2 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm"
          >
            {sending ? 'Enviando...' : mode === 'text' ? 'Enviar texto' : 'Enviar archivo'}
          </button>
          <button
            onClick={() => uploadAbortRef.current?.abort()}
            disabled={!uploading}
            className="w-full px-3 py-2 rounded border border-zinc-700 hover:border-zinc-500 disabled:opacity-50 text-sm"
          >
            Cancelar
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2 border-t border-zinc-800">
          <button onClick={() => setPaused(v => !v)} className="px-2 py-1.5 text-xs rounded border border-zinc-700 hover:border-zinc-500">{paused ? 'Reanudar polling' : 'Pausar polling'}</button>
          <button onClick={() => setAutoScroll(v => !v)} className="px-2 py-1.5 text-xs rounded border border-zinc-700 hover:border-zinc-500">{autoScroll ? 'Auto-scroll ON' : 'Auto-scroll OFF'}</button>
          <button onClick={() => setClearedAt(Date.now())} className="px-2 py-1.5 text-xs rounded border border-zinc-700 hover:border-zinc-500">Clear timeline</button>
          <button onClick={() => mutateEvents()} className="px-2 py-1.5 text-xs rounded border border-zinc-700 hover:border-zinc-500">Refresh ahora</button>
        </div>
      </div>

      <div className="lg:col-span-2 bg-zinc-900 border border-zinc-800 rounded-xl p-4 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <select value={onlyDirection} onChange={e => setOnlyDirection(e.target.value as 'all' | ConsoleDirection)} className="bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-xs">
            <option value="all">all directions</option>
            <option value="incoming">incoming</option>
            <option value="outgoing">outgoing</option>
            <option value="system">system</option>
          </select>
          <select value={onlyKind} onChange={e => setOnlyKind(e.target.value as 'all' | EventKind)} className="bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-xs">
            <option value="all">text/media/system/error</option>
            <option value="text">text</option>
            <option value="media">media</option>
            <option value="system">system events</option>
            <option value="error">errors</option>
          </select>
          <label className="text-xs text-zinc-400 flex items-center gap-1"><input type="checkbox" checked={onlyBot} onChange={e => setOnlyBot(e.target.checked)} /> bot only</label>
          <label className="text-xs text-zinc-400 flex items-center gap-1"><input type="checkbox" checked={onlyErrors} onChange={e => setOnlyErrors(e.target.checked)} /> errors only</label>
          <label className="text-xs text-zinc-400 flex items-center gap-1"><input type="checkbox" checked={hideSystem} onChange={e => setHideSystem(e.target.checked)} /> hide system</label>
          <select value={String(limit)} onChange={e => setLimit(Number(e.target.value))} className="bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-xs">
            <option value="200">200</option>
            <option value="400">400</option>
            <option value="600">600</option>
          </select>
          <span className="text-xs text-zinc-500">{isLoading ? 'Cargando...' : `${items.length} eventos`}</span>
        </div>

        <div ref={listRef} className="space-y-2 max-h-[70vh] lg:max-h-[640px] overflow-auto pr-1">
          {items.map(item => {
            const eventKey = `${item.instance}:${item.id}:${item.timestamp}`
            const jsonIsOpen = Boolean(expandedJson[eventKey])
            const interactionType = valueToText(item.interaction?.interactionType, '')
            const tone =
              item.kind === 'error'
                ? 'bg-red-950/30 border-red-900/50'
                : item.direction === 'incoming'
                  ? 'bg-zinc-950 border-zinc-800'
                  : item.direction === 'outgoing'
                    ? 'bg-blue-950/35 border-blue-900/50'
                    : 'bg-zinc-950/70 border-zinc-800'

            return (
              <div key={eventKey} className={`border rounded p-2.5 ${tone}`}>
                <div className="flex items-center justify-between text-[11px] text-zinc-400 gap-2">
                  <span>{item.instance}</span>
                  <span>{formatTimestamp(item.timestamp)}</span>
                </div>
                <p className="text-[11px] text-zinc-500 mt-1">
                  {item.direction.toUpperCase()} · {item.messageType} · status:{item.status} · fwd:{item.forwarding}
                </p>
                {item.interaction && interactionType ? (
                  <p className="text-[11px] text-cyan-300 mt-1 break-all">
                    interactive: {interactionType}
                    {item.interaction.id ? ` · id:${item.interaction.id}` : ''}
                    {item.interaction.title ? ` · title:${item.interaction.title}` : ''}
                  </p>
                ) : null}
                {item.badges.length ? (
                  <p className="text-[11px] text-amber-300 mt-1 break-all">flags: {item.badges.join(', ')}</p>
                ) : null}
                {item.quotedPreview ? <p className="text-[11px] text-zinc-400 mt-1 break-all">quoted: {item.quotedPreview}</p> : null}
                <p className="text-[11px] text-zinc-500 mt-1 break-all">
                  from:{item.sender} to:{item.recipient} · fromMe:{String(item.fromMe)} · fromBot:{String(item.fromBot)}
                </p>
                {item.text ? <p className="text-sm text-zinc-200 mt-1 whitespace-pre-wrap">{item.text}</p> : null}
                {item.media ? (
                  <div className="text-[11px] text-zinc-300 mt-1 break-all">
                    media: {item.media.kind ?? item.messageType} · {item.media.mimeType ?? 'n/a'} · {item.media.fileName ?? item.media.id} · source:{item.media.downloadSource ?? 'provider-url'} · size:{item.media.fileSize ?? 'n/a'}
                    {item.media.url ? ` · ${item.media.url}` : ''}
                  </div>
                ) : null}
                {item.error ? <p className="text-[11px] text-red-300 mt-1 break-all">error: {item.error}</p> : null}
                {item.requestId ? <p className="text-[11px] text-zinc-500 mt-1 break-all">request: {item.requestId}</p> : null}
                <div className="mt-2">
                  <button
                    type="button"
                    onClick={() => setExpandedJson(current => ({ ...current, [eventKey]: !current[eventKey] }))}
                    className="px-2 py-1 text-[11px] rounded border border-zinc-700 text-zinc-300 hover:border-zinc-500"
                  >
                    {jsonIsOpen ? 'Ocultar JSON' : 'Ver JSON'}
                  </button>
                </div>
                {jsonIsOpen ? (
                  <pre className="mt-2 max-h-80 overflow-auto rounded border border-zinc-800 bg-black/40 p-2 text-[11px] text-zinc-200 whitespace-pre-wrap break-words">
                    {formatJson(item.raw)}
                  </pre>
                ) : null}
              </div>
            )
          })}
          {items.length === 0 ? <p className="text-xs text-zinc-500">No hay eventos para los filtros actuales.</p> : null}
        </div>
      </div>
    </div>
  )
}
