import { CheckCircle2, CircleAlert, Instagram, RefreshCw, Unplug } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { Connection, InstagramReadiness } from '@/domain/connection'
import { environment } from '@/app/config/environment'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Toast } from '@/shared/components/Toast'
import { bindInstagramCoreChannel, disconnectInstagram, getInstagramReadiness, listInstagramCoreChannels, type CoreChannelOption } from '../api/connectionsApi'

function startAuthorize(connectionId: string) {
  const url = new URL('/connections/meta/instagram/authorize', environment.gatewayUrl || window.location.origin)
  url.searchParams.set('connection_id', connectionId); url.searchParams.set('ui_return', 'true')
  window.location.assign(url.toString())
}

function readinessCopy(readiness: InstagramReadiness | null): { label: string; tone: 'healthy' | 'attention' | 'pending' } {
  if (!readiness) return { label: 'Sin verificar', tone: 'pending' }
  if (readiness.ready) return { label: 'Ready', tone: 'healthy' }
  return { label: readiness.state === 'oauth_pending' ? 'Conexión pendiente' : 'Configuración pendiente', tone: 'attention' }
}

export function InstagramConnectionPanel({ connection, onConnectionChange }: { connection: Connection; onConnectionChange: (connection: Connection) => void }) {
  const [readiness, setReadiness] = useState<InstagramReadiness | null>(connection.readiness)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isDisconnectOpen, setIsDisconnectOpen] = useState(false)
  const [isDisconnecting, setIsDisconnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [coreChannels, setCoreChannels] = useState<CoreChannelOption[]>([])
  const [selectedCoreChannel, setSelectedCoreChannel] = useState(connection.coreChannel?.channelId || '')
  const [isBindingCoreChannel, setIsBindingCoreChannel] = useState(false)

  const refresh = useCallback(async () => {
    setIsRefreshing(true); setError(null)
    try { setReadiness(await getInstagramReadiness(connection.id)) } catch { setError('No se pudo actualizar el estado de Instagram.') } finally { setIsRefreshing(false) }
  }, [connection.id])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    let active = true
    void listInstagramCoreChannels(connection.id).then((channels) => { if (active) setCoreChannels(channels) }).catch(() => { if (active) setCoreChannels([]) })
    return () => { active = false }
  }, [connection.id])

  async function bindCoreChannel() {
    if (!selectedCoreChannel) return
    setIsBindingCoreChannel(true); setError(null)
    try { const updated = await bindInstagramCoreChannel(connection.id, selectedCoreChannel); onConnectionChange(updated); setNotice('Canal de Botly vinculado para inbound canonical.') }
    catch { setError('No se pudo vincular el canal de Botly. Intentá nuevamente.') }
    finally { setIsBindingCoreChannel(false) }
  }

  async function disconnect() {
    setIsDisconnecting(true); setError(null)
    try { const updated = await disconnectInstagram(connection.id); onConnectionChange(updated); setReadiness(updated.readiness); setNotice('La cuenta de Instagram fue desconectada.') }
    catch { setError('No se pudo desconectar Instagram. Intentá nuevamente.') }
    finally { setIsDisconnecting(false); setIsDisconnectOpen(false) }
  }

  const account = connection.providerAccount?.metadata || {}
  const state = readinessCopy(readiness)
  const isConnected = connection.status.state === 'connected' && !!connection.providerAccount
  const messageApiUrl = readiness?.ready
    ? new URL(`/connections/${encodeURIComponent(connection.id)}/instagram/messages`, environment.gatewayUrl || window.location.origin).toString()
    : null
  return <section className="connection-section instagram-connection-panel">
    <Toast message={error} tone="error" onDismiss={() => setError(null)} />
    <Toast message={notice} tone="success" onDismiss={() => setNotice(null)} />
    <div className="connection-section-heading"><div><div className="instagram-title"><Instagram size={20} aria-hidden="true" /><h3>Instagram</h3></div><p>{account.username ? `@${account.username}` : account.displayName || 'Cuenta profesional pendiente de conexión'}</p></div><StatusBadge tone={state.tone}>{state.label}</StatusBadge></div>
    <dl className="connection-information-list instagram-status-list"><div><dt>Cuenta</dt><dd>{isConnected ? 'Conectada' : 'No conectada'}</dd></div><div><dt>Provider account ID</dt><dd>{connection.providerAccount?.providerAccountId || 'No vinculada'}</dd></div><div><dt>Readiness</dt><dd>{state.label}</dd></div><div><dt>Canal de Botly</dt><dd>{connection.coreChannel?.name || (connection.coreChannel?.configured ? 'Canal vinculado' : 'No vinculado')}</dd></div></dl>
    {messageApiUrl ? <div className="connection-endpoint"><span>Ruta inbound</span><code>Meta → Gateway → CanonicalInboundEvent → Core</code><p className="connection-endpoint-note">La entrega usa el canal Core vinculado; no usa forwarding a webhooks de instancia.</p></div> : null}
    {readiness ? <ul className="instagram-readiness-list">{[
      ['Cuenta conectada', readiness.authenticated], ['Credenciales configuradas', readiness.credentialValid], ['Cuenta profesional detectada', readiness.accountDiscovered], ['Scopes requeridos', readiness.requiredScopesPresent],
    ].filter(([, value]) => value !== undefined).map(([label, value]) => <li key={String(label)}>{value ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}<span>{label}</span></li>)}</ul> : null}
    {!isConnected ? <button type="button" className="client-button-primary" onClick={() => startAuthorize(connection.id)}>Conectar con Instagram</button> : null}
    <p className="connection-endpoint-note">Los eventos inbound se entregan exclusivamente a Core mediante el contrato canonical.</p>
    <div className="instagram-channel-binding"><h4>Canal de Botly Core</h4><p>Determina el tenant y la credencial con que Gateway entrega eventos canonical a Core.</p><select value={selectedCoreChannel} onChange={(event) => setSelectedCoreChannel(event.target.value)} disabled={isBindingCoreChannel}><option value="">Seleccioná un canal</option>{coreChannels.map((channel) => <option key={channel.id} value={channel.id}>{channel.name} · {channel.status}</option>)}</select><button type="button" className="client-button-secondary" disabled={!selectedCoreChannel || isBindingCoreChannel || selectedCoreChannel === connection.coreChannel?.channelId} onClick={() => void bindCoreChannel()}>{isBindingCoreChannel ? 'Vinculando…' : 'Vincular canal'}</button>{!coreChannels.length ? <p className="connection-endpoint-note">No se encontraron canales elegibles de Core para este cliente.</p> : null}</div>
    <div className="connection-inline-actions"><button type="button" className="client-button-secondary" disabled={isRefreshing} onClick={() => void refresh()}><RefreshCw size={15} className={isRefreshing ? 'animate-spin' : ''} /> Actualizar estado</button>{isConnected ? <button type="button" className="client-button-secondary" onClick={() => startAuthorize(connection.id)}>Reautorizar Instagram</button> : null}{isConnected ? <button type="button" className="client-button-danger" onClick={() => setIsDisconnectOpen(true)}><Unplug size={15} /> Desconectar</button> : null}</div>
    <ConfirmDialog isOpen={isDisconnectOpen} title="¿Desconectar esta cuenta de Instagram?" description="Se revocará el vínculo de integración y esta conexión dejará de recibir eventos. No se borrará historial de negocio." confirmLabel="Desconectar Instagram" isSubmitting={isDisconnecting} onCancel={() => setIsDisconnectOpen(false)} onConfirm={() => void disconnect()} />
  </section>
}
