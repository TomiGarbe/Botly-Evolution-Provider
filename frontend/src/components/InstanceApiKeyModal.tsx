import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, KeyRound, RefreshCcw, ShieldOff, X } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import type { GatewayConfig } from '../lib/config'
import type { InstanceApiKey } from '../types'

interface Props {
  config: GatewayConfig
  instanceName: string
  onClose: () => void
  onToast: (message: string, type?: 'success' | 'error' | 'info') => void
  onRevealApiKey: (apiKey: string) => void
}

export default function InstanceApiKeyModal({ config, instanceName, onClose, onToast, onRevealApiKey }: Props) {
  const [data, setData] = useState<InstanceApiKey | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const apiKeyPreview = data?.maskedApiKey || 'No disponible'

  const load = async () => {
    setLoading(true)
    try {
      setData(await api.instances.getApiKey(config, instanceName))
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'No se pudo cargar API key', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const regenerate = async () => {
    if (!confirm(`Regenerar API key de "${instanceName}"?\n\nLas integraciones actuales dejaran de funcionar inmediatamente.`)) return
    setBusy(true)
    try {
      const payload = await api.instances.regenerateApiKey(config, instanceName)
      setData(payload)
      if (payload.apiKey) onRevealApiKey(payload.apiKey)
      onToast('API key regenerada', 'success')
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'No se pudo regenerar API key', 'error')
    } finally {
      setBusy(false)
    }
  }

  const revoke = async () => {
    if (!confirm(`Revocar API key de "${instanceName}"?\n\nSe bloqueara el acceso Bearer para esta instancia.`)) return
    setBusy(true)
    try {
      const payload = await api.instances.revokeApiKey(config, instanceName)
      setData(payload)
      onToast('API key revocada', 'success')
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'No se pudo revocar API key', 'error')
    } finally {
      setBusy(false)
    }
  }

  const enable = async () => {
    setBusy(true)
    try {
      const payload = await api.instances.enableApiKey(config, instanceName)
      setData(payload)
      if (payload.apiKey) onRevealApiKey(payload.apiKey)
      onToast('API key habilitada', 'success')
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : 'No se pudo habilitar API key', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-xl max-h-[calc(100vh-2rem)] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <h2 className="font-semibold text-sm flex items-center gap-2"><KeyRound size={14} /> API key de instancia</h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300"><X size={16} /></button>
        </div>
        <div className="px-5 py-5 space-y-4 overflow-y-auto">
          <p className="text-xs text-zinc-500">Instancia: <span className="font-mono text-zinc-300">{instanceName}</span></p>
          {loading ? <p className="text-sm text-zinc-400">Cargando...</p> : (
            <>
              <div className="rounded-lg border border-zinc-800 bg-zinc-950 px-4 py-3">
                <p className="text-xs text-zinc-500 mb-2">API key</p>
                <p className="text-sm text-zinc-300">
                  {data?.hasApiKey ? 'Generada. La clave completa no se muestra desde el panel.' : 'Sin clave generada.'}
                </p>
                {data?.hasApiKey && (
                  <p className="mt-2 text-sm font-mono text-zinc-200 break-all">
                    {apiKeyPreview}
                  </p>
                )}
              </div>
              <div className="text-xs text-zinc-500 space-y-1">
                <p>Estado: <span className={data?.enabled ? 'text-emerald-400' : 'text-red-400'}>{data?.enabled ? 'habilitada' : 'revocada'}</span></p>
                <p>Creada: {data?.createdAt || '-'}</p>
                <p>Ultimo uso: {data?.lastUsedAt || 'sin uso'}</p>
              </div>
            </>
          )}
          <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 p-3 text-xs text-amber-300 flex gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <p>Regenerar invalida la clave anterior en el acto. Actualiza tus bots/integraciones antes de volver a enviar mensajes.</p>
          </div>
        </div>
        <div className="px-5 py-4 border-t border-zinc-800 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            <button disabled={busy} onClick={regenerate} className="px-3 py-2 text-xs bg-blue-600 hover:bg-blue-500 rounded-md text-white disabled:opacity-50 flex items-center gap-1"><RefreshCcw size={12} />Regenerar</button>
            {data?.enabled ? (
              <button disabled={busy} onClick={revoke} className="px-3 py-2 text-xs bg-red-700/80 hover:bg-red-700 rounded-md text-white disabled:opacity-50 flex items-center gap-1"><ShieldOff size={12} />Revocar</button>
            ) : (
              <button disabled={busy} onClick={enable} className="px-3 py-2 text-xs bg-emerald-700 hover:bg-emerald-600 rounded-md text-white disabled:opacity-50 flex items-center gap-1"><CheckCircle2 size={12} />Habilitar</button>
            )}
          </div>
          <button onClick={onClose} className="px-3 py-2 text-xs border border-zinc-700 rounded-md hover:border-zinc-600">Cerrar</button>
        </div>
      </div>
    </div>
  )
}
