import { LayoutGrid, MessageSquare, Webhook, Settings, Zap } from 'lucide-react'

interface Props {
  onOpenSettings: () => void
  view: 'instances' | 'messages' | 'webhooks'
  onChangeView: (view: 'instances' | 'messages' | 'webhooks') => void
}

const navItems = [
  { icon: LayoutGrid, label: 'Instancias', view: 'instances' as const },
  { icon: MessageSquare, label: 'Mensajes', view: 'messages' as const },
  { icon: Webhook, label: 'Webhooks', view: 'webhooks' as const },
]

export default function Sidebar({ onOpenSettings, view, onChangeView }: Props) {
  return (
    <aside className="flex flex-col w-56 shrink-0 bg-zinc-900 border-r border-zinc-800 h-screen sticky top-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 h-14 border-b border-zinc-800">
        <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center">
          <Zap size={14} className="text-white" />
        </div>
        <span className="font-semibold text-sm tracking-tight">
          Botly <span className="text-zinc-500">Evolution</span>
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <p className="px-2 mb-2 text-xs font-medium text-zinc-600 uppercase tracking-wider">
          WhatsApp
        </p>
        {navItems.map(({ icon: Icon, label, view: itemView }) => (
          <button
            key={label}
            disabled={false}
            onClick={() => itemView && onChangeView(itemView)}
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

      {/* Bottom */}
      <div className="px-3 py-4 border-t border-zinc-800">
        <button
          onClick={onOpenSettings}
          className="w-full flex items-center gap-2.5 px-2 py-2 rounded-md text-sm text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50 transition-colors"
        >
          <Settings size={15} />
          Configuración
        </button>
      </div>
    </aside>
  )
}
