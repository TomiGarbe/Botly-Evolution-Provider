import { useState } from 'react'
import { Check, Copy, KeyRound, X } from 'lucide-react'

interface Props {
  apiKey: string
  instanceName: string
  title: string
  description: string
  onClose: () => void
  onToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

export default function ApiKeyRevealModal({
  apiKey,
  instanceName,
  title,
  description,
  onClose,
  onToast,
}: Props) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(apiKey)
      setCopied(true)
      onToast('API key copiada', 'success')
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      onToast('No se pudo copiar la API key', 'error')
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-[60] p-4"
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-xl max-h-[calc(100vh-2rem)] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <h2 className="font-semibold text-sm flex items-center gap-2"><KeyRound size={14} /> {title}</h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300"><X size={16} /></button>
        </div>

        <div className="px-5 py-5 space-y-4 overflow-y-auto">
          <div className="space-y-1">
            <p className="text-xs text-zinc-500">Instancia</p>
            <p className="text-sm font-mono text-zinc-200">{instanceName}</p>
          </div>

          <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 p-3 text-xs text-amber-300">
            {description}
          </div>

          <div className="rounded-lg border border-zinc-800 bg-zinc-950 px-4 py-3 space-y-2">
            <p className="text-xs text-zinc-500">API key completa</p>
            <p className="text-sm font-mono text-zinc-100 break-all">{apiKey}</p>
          </div>
        </div>

        <div className="px-5 py-4 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-3">
          <button onClick={onClose} className="px-3 py-2 text-xs border border-zinc-700 rounded-md hover:border-zinc-600">Cerrar</button>
          <button
            onClick={handleCopy}
            className="px-3 py-2 text-xs bg-blue-600 hover:bg-blue-500 rounded-md text-white flex items-center justify-center gap-1.5"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? 'Copiada' : 'Copiar API key'}
          </button>
        </div>
      </div>
    </div>
  )
}
