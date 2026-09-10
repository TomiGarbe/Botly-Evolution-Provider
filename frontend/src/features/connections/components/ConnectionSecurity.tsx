import { ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { Connection } from '@/domain/connection'
import { getConnectionDiagnostics, type ConnectionDiagnosticCheck } from '../api/connectionOperationsApi'

const securityCodes = new Set(['oauth', 'scopes', 'meta_webhook', 'token', 'meta_api', 'provider_account'])
function label(status: ConnectionDiagnosticCheck['status']): string { return ({ healthy: 'Activa', degraded: 'Requiere atención', unhealthy: 'No disponible', unknown: 'Sin verificar' })[status] }

export function ConnectionSecurity({ connection, onAuthorize }: { connection: Connection; onAuthorize?: () => void }) {
  const [checks, setChecks] = useState<ConnectionDiagnosticCheck[]>([]); const [error, setError] = useState(false)
  const load = useCallback(async () => { try { const result = await getConnectionDiagnostics(connection.id); setChecks(result.checks.filter((item) => securityCodes.has(item.code))); setError(false) } catch { setError(true) } }, [connection.id])
  useEffect(() => { void load() }, [load])
  return <section className="connection-section workspace-security"><div className="connection-section-heading"><div><h3><ShieldCheck size={18} /> Seguridad</h3><p>Estado de autenticación, credenciales y seguridad del webhook.</p></div>{onAuthorize ? <button type="button" className="client-button-secondary" onClick={onAuthorize}>Reautorizar</button> : null}</div>{error ? <p className="connection-endpoint-note">No se pudo cargar el estado de seguridad.</p> : null}<dl className="connection-information-list">{checks.map((check) => <div key={check.code}><dt>{check.label}</dt><dd>{label(check.status)}</dd></div>)}{!error && !checks.length ? <div><dt>Estado</dt><dd>Sin checks específicos para esta conexión</dd></div> : null}</dl></section>
}
