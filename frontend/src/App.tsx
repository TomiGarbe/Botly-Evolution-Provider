import { useState, useCallback, useEffect } from 'react'
import useSWR from 'swr'
import { Menu, Plus, RefreshCw, Server } from 'lucide-react'
import Sidebar from './components/Sidebar'
import InstanceCard from './components/InstanceCard'
import QRModal from './components/QRModal'
import CreateModal from './components/CreateModal'
import SettingsModal from './components/SettingsModal'
import MediaLab from './components/MediaLab'
import InstanceApiKeyModal from './components/InstanceApiKeyModal'
import WebhooksManager from './components/WebhooksManager'
import ApiKeyRevealModal from './components/ApiKeyRevealModal'
import { api, ApiError } from './lib/api'
import { loadConfig, type GatewayConfig } from './lib/config'
import type { Toast } from './types'

function Toasts({ toasts, remove }: { toasts: Toast[]; remove: (id: string) => void }) {
  return (
    <div className="fixed bottom-20 lg:bottom-4 right-4 left-4 sm:left-auto flex flex-col gap-2 z-[200] pointer-events-none">
      {toasts.map(t => (
        <div
          key={t.id}
          onClick={() => remove(t.id)}
          className={`
            pointer-events-auto flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-medium
            border shadow-xl cursor-pointer animate-slide-up
            ${t.type === 'success' ? 'bg-zinc-900 border-emerald-800 text-emerald-400' :
              t.type === 'error' ? 'bg-zinc-900 border-red-900 text-red-400' :
              'bg-zinc-900 border-zinc-700 text-zinc-300'}
          `}
        >
          {t.message}
        </div>
      ))}
    </div>
  )
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-5">
      <div className="w-16 h-16 rounded-2xl bg-zinc-800 flex items-center justify-center">
        <Server size={28} className="text-zinc-600" />
      </div>
      <div className="text-center">
        <p className="font-semibold text-zinc-200">Sin instancias</p>
        <p className="text-sm text-zinc-500 mt-1">Crea una instancia para vincular un numero de WhatsApp</p>
      </div>
      <button
        onClick={onAdd}
        className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
      >
        <Plus size={15} />
        Nueva instancia
      </button>
    </div>
  )
}

