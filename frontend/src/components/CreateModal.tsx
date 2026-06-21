import { useEffect, useRef, useState } from 'react'
import { X, Loader2 } from 'lucide-react'

interface Props {
  onClose:  () => void
  onCreate: (name: string) => Promise<void>
}

export default function CreateModal({ onClose, onCreate }: Props) {
  const [name, setName]       = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleSubmit = async () => {
    const trimmed = name.trim()
    if (!trimmed) { setError('El nombre no puede estar vacío'); return }
    if (!/^[a-z0-9_]{1,64}$/.test(trimmed)) {
      setError('Solo minúsculas, números y guion bajo (máx. 64 caracteres)')
      return
    }
    setLoading(true)
    setError('')
    try {
      await onCreate(trimmed)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al crear la instancia')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md max-h-[calc(100vh-2rem)] flex flex-col gap-0 overflow-hidden animate-slide-up">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <h2 className="font-semibold text-sm">Nueva instancia</h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors p-1 rounded-md hover:bg-zinc-800">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-400">
              Nombre de la instancia
            </label>
            <input
              ref={inputRef}
              value={name}
              onChange={e => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
              onKeyDown={e => { if (e.key === 'Enter') handleSubmit() }}
              placeholder="acme_support"
              className="bg-zinc-800 border border-zinc-700 focus:border-blue-500 focus:outline-none rounded-lg px-3 py-2.5 text-sm font-mono placeholder:text-zinc-600 transition-colors"
            />
            {error
              ? <p className="text-xs text-red-400">{error}</p>
              : <p className="text-xs text-zinc-600">Solo minúsculas, números y guion bajo. Ej: <span className="font-mono">acme_support</span></p>
            }
          </div>

          <div className="bg-zinc-800/50 rounded-lg px-4 py-3 flex flex-col gap-1">
            <p className="text-xs font-medium text-zinc-400">Integración</p>
            <p className="text-xs text-zinc-500">WhatsApp via Baileys — una instancia = un número</p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex flex-col-reverse sm:flex-row items-stretch sm:items-center justify-end gap-2 px-5 py-4 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors rounded-lg hover:bg-zinc-800"
          >
            Cancelar
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !name}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
          >
            {loading && <Loader2 size={13} className="animate-spin" />}
            {loading ? 'Creando…' : 'Crear instancia'}
          </button>
        </div>
      </div>
    </div>
  )
}
