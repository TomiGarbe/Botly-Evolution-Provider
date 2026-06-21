import { useState } from 'react'
import useSWR from 'swr'
import { QrCode, LogOut, Trash2, Wifi, WifiOff, Loader2, KeyRound, Copy } from 'lucide-react'
import { api } from '../lib/api'
import type { GatewayConfig } from '../lib/config'
import type { Instance, ConnectionStatus, InstanceApiKey, Toast } from '../types'

interface Props {
  instance: Instance
  config: GatewayConfig
  onToast: (message: string, type?: Toast['type']) => void
  onQR: (name: string) => void
  onLogout: (name: string) => void
  onDelete: (name: string) => void
  onApiKey: (name: string) => void
}

const statusConfig: Record<ConnectionStatus, { label: string; dot: string; text: string }> = {
  open: { label: 'Conectado', dot: 'bg-emerald-500', text: 'text-emerald-400' },
  connecting: { label: 'Conectando', dot: 'bg-amber-400 animate-pulse', text: 'text-amber-400' },
  close: { label: 'Desconectado', dot: 'bg-zinc-600', text: 'text-zinc-500' },
}

function normalizeBaseUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, '')
}

export default function InstanceCard({ instance, config, onToast, onQR, onLogout, onDelete, onApiKey }: Props) {
  const { name, id, status } = instance
  const cfg = statusConfig[status] ?? statusConfig.close
  const [copiedKey, setCopiedKey] = useState('')

  const publicBaseUrl = normalizeBaseUrl(config.publicBaseUrl || config.url)
  const sendMessageUrl = `${publicBaseUrl}/messages/${name}`

  const { data: apiKeyInfo } = useSWR<InstanceApiKey>(
    config.apiKey ? ['instance-api-key', config.url, name] : null,
    () => api.instances.getApiKey(config, name),
    { dedupingInterval: 15000, revalidateOnFocus: false }
  )

  const authTypeLabel = apiKeyInfo?.enabled ? 'Bearer token por instancia' : 'Sin auth por instancia'
  const apiKeyPreview = apiKeyInfo?.maskedApiKey || 'No disponible'

  const copyText = async (text: string, key: string, ok = 'Copiado') => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedKey(key)
      onToast(ok, 'success')
      window.setTimeout(() => setCopiedKey(current => (current === key ? '' : current)), 1500)
    } catch {
      onToast('Error al copiar', 'error')
    }
  }

  return (
    <div className="group relative bg-zinc-900 border border-zinc-800 rounded-xl p-5 flex flex-col gap-4 hover:border-zinc-700 transition-colors animate-fade-in">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold text-sm truncate">{name}</p>
          {id && (
            <p className="text-xs text-zinc-600 font-mono mt-0.5 truncate">
              {id.substring(0, 8)}...
            </p>
          )}
        </div>

        <span className={`flex items-center gap-1.5 text-xs font-medium shrink-0 ${cfg.text}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
          {cfg.label}
        </span>
      </div>

      <div className="flex items-center gap-2 text-xs text-zinc-600">
        {status === 'open' && <Wifi size={12} className="text-emerald-600" />}
        {status === 'connecting' && <Loader2 size={12} className="animate-spin text-amber-500" />}
        {status === 'close' && <WifiOff size={12} />}
        <span>WHATSAPP-BAILEYS</span>
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 flex flex-col gap-2.5">
        <p className="text-xs font-semibold text-zinc-300">Integration</p>
        <div className="text-[11px] text-zinc-500">Endpoint recomendado: <span className="font-mono text-zinc-400">POST /messages/{name}</span></div>

        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] uppercase tracking-wide text-zinc-500">Mensajes unificados</p>
            <p className="text-[11px] font-mono text-zinc-300 truncate" title={sendMessageUrl}>{sendMessageUrl}</p>
          </div>
          <button
            onClick={() => copyText(sendMessageUrl, 'messages', 'URL de mensajes copiada')}
            className="shrink-0 px-2 py-1 text-[11px] text-zinc-300 border border-zinc-700 rounded-md hover:border-zinc-600"
            title="Copy Unified Messages URL"
          >
            {copiedKey === 'messages' ? 'copied!' : <Copy size={12} />}
          </button>
        </div>

        <div className="pt-1 border-t border-zinc-800">
          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Auth type</p>
          <p className="text-[11px] text-zinc-300">{authTypeLabel}</p>
          {apiKeyInfo?.hasApiKey && (
            <p className="text-[11px] font-mono text-zinc-300 mt-0.5 truncate" title={apiKeyPreview}>
              API key activa: {apiKeyPreview}
            </p>
          )}
          <p className="text-[10px] text-zinc-500 mt-0.5">Estado API key: {apiKeyInfo?.enabled ? 'activa' : 'inactiva'} {apiKeyInfo?.hasApiKey ? '(generada)' : '(sin generar)'}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-zinc-800">
        <button
          onClick={() => onApiKey(name)}
          className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-md transition-colors"
        >
          <KeyRound size={12} />
          API Key
        </button>
        {status !== 'open' && (
          <button
            onClick={() => onQR(name)}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
          >
            <QrCode size={12} />
            Ver QR
          </button>
        )}
        {status === 'open' && (
          <button
            onClick={() => onLogout(name)}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-md transition-colors"
          >
            <LogOut size={12} />
            Desconectar
          </button>
        )}
        <div className="flex-1 hidden sm:block" />
        <button
          onClick={() => onDelete(name)}
          className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 text-zinc-600 hover:text-red-400 hover:bg-red-950/40 rounded-md transition-colors"
        >
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  )
}
