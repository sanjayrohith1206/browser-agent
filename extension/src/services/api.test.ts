import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError } from './api';

function respond(status: number, body?: unknown) {
  return vi.fn(async () =>
    body === undefined
      ? new Response(null, { status })
      : new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('ApiClient', () => {
  it('sends the sign-in token and offers it as a socket subprotocol', async () => {
    const fetchMock = respond(200, { tasks: [] });
    vi.stubGlobal('fetch', fetchMock);
    const api = new ApiClient('http://127.0.0.1:8787').withToken('tok123', () => undefined);
    await api.listTasks(10, '2026-01-01T00:00:00Z');
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(String(url)).toBe('http://127.0.0.1:8787/api/tasks?limit=10&before=2026-01-01T00%3A00%3A00Z');
    expect(init.headers).toEqual({ Authorization: 'Bearer tok123' });
    expect(api.socketProtocols()).toEqual(['browser-agent.v1', 'auth.tok123']);
  });

  it('reports an expired sign-in', async () => {
    vi.stubGlobal('fetch', respond(401, { detail: 'Please sign in.' }));
    const onUnauthorized = vi.fn();
    const api = new ApiClient('http://127.0.0.1:8787').withToken('old', onUnauthorized);
    await expect(api.listMemory()).rejects.toThrow(new ApiError('Please sign in.', 401));
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('turns validation errors into a readable message', async () => {
    vi.stubGlobal(
      'fetch',
      respond(422, { detail: [{ loc: ['body', 'email'], msg: 'Value error, enter a valid email address' }] }),
    );
    await expect(new ApiClient('http://x').register('nope', 'pw')).rejects.toThrow('Enter a valid email address.');
  });

  it('handles empty responses', async () => {
    vi.stubGlobal('fetch', respond(204));
    await expect(new ApiClient('http://x').withToken('t', () => undefined).deleteTask('a')).resolves.toBeUndefined();
  });
});
