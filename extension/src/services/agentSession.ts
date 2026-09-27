// Drives one task: creates it, streams its events, and executes the browser
// tools it requests in the task's tab.

import { TERMINAL_STATUSES, type PageContext, type ServerEvent, type Task } from '@shared/protocol';
import type { ApiClient } from './api';
import { CLOSE_REASONS, TaskSocket } from './taskSocket';
import { ToolExecutor, type BrowserApi, chromeBrowserApi } from './toolExecutor';

export interface SessionHandlers {
  onTask(task: Task): void;
  onEvent(event: ServerEvent): void;
  /** The session ended without the task reaching a final status. */
  onConnectionLost(message: string): void;
}

export class AgentSession {
  private readonly socket = new TaskSocket();
  private taskId: string | null = null;
  private finished = false;

  constructor(
    private readonly api: ApiClient,
    private readonly handlers: SessionHandlers,
    private readonly browser: BrowserApi = chromeBrowserApi,
  ) {}

  async start(prompt: string, page: PageContext): Promise<void> {
    const task = await this.api.createTask({ prompt, page });
    this.taskId = task.id;
    this.handlers.onTask(task);

    const executor = new ToolExecutor(page.tab_id, this.browser);
    this.socket.connect(this.api.taskSocketUrl(task.id), this.api.socketProtocols(), {
      onEvent: (event) => {
        this.handlers.onEvent(event);
        if (event.type === 'tool_start') {
          void executor.execute(event.tool, event.input, event.confirmed === true).then((result) => {
            this.socket.send({ type: 'tool_result', call_id: event.call_id, result });
          });
        }
        if (event.type === 'task_status' && TERMINAL_STATUSES.includes(event.status)) {
          this.finished = true;
          this.socket.close();
        }
      },
      onClose: (code) => {
        if (!this.finished) {
          this.handlers.onConnectionLost(
            CLOSE_REASONS[code] ?? 'Lost connection to the assistant server.',
          );
        }
      },
    });
  }

  /** Answers the question the agent is waiting on. */
  reply(requestId: string, answer: string): boolean {
    return this.socket.send({ type: 'user_reply', request_id: requestId, answer });
  }

  /** Approves or declines the action the agent is waiting on. */
  decide(requestId: string, approved: boolean): boolean {
    return this.socket.send({ type: 'confirmation_reply', request_id: requestId, approved });
  }

  async stop(): Promise<void> {
    if (this.socket.send({ type: 'stop' })) return;
    if (this.taskId) await this.api.stopTask(this.taskId);
  }

  dispose(): void {
    this.finished = true;
    this.socket.close();
  }
}
