import { CircleAlert, CheckCircle2, RefreshCw, Unplug } from 'lucide-react'
import type { Connection } from '@/domain/connection'
import { StatusBadge } from '@/shared/components/StatusBadge'

function readiness(connection: Connection): { label: string; tone: 'healthy' | 'attention' | 'pending' } {
  const value = connection.readiness
  if (!value) return connection.status.health === 'unhealthy' || connection.status.state === 'disconnected'
    ? { label: 'Error', tone: 'attention' }
    : { label: 'Sin verificar', tone: 'pending' }
  if (value.ready && (value.coreDeliveryReady === undefined || value.coreDeliveryReady)) return { label: 'Lista', tone: 'healthy' }
  return { label: value.state === 'expired' ? 'Requiere atención' : 'Configuración pendiente', tone: 'attention' }
}

export function ConnectionOverview({ connection, onAuthorize, onDisconnect, isRefreshing, onRefresh }: { connection: Connection; onAuthorize?: () => void; onDisconnect?: () => void; isRefreshing?: boolean; onRefresh: () => void }) {
  const account = connection.providerAccount?.metadata || {}; const state = readiness(connection); const hasAccount = Boolean(connection.providerAccount)
  return <section className="connection-section connection-overview">
    <div className="connection-section-heading"><div><h3>Configuración de la conexión</h3><p>Estado operativo y cuenta asociada.</p></div><StatusBadge tone={state.tone}>{state.label}</StatusBadge></div>
    <dl className="connection-information-list"><div><dt>Cuenta</dt><dd>{hasAccount ? (account.username ? `@${account.username}` : account.displayName || 'Configurada') : 'Pendiente de conexión'}</dd></div>{connection.providerAccount?.providerAccountId ? <div><dt>Identificador externo</dt><dd><code>{connection.providerAccount.providerAccountId}</code></dd></div> : null}<div><dt>Readiness</dt><dd>{state.label}</dd></div></dl>
    {connection.readiness ? <ul className="connection-readiness-list">{[
      ['Autenticación', connection.readiness.authenticated], ['Credenciales', connection.readiness.credentialValid], ['Cuenta detectada', connection.readiness.accountDiscovered], ['Permisos requeridos', connection.readiness.requiredScopesPresent],
    ].filter(([, value]) => value !== undefined).map(([label, value]) => <li key={String(label)}>{value ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}<span>{label}</span></li>)}</ul> : null}
    <div className="connection-inline-actions"><button type="button" className="client-button-secondary" onClick={onRefresh} disabled={isRefreshing}><RefreshCw size={15} className={isRefreshing ? 'animate-spin' : ''} /> Actualizar estado</button>{onAuthorize ? <button type="button" className="client-button-primary" onClick={onAuthorize}>{hasAccount ? 'Reautorizar' : 'Conectar'}</button> : null}{onDisconnect && hasAccount ? <button type="button" className="client-button-danger" onClick={onDisconnect}><Unplug size={15} /> Desconectar</button> : null}</div>
  </section>
}
