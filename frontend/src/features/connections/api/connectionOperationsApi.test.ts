import { describe, expect, it, vi } from 'vitest'

const { gatewayRequest } = vi.hoisted(() => ({ gatewayRequest: vi.fn() }))

vi.mock('@/shared/lib/gatewayClient', () => ({ gatewayRequest }))

import { listConnectionWebhookActivity } from './connectionOperationsApi'

describe('listConnectionWebhookActivity', () => {
  it('maps the safe common transport activity contract', async () => {
    gatewayRequest.mockResolvedValue({
      items: [{
        id: 'core:event-1', timestamp: 1_700_000_000_000, direction: 'outbound', source: 'gateway', destination: 'core',
        event_type: 'message.created', status: 'delivered_to_core', http_status: null, duration_ms: null,
        event_id: 'event-1', provider_message_id: 'mid-1', request_id: 'request-1', correlation_id: 'correlation-1',
        error: null, attempt_count: 1,
      }],
    })

    await expect(listConnectionWebhookActivity('connection-1')).resolves.toEqual([{
      id: 'core:event-1', timestamp: 1_700_000_000_000, direction: 'outbound', source: 'gateway', destination: 'core',
      eventType: 'message.created', status: 'delivered_to_core', httpStatus: null, durationMs: null,
      eventId: 'event-1', providerMessageId: 'mid-1', requestId: 'request-1', correlationId: 'correlation-1',
      error: null, attemptCount: 1,
    }])
    expect(gatewayRequest).toHaveBeenCalledWith('/connections/connection-1/webhook/activity?limit=100')
  })
})
