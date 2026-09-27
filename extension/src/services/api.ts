import type {
  CreateTaskRequest,
  MemoryItem,
  ServerEvent,
  SignedIn,
  Task,
  TaskSummary,
  User,
} from '@shared/protocol';

export interface Health {
  status: 'ok' | 'degraded';
  llm_provider: string | null;
  llm_model: string | null;
  detail?: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const SOCKET_PROTOCOL = 'browser-agent.v1';

export class ApiClient {
  constructor(
    readonly baseUrl: string,
    private readonly token: string | null = null,
    /** Called when the server says the sign-in is no longer valid. */
    private readonly onUnauthorized: () => void = () => undefined,
  ) {}

  withToken(token: string | null, onUnauthorized: () => void): ApiClient {
    return new ApiClient(this.baseUrl, token, onUnauthorized);
  }

  get signedIn(): boolean {
    return this.token !== null;
  }

  health(): Promise<Health> {
    return this.request<Health>('GET', '/healthz');
  }

  // ---------------------------------------------------------------- auth

  authStatus(): Promise<{ registration_open: boolean }> {
    return this.request('GET', '/api/auth/status');
  }

  register(email: string, password: string, displayName = ''): Promise<SignedIn> {
    return this.request('POST', '/api/auth/register', { email, password, display_name: displayName });
  }

  login(email: string, password: string): Promise<SignedIn> {
    return this.request('POST', '/api/auth/login', { email, password });
  }

  async logout(): Promise<void> {
    await this.request('POST', '/api/auth/logout');
  }

  me(): Promise<User> {
    return this.request('GET', '/api/auth/me');
  }

  // --------------------------------------------------------------- tasks

  createTask(body: CreateTaskRequest): Promise<Task> {
    return this.request<Task>('POST', '/api/tasks', body);
  }

  getTask(id: string): Promise<Task> {
    return this.request<Task>('GET', `/api/tasks/${encodeURIComponent(id)}`);
  }

  stopTask(id: string): Promise<Task> {
    return this.request<Task>('POST', `/api/tasks/${encodeURIComponent(id)}/stop`);
  }

  async deleteTask(id: string): Promise<void> {
    await this.request('DELETE', `/api/tasks/${encodeURIComponent(id)}`);
  }

  async listTasks(limit = 30, before?: string): Promise<TaskSummary[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (before) params.set('before', before);
    const res = await this.request<{ tasks: TaskSummary[] }>('GET', `/api/tasks?${params}`);
    return res.tasks;
  }

  async taskEvents(id: string): Promise<ServerEvent[]> {
    const res = await this.request<{ events: ServerEvent[] }>('GET', `/api/tasks/${encodeURIComponent(id)}/events`);
    return res.events;
  }

  taskSocketUrl(id: string, afterSeq = 0): string {
    const url = new URL(`/ws/tasks/${encodeURIComponent(id)}`, this.baseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('after_seq', String(afterSeq));
    return url.toString();
  }

  /** WebSockets can't carry headers, so the token rides as a subprotocol. */
  socketProtocols(): string[] {
    return this.token ? [SOCKET_PROTOCOL, `auth.${this.token}`] : [SOCKET_PROTOCOL];
  }

  // -------------------------------------------------------------- memory

  async listMemory(): Promise<{ items: MemoryItem[]; max_items: number }> {
    return this.request('GET', '/api/memory');
  }

  addMemory(content: string): Promise<MemoryItem> {
    return this.request('POST', '/api/memory', { content });
  }

  updateMemory(id: string, content: string): Promise<MemoryItem> {
    return this.request('PATCH', `/api/memory/${encodeURIComponent(id)}`, { content });
  }

  async deleteMemory(id: string): Promise<void> {
    await this.request('DELETE', `/api/memory/${encodeURIComponent(id)}`);
  }

  // ------------------------------------------------------------- plumbing

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let res: Response;
    try {
      res = await fetch(new URL(path, this.baseUrl), {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      throw new ApiError(`Can't reach the assistant server at ${this.baseUrl}. Is it running?`, 0);
    }
    if (!res.ok) {
      if (res.status === 401 && this.token) this.onUnauthorized();
      throw new ApiError(await errorDetail(res), res.status);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) return validationMessage(data.detail);
  } catch {
    // Non-JSON error body.
  }
  return `The server returned an error (${res.status}).`;
}

/** The first validation problem, in words (FastAPI's 422 format). */
function validationMessage(detail: unknown[]): string {
  const first = detail[0] as { msg?: unknown } | undefined;
  const msg = typeof first?.msg === 'string' ? first.msg.replace(/^Value error, /, '') : '';
  return msg ? `${msg.charAt(0).toUpperCase()}${msg.slice(1)}.` : 'The request was not valid.';
}
