import type { ClientMessage, ServerEvent } from '@shared/protocol';

export interface SocketHandlers {
  onEvent(event: ServerEvent): void;
  onClose(code: number, reason: string): void;
}

// Close codes sent by the backend.
export const CLOSE_REASONS: Record<number, string> = {
  4401: 'Your sign-in has expired. Please sign in again.',
  4403: 'The assistant server rejected this extension.',
  4404: 'The task no longer exists on the server.',
  4409: 'This task is already open in another panel.',
  4429: 'Too many messages were sent. Please try again.',
  4503: 'The assistant server is not configured yet.',
};

/** One WebSocket per task: server events in; tool results, answers,
 * approvals and stop out. */
export class TaskSocket {
  private ws: WebSocket | null = null;

  connect(url: string, protocols: string[], handlers: SocketHandlers): void {
    const ws = new WebSocket(url, protocols);
    this.ws = ws;
    ws.onmessage = (msg) => {
      if (typeof msg.data !== 'string') return;
      try {
        handlers.onEvent(JSON.parse(msg.data) as ServerEvent);
      } catch (err) {
        console.error('Malformed server event', err);
      }
    };
    ws.onclose = (ev) => {
      this.ws = null;
      handlers.onClose(ev.code, ev.reason);
    };
  }

  send(message: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(message));
    return true;
  }

  close(): void {
    this.ws?.close(1000, 'client closed');
    this.ws = null;
  }
}
