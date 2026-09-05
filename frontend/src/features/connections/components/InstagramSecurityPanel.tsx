import { ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { Connection } from '@/domain/connection'
import { environment } from '@/app/config/environment'
import { getInstagramReadiness } from '../api/connectionsApi'

function reauthorize(connectionId: string) {
  const url = new URL('/connections/meta/instagram/authorize', environment.gatewayUrl || window.location.origin)
  url.searchParams.set('connection_id', connectionId)
  url.searchParams.set('ui_return', 'true')
  window.location.assign(url.toString())
}

export function InstagramSecurityPanel({ connection }: { connection: Connection }) {
  const [readiness, setReadiness] = useState(connection.readiness)
  useEffect(() => { void getInstagramReadiness(connection.id).then(setReadiness).catch(() => undefined) }, [connection.id])
  const needsReauthorization = !readiness?.ready
  return <section className="connection-section workspace-security"><div className="connection-section-heading"><div><h3><ShieldCheck size={18} /> Seguridad de Instagram</h3><p>Tokens y secretos permanecen cifrados en Gateway; no se exponen al navegador.</p></div>{needsReauthorization ? <button type="button" className="client-button-primary" onClick={() => reauthorize(connection.id)}>Reautorizar Instagram</button> : null}</div><dl className="connection-information-list"><div><dt>Cuenta profesional</dt><dd>{connection.providerAccount ? 'Configurada' : 'No configurada'}</dd></div><div><dt>Credencial OAuth</dt><dd>{readiness?.credentialValid ? 'Activa' : readiness?.state === 'expired' ? 'Expirada: requiere reautorización' : 'No disponible o requiere reconexión'}</dd></div><div><dt>Scopes requeridos</dt><dd>{readiness?.requiredScopesPresent ? 'Presentes' : readiness?.missingScopes?.length ? `Pendientes: ${readiness.missingScopes.join(', ')}` : 'Pendientes'}</dd></div><div><dt>Webhook Meta</dt><dd>Firmado y validado por Gateway</dd></div></dl></section>
}
