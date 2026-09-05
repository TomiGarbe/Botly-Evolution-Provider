import { ArrowLeft, Clipboard, Eye, EyeOff, Pencil, RefreshCw, Trash2 } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import type { Connection } from '@/domain/connection'
import { StatusBadge, type StatusTone } from '@/shared/components/StatusBadge'
import { LoadingState } from '@/shared/components/LoadingState'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { Toast } from '@/shared/components/Toast'
import { Field, Input } from '@/shared/components/FormControls'
import { ConnectionApiKey, ConnectionIntegrationEndpoints, ConnectionStatusSummary, getConnectionApiKey, getConnectionIntegrationEndpoints, getConnectionStatusSummary, reconnectConnection, regenerateConnectionApiKey } from '../api/connectionOperationsApi'
import { deleteConnection, getConnection, updateConnectionName } from '../api/connectionsApi'
import { MessagesWorkspace } from '../components/MessagesWorkspace'
import { ConnectionWebhooks } from '../components/ConnectionWebhooks'
import { OperationsDiagnostics } from '../components/OperationsDiagnostics'

type WorkspaceTab = 'general' | 'security' | 'messages' | 'webhooks'
const tabs: Array<{ id: WorkspaceTab; label: string }> = [{ id: 'general', label: 'General' }, { id: 'security', label: 'Seguridad' }, { id: 'messages', label: 'Mensajes' }, { id: 'webhooks', label: 'Webhooks' }]

