import { LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '@/app/providers/AuthProvider'
import { Input } from '@/shared/components/FormControls'

interface GoogleAccounts {
  id: {
    initialize: (config: { client_id: string; callback: (response: { credential?: string }) => void }) => void
    renderButton: (element: HTMLElement, options: Record<string, string | number>) => void
  }
}

declare global {
  interface Window { google?: { accounts?: GoogleAccounts } }
}

let googleScript: Promise<GoogleAccounts> | null = null

function loadGoogleAccounts(): Promise<GoogleAccounts> {
  if (window.google?.accounts) return Promise.resolve(window.google.accounts)
  if (googleScript) return googleScript
  googleScript = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = () => window.google?.accounts ? resolve(window.google.accounts) : reject(new Error('Google unavailable'))
    script.onerror = () => reject(new Error('Google unavailable'))
    document.head.appendChild(script)
  })
  return googleScript
}

export function LoginPage() {
  const host = useRef<HTMLDivElement>(null)
  const { user, googleClientId, googleConfigUnavailable, isLoading, accessDenied, signInWithGoogle, signInWithEmail } = useAuth()
  const [isGoogleReady, setIsGoogleReady] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    if (!googleClientId || !host.current) return
    void loadGoogleAccounts().then((accounts) => {
      if (!active || !host.current) return
      accounts.id.initialize({ client_id: googleClientId, callback: (response) => { if (response.credential) void signInWithGoogle(response.credential) } })
      accounts.id.renderButton(host.current, { type: 'standard', theme: 'filled_black', size: 'large', text: 'continue_with', shape: 'rectangular', width: 280 })
      setIsGoogleReady(true)
    }).catch(() => { if (active) setIsGoogleReady(false) })
    return () => { active = false }
  }, [googleClientId, signInWithGoogle])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    try { await signInWithEmail(email, password) } catch { setError('Email o contraseña incorrectos.') }
  }

  if (user) return <Navigate to={user.role === 'meta_reviewer' ? '/clients' : '/dashboard'} replace />
  if (accessDenied) return <Navigate to="/access-denied" replace />

  return <main className="auth-page">
    <section className="auth-login" aria-label="Acceso a Botly Gateway">
      <div className="auth-brand">
        <div className="auth-logo-frame">
          <img className="auth-logo" src="/logo-gateway-mark.svg" alt="" />
        </div>
        <span className="auth-eyebrow">BOTLY · GATEWAY</span>
        <h1>Ingresá a tu cuenta</h1>
        <p>Gestioná tus conexiones de WhatsApp desde un solo lugar.</p>
      </div>

      <form className="auth-password-form" onSubmit={(event) => void submit(event)}>
        <div className="auth-field">
          <label htmlFor="login-email">Email</label>
          <Input id="login-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" placeholder="tu@email.com" required />
        </div>
        <div className="auth-field">
          <label htmlFor="login-password">Contraseña</label>
          <Input id="login-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="••••••••" required />
        </div>
        <button className="auth-submit" type="submit" disabled={isLoading}>
          {isLoading ? <LoaderCircle size={17} className="animate-spin" aria-hidden="true" /> : null}
          <span>{isLoading ? 'Ingresando…' : 'Ingresar'}</span>
        </button>
      </form>

      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {googleClientId ? <>
        <div className="auth-divider" aria-hidden="true"><span>o continuar con</span></div>
        <div className="auth-google-area">
          <div ref={host} className="auth-google-button" aria-label="Continuar con Google" />
          {!isGoogleReady && !isLoading ? <LoaderCircle size={18} className="auth-loading animate-spin" aria-label="Cargando Google" /> : null}
        </div>
      </> : <p className="auth-unavailable">{googleConfigUnavailable ? 'No se pudo cargar la autenticación con Google. Revisá la conexión con el Gateway.' : 'Google no está configurado.'}</p>}
      <p className="auth-private">Acceso privado · no hay registro público</p>
    </section>
  </main>
}
