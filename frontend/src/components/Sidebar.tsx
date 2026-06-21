import { LayoutGrid, MessageSquare, Settings, Webhook, X, Zap } from 'lucide-react'

interface Props {
  onOpenSettings: () => void
  view: 'instances' | 'messages' | 'webhooks'
  onChangeView: (view: 'instances' | 'messages' | 'webhooks') => void
  mobileOpen: boolean
  onCloseMobile: () => void
}

const navItems = [
  { icon: LayoutGrid, label: 'Instancias', view: 'instances' as const },
  { icon: MessageSquare, label: 'Mensajes', view: 'messages' as const },
  { icon: Webhook, label: 'Webhooks', view: 'webhooks' as const },
]

export default function Sidebar({ onOpenSettings, view, onChangeView, mobileOpen, onCloseMobile }: Props) {
  const handleChangeView = (nextView: 'instances' | 'messages' | 'webhooks') => {
    onChangeView(nextView)
    onCloseMobile()
  }

  const navContent = (
    <>
      <div className="flex items-center justify-between gap-2 px-5 h-14 border-b border-zinc-800">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center shrink-0">
            <Zap size={14} className="text-white" />
          </div>
          <span className="font-semibold text-sm tracking-tight truncate">
            Botly <span className="text-zinc-500">Evolution</span>
          </span>
        </div>
        <button
          type="button"
          onClick={onCloseMobile}
          className="lg:hidden text-zinc-500 hover:text-zinc-300 transition-colors p-1 rounded-md hover:bg-zinc-800"
          aria-label="Cerrar menu"
        >
          <X size={16} />
        </button>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        <p className="px-2 mb-2 text-xs font-medium text-zinc-600 uppercase tracking-wider">
          WhatsApp
        </p>
        {navItems.map(({ icon: Icon, label, view: itemView }) => (
          <button
            key={label}
            onClick={() => handleChangeView(itemView)}
            className={`
              w-full flex items-center gap-2.5 px-2 py-2 rounded-md text-sm transition-colors
              ${view === itemView
                ? 'bg-zinc-800 text-zinc-50 font-medium'
                : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'}
            `}
          >
            <Icon size={15} />
            <span className="flex-1 text-left">{label}</span>
          </button>
        ))}
      </nav>

      <div className="px-3 py-4 border-t border-zinc-800">
        <button
          onClick={() => {
            onCloseMobile()
            onOpenSettings()
          }}
          className="w-full flex items-center gap-2.5 px-2 py-2 rounded-md text-sm text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50 transition-colors"
        >
          <Settings size={15} />
          Configuracion
        </button>
      </div>
    </>
  )

  return (
    <>
      <aside className="hidden lg:flex lg:flex-col lg:w-56 lg:shrink-0 lg:bg-zinc-900 lg:border-r lg:border-zinc-800 lg:h-screen lg:sticky lg:top-0">
        {navContent}
      </aside>

      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}

      <aside className={`lg:hidden fixed inset-y-0 left-0 z-50 w-[min(85vw,20rem)] bg-zinc-900 border-r border-zinc-800 flex flex-col transition-transform duration-200 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        {navContent}
      </aside>
    </>
  )
}
