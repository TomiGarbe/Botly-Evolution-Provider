import type { Connection } from '@/domain/connection'
import { MessagesWorkspace } from './MessagesWorkspace'

export function InstagramMessagesWorkspace({ connection }: { connection: Connection }) {
  return <MessagesWorkspace runtimeName={null} connectionId={connection.id} instagram />
}