function workspaceState(connection: Connection, status: ConnectionStatusSummary | null): { label: string; tone: StatusTone } {
  if (status?.connected || connection.status.health === 'healthy') return { label: 'Operativa', tone: 'healthy' }
  if (connection.status.state === 'pending') return { label: 'Pendiente', tone: 'pending' }
  if (connection.status.state === 'connecting') return { label: 'Configurando', tone: 'configuring' }
  if (connection.status.health === 'unhealthy' || connection.status.state === 'disconnected') return { label: 'Problema crítico', tone: 'critical' }
  return { label: 'Atención requerida', tone: 'attention' }
}
function dateTime(value: string | null | undefined, fallback = 'Sin actividad registrada'): string {
  if (!value) return fallback
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat('es-AR', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function ConnectionDetailPage() {
  const navigate = useNavigate(); const location = useLocation(); const { connectionId } = useParams()
  const [connection, setConnection] = useState<Connection | null>(null)
  const [integrationEndpoints, setIntegrationEndpoints] = useState<ConnectionIntegrationEndpoints | null>(null)
  const [apiKey, setApiKey] = useState<ConnectionApiKey | null>(null)
  const [statusSummary, setStatusSummary] = useState<ConnectionStatusSummary | null>(null)
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('general')
  const [isLoading, setIsLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null)
  const [isEditingName, setIsEditingName] = useState(false); const [name, setName] = useState(''); const [isSaving, setIsSaving] = useState(false)
  const [showKey, setShowKey] = useState(false); const [isDeleting, setIsDeleting] = useState(false); const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false); const [isRegenerateDialogOpen, setIsRegenerateDialogOpen] = useState(false)

  const loadConnection = useCallback(async () => {
    if (!connectionId) return
    setError(null)
    try { const loaded = await getConnection(connectionId); setConnection(loaded); setName(loaded.name) } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo cargar la conexión.') } finally { setIsLoading(false) }
  }, [connectionId])
  const loadOperations = useCallback(async () => {
    if (!connectionId) return
    try {
      const [nextApiKey, nextStatus, nextEndpoints] = await Promise.all([getConnectionApiKey(connectionId), getConnectionStatusSummary(connectionId), getConnectionIntegrationEndpoints(connectionId)])
      setApiKey(nextApiKey); setStatusSummary(nextStatus); setIntegrationEndpoints(nextEndpoints)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo cargar la operación de la conexión.') }
  }, [connectionId])
  useEffect(() => { void loadConnection() }, [loadConnection])
  useEffect(() => { if (connection) void loadOperations() }, [connection, loadOperations])
  useEffect(() => {
    if (location.pathname.endsWith('/webhooks')) setActiveTab('webhooks')
    else { const requested = new URLSearchParams(location.search).get('tab'); setActiveTab(tabs.some((tab) => tab.id === requested) ? requested as WorkspaceTab : 'general') }
  }, [location.pathname, location.search])

  async function refreshWorkspace() { await loadConnection(); await loadOperations() }
  async function saveName(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!connection) return; setError(null); setIsSaving(true); try { setConnection(await updateConnectionName(connection.id, name)); setIsEditingName(false) } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo actualizar la conexión.') } finally { setIsSaving(false) } }
  async function regenerateKey() { if (!connection) return; setError(null); try { const updated = await regenerateConnectionApiKey(connection.id); setApiKey(updated); setShowKey(true); setNotice('Nueva API Key generada. Podés mostrarla o copiarla cuando la necesites.') } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo regenerar la API Key.') } }
  async function revealKey() { if (!connection) return null; if (apiKey?.apiKey) return apiKey.apiKey; try { const updated = await getConnectionApiKey(connection.id, true); setApiKey(updated); return updated.apiKey || null } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo mostrar la API Key.'); return null } }
  async function toggleKeyVisibility() { if (showKey) { setShowKey(false); return }; if (await revealKey()) setShowKey(true) }
  async function copyKey() { const value = apiKey?.apiKey || await revealKey(); if (!value) return; try { await navigator.clipboard.writeText(value); setNotice('API Key copiada.') } catch { setError('No se pudo copiar la API Key.') } }
  async function copyIntegrationUrl(url: string) { try { await navigator.clipboard.writeText(url); setNotice('URL de envío copiada.') } catch { setError('No se pudo copiar la URL de envío.') } }
  async function reconnect() { if (!connection) return; setError(null); try { await reconnectConnection(connection.id); await refreshWorkspace() } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo iniciar la reconexión.') } }
  async function removeConnection() { if (!connection) return; setError(null); setIsDeleting(true); try { await deleteConnection(connection.id); navigate(`/clients/${connection.clientId}`) } catch (reason) { setError(reason instanceof Error ? reason.message : 'No se pudo eliminar la conexión.') } finally { setIsDeleting(false) } }

  if (isLoading) return <LoadingState label="Cargando conexión…" />
  if (!connection) return <div className="clients-state clients-state-error" role="alert"><p>{error || 'Conexión no encontrada.'}</p><button type="button" onClick={() => void loadConnection()}>Reintentar</button></div>
  const displayedKey = showKey && apiKey?.apiKey ? apiKey.apiKey : apiKey?.maskedApiKey
  const headerState = workspaceState(connection, statusSummary)
  const messageId = new URLSearchParams(location.search).get('message_id')

  return <section className="connection-detail workspace-detail">
    <header className="workspace-header"><button type="button" className="client-back-link" onClick={() => navigate(`/clients/${connection.clientId}`)}><ArrowLeft size={16} aria-hidden="true" /> {connection.client?.name || 'Cliente'}</button><div className="workspace-header-main"><div><StatusBadge tone={headerState.tone}>{headerState.label}</StatusBadge><h2>{connection.name}</h2><p>{connection.client?.name || 'Cliente'} · {connection.channel.displayName} · {connection.provider.displayName}</p></div><div className="workspace-header-actions"><button type="button" className="client-button-secondary" onClick={() => void refreshWorkspace()}><RefreshCw size={15} aria-hidden="true" /> Actualizar</button></div></div></header>
    <div className="workspace-tabs" role="tablist" aria-label="Secciones del Workspace">{tabs.map((tab) => <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} className={activeTab === tab.id ? 'is-active' : ''} onClick={() => navigate(tab.id === 'webhooks' ? `/connections/${connection.id}/webhooks` : `/connections/${connection.id}${tab.id === 'general' ? '' : `?tab=${tab.id}`}`)}>{tab.label}</button>)}</div>
    <Toast message={error} tone="error" onDismiss={() => setError(null)} /><Toast message={notice} tone="success" onDismiss={() => setNotice(null)} />
    <div className="workspace-tab-content">
      {activeTab === 'webhooks' ? <ConnectionWebhooks connection={connection} /> : null}
      {activeTab === 'general' ? <>
        {isEditingName ? <form className="connection-name-form" onSubmit={saveName}><Field label="Nombre" required><Input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required autoFocus /></Field><div><button type="button" className="client-button-secondary" onClick={() => { setName(connection.name); setIsEditingName(false) }}>Cancelar</button><button type="submit" className="client-button-primary" disabled={isSaving}>{isSaving ? 'Guardando…' : 'Guardar'}</button></div></form> : null}
        <section className="connection-section workspace-general-info"><div className="connection-section-heading"><h3>Información general</h3><button type="button" className="client-button-secondary" onClick={() => setIsEditingName((value) => !value)}><Pencil size={15} aria-hidden="true" /> Editar nombre</button></div><dl className="connection-information-list"><div><dt>Cliente</dt><dd>{connection.client?.name || 'No disponible'}</dd></div><div><dt>Canal</dt><dd>{connection.channel.displayName}</dd></div><div><dt>Provider</dt><dd>{connection.provider.displayName}</dd></div><div><dt>Estado</dt><dd><StatusBadge tone={headerState.tone}>{headerState.label}</StatusBadge></dd></div><div><dt>Última actividad</dt><dd>{dateTime(statusSummary?.lastActivityAt || connection.lastActivityAt)}</dd></div></dl></section>
        <section className="connection-section connection-integration-section"><div className="connection-section-heading"><div><h3>Integración con tu bot</h3><p>Usá esta URL para que tu bot solicite el envío de mensajes por esta conexión.</p></div></div><div className="connection-endpoint"><span>API de envío</span><code>{integrationEndpoints?.messageApiUrl || 'No disponible'}</code>{integrationEndpoints ? <button type="button" className="client-button-secondary" onClick={() => void copyIntegrationUrl(integrationEndpoints.messageApiUrl)}><Clipboard size={15} aria-hidden="true" /> Copiar</button> : null}</div><p className="connection-endpoint-note">Método POST · requiere autenticación del Gateway · esta URL no incluye claves.</p></section>
        <OperationsDiagnostics connectionId={connection.id} providerId={connection.provider.id} onReconnect={reconnect} onRefreshConnection={refreshWorkspace} onManageWebhooks={() => navigate(`/connections/${connection.id}/webhooks`)} />
        <section className="connection-section"><h3>Administración</h3><div className="connection-inline-actions"><button type="button" className="client-button-danger" onClick={() => setIsDeleteDialogOpen(true)} disabled={isDeleting}><Trash2 size={15} aria-hidden="true" /> Eliminar conexión</button></div></section>
      </> : null}
      {activeTab === 'security' ? <section className="connection-section workspace-security"><div className="connection-section-heading"><div><h3>API Key</h3><p>{apiKey?.enabled && apiKey.hasApiKey ? 'Activa' : 'Sin API Key activa'}</p></div></div><dl className="connection-information-list"><div><dt>Estado</dt><dd>{apiKey?.enabled && apiKey.hasApiKey ? 'Activa' : 'Sin API Key activa'}</dd></div><div><dt>API Key</dt><dd className="connection-key-value">{displayedKey || 'No hay una API Key disponible'}</dd></div></dl>{apiKey?.hasApiKey && !apiKey.canRevealApiKey ? <p className="connection-endpoint-note">Esta clave fue creada antes de habilitar la visualización. Regenerala una vez para poder mostrarla y copiarla luego.</p> : null}<div className="connection-inline-actions">{apiKey?.canRevealApiKey ? <button type="button" className="client-button-secondary" onClick={() => void toggleKeyVisibility()}>{showKey ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}{showKey ? 'Ocultar' : 'Mostrar'}</button> : null}<button type="button" className="client-button-secondary" onClick={() => void copyKey()} disabled={!apiKey?.canRevealApiKey && !apiKey?.apiKey}><Clipboard size={15} aria-hidden="true" /> Copiar</button><button type="button" className="client-button-secondary" onClick={() => setIsRegenerateDialogOpen(true)}>Regenerar</button></div></section> : null}
      {activeTab === 'messages' ? <MessagesWorkspace connectionId={connection.id} messageId={messageId} /> : null}
    </div>
    <ConfirmDialog isOpen={isDeleteDialogOpen} title="Eliminar conexión" description="Esta acción eliminará la conexión y su acceso al Workspace." confirmLabel="Eliminar conexión" isSubmitting={isDeleting} onCancel={() => setIsDeleteDialogOpen(false)} onConfirm={() => void removeConnection()} />
    <ConfirmDialog isOpen={isRegenerateDialogOpen} title="Regenerar API Key" description="La API Key actual dejará de ser válida. Asegurate de copiar la nueva clave al finalizar." confirmLabel="Regenerar API Key" tone="default" onCancel={() => setIsRegenerateDialogOpen(false)} onConfirm={() => { setIsRegenerateDialogOpen(false); void regenerateKey() }} />
  </section>
}
