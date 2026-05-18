import { useEffect, useState } from 'react'
import { X, Eye, EyeOff, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import { saveConfig, type GatewayConfig } from '../lib/config'
import { api, ApiError } from '../lib/api'

interface Props {
  config:   GatewayConfig
  onClose:  () => void
  onChange: (cfg: GatewayConfig) => void
}

type TestStatus = 'idle' | 'loading' | 'ok' | 'error'

export default function SettingsModal({ config, onClose, onChange }: Props) {
  const [url,    setUrl]    = useState(config.url)
  const [publicBaseUrl, setPublicBaseUrl] = useState(config.publicBaseUrl || config.url)
  const [apiKey, setApiKey] = useState(config.apiKey)
  const [showKey, setShowKey] = useState(false)
  const [test, setTest]     = useState<TestStatus>('idle')
  const [testMsg, setTestMsg] = useState('')

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleTest = async () => {
    setTest('loading')
    try {
      await api.health({ url, apiKey, publicBaseUrl: publicBaseUrl || url })
      setTest('ok')
      setTestMsg('Gateway responde correctamente')
    } catch (e) {
      setTest('error')
      setTestMsg(e instanceof ApiError ? `Error ${e.status}: ${e.message}` : 'No se pudo conectar al gateway')
    }
  }

  const handleSave = () => {
    const cleanUrl = url.replace(/\/$/, '')
    const cleanPublicBaseUrl = publicBaseUrl.trim().replace(/\/$/, '') || cleanUrl
    const cfg: GatewayConfig = { url: cleanUrl, apiKey, publicBaseUrl: cleanPublicBaseUrl }
    saveConfig(cfg)
    onChange(cfg)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md flex flex-col overflow-hidden animate-slide-up">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <h2 className="font-semibold text-sm">Configuración</h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors p-1 rounded-md hover:bg-zinc-800">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex flex-col gap-5">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-400">Gateway URL</label>
            <input
              value={url}
              onChange={e => { setUrl(e.target.value); setTest('idle') }}
              placeholder="http://localhost:9000"
              className="bg-zinc-800 border border-zinc-700 focus:border-blue-500 focus:outline-none rounded-lg px-3 py-2.5 text-sm font-mono placeholder:text-zinc-600 transition-colors"
            />
            <p className="text-xs text-zinc-600">
              En producción: URL de Azure Container Apps / Railway / Render
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-400">Public Base URL</label>
            <input
              value={publicBaseUrl}
              onChange={e => setPublicBaseUrl(e.target.value)}
              placeholder="https://gateway.mi-dominio.com"
              className="bg-zinc-800 border border-zinc-700 focus:border-blue-500 focus:outline-none rounded-lg px-3 py-2.5 text-sm font-mono placeholder:text-zinc-600 transition-colors"
            />
            <p className="text-xs text-zinc-600">
              URL p&uacute;blica real para tus bots/integraciones. Si queda vac&iacute;a, usa Gateway URL.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-400">API Key</label>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => { setApiKey(e.target.value); setTest('idle') }}
                placeholder="tu GATEWAY_API_KEY"
                className="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 focus:outline-none rounded-lg px-3 py-2.5 pr-9 text-sm font-mono placeholder:text-zinc-600 transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowKey(v => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {/* Test result */}
          {test !== 'idle' && (
            <div className={`flex items-center gap-2 text-xs px-3 py-2.5 rounded-lg ${
              test === 'ok'      ? 'bg-emerald-950/50 text-emerald-400 border border-emerald-900' :
              test === 'error'   ? 'bg-red-950/50 text-red-400 border border-red-900' :
              'bg-zinc-800 text-zinc-400'
            }`}>
              {test === 'loading' && <Loader2 size={13} className="animate-spin" />}
              {test === 'ok'      && <CheckCircle2 size={13} />}
              {test === 'error'   && <AlertCircle size={13} />}
              {test === 'loading' ? 'Probando conexión…' : testMsg}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-5 py-4 border-t border-zinc-800">
          <button
            onClick={handleTest}
            disabled={test === 'loading'}
            className="flex items-center gap-2 px-3 py-2 text-xs font-medium text-zinc-400 hover:text-zinc-200 border border-zinc-700 hover:border-zinc-600 rounded-lg transition-colors disabled:opacity-50"
          >
            {test === 'loading' && <Loader2 size={12} className="animate-spin" />}
            Probar conexión
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors rounded-lg hover:bg-zinc-800"
            >
              Cancelar
            </button>
            <button
              onClick={handleSave}
              className="px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
            >
              Guardar
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
