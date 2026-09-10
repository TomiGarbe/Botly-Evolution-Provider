import { environment } from '@/app/config/environment'
import { gatewayRequest } from '@/shared/lib/gatewayClient'

export type MessageKind = 'text' | 'image' | 'audio' | 'document' | 'video'

export interface TimelineMessage {
  id: string
  messageId: string | null
  timestamp: number
  direction: 'inbound' | 'outbound'
  kind: MessageKind | string
  text: string
  status: string | null
  sender: string | null
  recipient: string | null
  media: { id?: string; kind?: string; mimeType?: string; fileName?: string; caption?: string; url?: string } | null
  provider: string | null
  providerMessageId: string | null
  conversationId: string | null
  channelId: string | null
  connectionId: string | null
  correlationId: string | null
  requestId: string | null
  eventId: string | null
  deliveryId: string | null
  outboundAttemptId: string | null
  payload: unknown
}

export async function listTimelineMessages(runtimeName: string): Promise<TimelineMessage[]> {
  const payload = await gatewayRequest<{ items: TimelineMessage[] }>(`/messages/${encodeURIComponent(runtimeName)}/timeline?limit=200`)
  return payload.items
}

/** Provider routing belongs to the Gateway, so the workspace stays uniform. */
export async function listConnectionTimelineMessages(connectionId: string): Promise<TimelineMessage[]> {
  const payload = await gatewayRequest<{ items: TimelineMessage[] }>(`/connections/${encodeURIComponent(connectionId)}/messages?limit=200`)
  return payload.items
}

export async function sendConnectionText(connectionId: string, recipient: string, text: string): Promise<void> {
  await gatewayRequest(`/connections/${encodeURIComponent(connectionId)}/messages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ recipient, text }),
  })
}

export function sendWorkspaceMessage(
  runtimeName: string,
  input: { number: string; type: MessageKind; text?: string; caption?: string; file?: File },
  onProgress: (progress: number) => void,
): Promise<void> {
  const form = new FormData()
  form.set('number', input.number)
  form.set('type', input.type)
  if (input.type === 'text') form.set('text', input.text || '')
  else {
    form.set('caption', input.caption || '')
    if (input.file) form.set('file', input.file)
  }
  const baseUrl = environment.gatewayUrl || window.location.origin
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', new URL(`/messages/${encodeURIComponent(runtimeName)}`, baseUrl).toString())
    request.withCredentials = true
    request.setRequestHeader('Accept', 'application/json')
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    request.onerror = () => reject(new Error('No se pudo conectar con el Gateway. Intentá nuevamente.'))
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) return resolve()
      if (request.status === 401) return reject(new Error('Tu sesión venció. Volvé a iniciar sesión.'))
      if (request.status === 413) return reject(new Error('El archivo supera el tamaño permitido.'))
      if (request.status === 415) return reject(new Error('Ese tipo de archivo no está permitido.'))
      reject(new Error('No se pudo enviar el mensaje. Verificá la conexión e intentá nuevamente.'))
    }
    request.send(form)
  })
}
