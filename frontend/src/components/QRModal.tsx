import { useEffect, useCallback, useMemo, useState } from 'react'
import useSWR from 'swr'
import { X, RefreshCw, CheckCircle2, Loader2, RotateCcw } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import type { GatewayConfig } from '../lib/config'

interface Props {
  instanceName: string
  config: GatewayConfig
  onClose: () => void
  onConnected: () => void
}

export default function QRModal({ instanceName, config, onClose, onConnected }: Props) {
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [reconnecting, setReconnecting] = useState(false)

  const stateSwr = useSWR(
    ['state', instanceName],
    () => api.instances.state(config, instanceName),
    {
      refreshInterval: () => (document.hidden ? 0 : 4000),
      revalidateOnFocus: true,
      shouldRetryOnError: true,
      errorRetryInterval: 6000,
      onError: err => setErrorMessage(err instanceof ApiError ? err.message : 'Error consultando estado'),
    }
  )

  const state = stateSwr.data?.status

  const qrSwr = useSWR(
    state !== 'open' ? ['qr', instanceName] : null,
    () => api.instances.qr(config, instanceName),
    {
      refreshInterval: data => {
        if (document.hidden) return 0
        if (data?.nextRecommendedRefreshAt) {
          const now = Math.floor(Date.now() / 1000)
          if (data.nextRecommendedRefreshAt > now) {
            return Math.max(6000, (data.nextRecommendedRefreshAt - now) * 1000)
          }
        }
        return 8000
      },
      revalidateOnFocus: true,
      dedupingInterval: 2500,
      onError: err => setErrorMessage(err instanceof ApiError ? err.message : 'Error obteniendo QR'),
    }
  )

  const base64 = qrSwr.data?.base64 ?? qrSwr.data?.qrcode?.base64
  const isBusy = qrSwr.isValidating || stateSwr.isValidating || reconnecting

  useEffect(() => {
    if (state === 'open') onConnected()
  }, [state, onConnected])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleRefresh = useCallback(async () => {
    setErrorMessage(null)
    await qrSwr.mutate(() => api.instances.qr(config, instanceName, true), { revalidate: false })
  }, [config, instanceName, qrSwr])

  const handleReconnect = useCallback(async () => {
    setReconnecting(true)
    setErrorMessage(null)
    try {
      await api.instances.reconnect(config, instanceName)
      await Promise.all([stateSwr.mutate(), qrSwr.mutate()])
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : 'No se pudo reconectar la instancia')
    } finally {
      setReconnecting(false)
    }
  }, [config, instanceName, qrSwr, stateSwr])

  const subtitle = useMemo(() => {
    if (stateSwr.data?.stale) return 'estado en cache'
    if (state === 'connecting') return 'conectando'
    if (state === 'open') return 'conectado'
    return 'esperando escaneo'
  }, [state, stateSwr.data?.stale])

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in"
      onClick={e => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-sm max-h-[calc(100vh-2rem)] flex flex-col gap-0 overflow-hidden animate-slide-up">
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <div>
            <h2 className="font-semibold text-sm">Vincular numero</h2>
            <p className="text-xs text-zinc-500 font-mono mt-0.5">{instanceName} - {subtitle}</p>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors p-1 rounded-md hover:bg-zinc-800">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-5 overflow-y-auto">
          {state === 'open' ? (
            <div className="flex flex-col items-center gap-3 py-6">
              <CheckCircle2 size={48} className="text-emerald-400" />
              <p className="font-semibold text-emerald-400">Numero conectado</p>
              <p className="text-xs text-zinc-500 text-center">La instancia esta activa y lista para recibir mensajes.</p>
            </div>
          ) : base64 ? (
            <div className="bg-white rounded-xl p-3 flex items-center justify-center">
              <img
                src={base64.startsWith('data:') ? base64 : `data:image/png;base64,${base64}`}
                alt="QR Code"
                className="w-full max-w-[260px] rounded-lg"
              />
            </div>
          ) : (
            <div className="bg-zinc-800/50 rounded-xl h-72 flex flex-col items-center justify-center gap-3">
              <Loader2 size={24} className="animate-spin text-zinc-600" />
              <p className="text-xs text-zinc-500">Generando QR...</p>
            </div>
          )}
        </div>

        {state !== 'open' && (
          <div className="px-5 pb-5 flex flex-col gap-3">
            <div className="bg-zinc-800/50 rounded-lg px-4 py-3 text-xs text-zinc-400 leading-relaxed">
              Abri WhatsApp en tu celular, entra a Dispositivos vinculados, luego Vincular un dispositivo y escanea este QR.
            </div>
            {errorMessage && (
              <div className="bg-red-950/50 border border-red-900 rounded-lg px-3 py-2 text-xs text-red-300">
                {errorMessage}
              </div>
            )}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
              <button
                onClick={handleRefresh}
                disabled={isBusy}
                className="flex items-center justify-center gap-2 text-xs font-medium text-zinc-500 hover:text-zinc-300 transition-colors py-1 disabled:opacity-40"
              >
                <RefreshCw size={12} className={qrSwr.isValidating ? 'animate-spin' : ''} />
                Refrescar QR
              </button>
              <button
                onClick={handleReconnect}
                disabled={isBusy}
                className="flex items-center justify-center gap-2 text-xs font-medium text-zinc-400 hover:text-zinc-200 transition-colors py-1 disabled:opacity-40"
              >
                <RotateCcw size={12} className={reconnecting ? 'animate-spin' : ''} />
                Reconectar
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