export default function App() {
  const [config, setConfig] = useState<GatewayConfig>(loadConfig)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [qrTarget, setQrTarget] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [apiKeyTarget, setApiKeyTarget] = useState<string | null>(null)
  const [view, setView] = useState<'instances' | 'messages' | 'webhooks'>('instances')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [revealedApiKey, setRevealedApiKey] = useState<{ instanceName: string; apiKey: string; title: string; description: string } | null>(null)

  useEffect(() => {
    if (!config.apiKey) setShowSettings(true)
  }, [config.apiKey])

  useEffect(() => {
    document.body.classList.toggle('overflow-hidden', mobileNavOpen)
    return () => document.body.classList.remove('overflow-hidden')
  }, [mobileNavOpen])

  const addToast = useCallback((message: string, type: Toast['type'] = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setToasts(ts => [...ts, { id, message, type }])
    setTimeout(() => setToasts(ts => ts.filter(t => t.id !== id)), 4000)
  }, [])

  const removeToast = useCallback((id: string) => {
    setToasts(ts => ts.filter(t => t.id !== id))
  }, [])

  const { data: instances, isLoading, isValidating, mutate, error: instancesError } = useSWR(
    config.apiKey ? 'instances' : null,
    () => api.instances.list(config),
    {
      refreshInterval: () => (document.hidden ? 30000 : 12000),
      revalidateOnFocus: true,
      dedupingInterval: 4000,
      shouldRetryOnError: true,
      errorRetryInterval: 8000,
    }
  )

  const handleCreate = useCallback(async (name: string) => {
    const created = await api.instances.create(config, name)
    addToast(`Instancia "${name}" creada`, 'success')
    await mutate()
    setShowCreate(false)
    if (created.apiKey) {
      setRevealedApiKey({
        instanceName: name,
        apiKey: created.apiKey,
        title: 'API key creada',
        description: 'Guardala ahora. Esta es la unica vez que el panel muestra la API key completa al crear la instancia.',
      })
    }
    setQrTarget(name)
  }, [config, mutate, addToast])

  const handleLogout = useCallback(async (name: string) => {
    if (!confirm(`Desconectar "${name}" de WhatsApp?\n\nHabra que re-escanear el QR para volver a conectar.`)) return
    try {
      await api.instances.logout(config, name)
      addToast(`"${name}" desconectado`, 'success')
      mutate()
    } catch (e) {
      addToast(e instanceof ApiError ? e.message : 'Error al desconectar', 'error')
    }
  }, [config, mutate, addToast])

  const handleDelete = useCallback(async (name: string) => {
    if (!confirm(`Eliminar la instancia "${name}"?\n\nEsta accion no se puede deshacer.`)) return
    try {
      await api.instances.delete(config, name)
      addToast(`Instancia "${name}" eliminada`, 'success')
      mutate()
    } catch (e) {
      addToast(e instanceof ApiError ? e.message : 'Error al eliminar', 'error')
    }
  }, [config, mutate, addToast])

  const handleConnected = useCallback(() => {
    addToast('Numero conectado exitosamente', 'success')
    setQrTarget(null)
    mutate()
  }, [addToast, mutate])

  const list = Array.isArray(instances) ? instances : []

  return (
    <div className="flex min-h-screen bg-zinc-950">
      <Sidebar
        onOpenSettings={() => setShowSettings(true)}
        view={view}
        onChangeView={setView}
        mobileOpen={mobileNavOpen}
        onCloseMobile={() => setMobileNavOpen(false)}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center justify-between gap-3 px-4 sm:px-6 lg:px-8 min-h-14 py-3 border-b border-zinc-800 shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setMobileNavOpen(true)}
                className="lg:hidden inline-flex items-center justify-center w-9 h-9 rounded-lg border border-zinc-800 text-zinc-300 hover:bg-zinc-900"
                aria-label="Abrir menu"
              >
                <Menu size={16} />
              </button>
              <h1 className="font-semibold text-sm">{view === 'instances' ? 'Instancias' : view === 'messages' ? 'Mensajes' : 'Webhooks'}</h1>
            </div>
            {!isLoading && view === 'instances' && (
              <p className="text-xs text-zinc-500 mt-0.5 ml-12 lg:ml-0">
                {list.length} {list.length === 1 ? 'instancia' : 'instancias'} totales
              </p>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {view === 'instances' ? (
              <>
                <button
                  onClick={() => mutate()}
                  disabled={isValidating}
                  className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 text-xs text-zinc-500 hover:text-zinc-300 border border-zinc-800 hover:border-zinc-700 rounded-lg transition-colors disabled:opacity-40"
                >
                  <RefreshCw size={12} className={isValidating ? 'animate-spin' : ''} />
                  <span className="hidden sm:inline">Actualizar</span>
                </button>
                <button
                  onClick={() => setShowCreate(true)}
                  className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
                >
                  <Plus size={13} />
                  <span className="hidden sm:inline">Nueva instancia</span>
                </button>
              </>
            ) : null}
          </div>
        </header>

        <main className="flex-1 px-4 sm:px-6 lg:px-8 py-4 sm:py-6 pb-24 lg:pb-6">
          {view === 'messages' ? (
            <MediaLab
              config={config}
              instances={list}
              instancesLoading={isLoading}
              instancesError={instancesError instanceof Error ? instancesError.message : undefined}
              onToast={addToast}
            />
          ) : view === 'webhooks' ? (
            <WebhooksManager config={config} instances={list} onToast={addToast} />
          ) : isLoading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 h-36 animate-pulse" />
              ))}
            </div>
          ) : !config.apiKey ? (
            <div className="flex flex-col items-center justify-center py-24 gap-4">
              <p className="text-zinc-500 text-sm">Configura el gateway para empezar</p>
              <button
                onClick={() => setShowSettings(true)}
                className="px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
              >
                Abrir configuracion
              </button>
            </div>
          ) : list.length === 0 ? (
            <EmptyState onAdd={() => setShowCreate(true)} />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {list.map(inst => (
                <InstanceCard
                  key={inst.id}
                  instance={inst}
                  config={config}
                  onToast={addToast}
                  onQR={setQrTarget}
                  onLogout={handleLogout}
                  onDelete={handleDelete}
                  onApiKey={setApiKeyTarget}
                />
              ))}
            </div>
          )}
        </main>
      </div>

      {qrTarget && (
        <QRModal
          instanceName={qrTarget}
          config={config}
          onClose={() => setQrTarget(null)}
          onConnected={handleConnected}
        />
      )}
      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreate={handleCreate}
        />
      )}
      {showSettings && (
        <SettingsModal
          config={config}
          onClose={() => setShowSettings(false)}
          onChange={setConfig}
        />
      )}
      {apiKeyTarget && (
        <InstanceApiKeyModal
          config={config}
          instanceName={apiKeyTarget}
          onClose={() => setApiKeyTarget(null)}
          onToast={addToast}
          onRevealApiKey={apiKey =>
            setRevealedApiKey({
              instanceName: apiKeyTarget,
              apiKey,
              title: 'API key regenerada',
              description: 'La clave anterior ya no sirve. Copia esta nueva API key y actualiza tus integraciones antes de seguir.',
            })
          }
        />
      )}
      {revealedApiKey && (
        <ApiKeyRevealModal
          apiKey={revealedApiKey.apiKey}
          instanceName={revealedApiKey.instanceName}
          title={revealedApiKey.title}
          description={revealedApiKey.description}
          onClose={() => setRevealedApiKey(null)}
          onToast={addToast}
        />
      )}

      <Toasts toasts={toasts} remove={removeToast} />

      <nav className="lg:hidden fixed inset-x-0 bottom-0 z-30 border-t border-zinc-800 bg-zinc-950/95 backdrop-blur px-2 py-2">
        <div className="grid grid-cols-4 gap-2">
          <button
            type="button"
            onClick={() => setView('instances')}
            className={`rounded-lg px-3 py-2 text-xs ${view === 'instances' ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-500'}`}
          >
            Instancias
          </button>
          <button
            type="button"
            onClick={() => setView('messages')}
            className={`rounded-lg px-3 py-2 text-xs ${view === 'messages' ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-500'}`}
          >
            Mensajes
          </button>
          <button
            type="button"
            onClick={() => setView('webhooks')}
            className={`rounded-lg px-3 py-2 text-xs ${view === 'webhooks' ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-500'}`}
          >
            Webhooks
          </button>
          <button
            type="button"
            onClick={() => setShowSettings(true)}
            className="rounded-lg px-3 py-2 text-xs text-zinc-500"
          >
            Ajustes
          </button>
        </div>
      </nav>
    </div>
  )
}
