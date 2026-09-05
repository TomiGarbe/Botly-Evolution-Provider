import type { Connection } from '@/domain/connection'
import { MessagesWorkspace } from './MessagesWorkspace'

export function InstagramMessagesWorkspace({ connection }: { connection: Connection }) {
  return <MessagesWorkspace connectionId={connection.id} />
}
