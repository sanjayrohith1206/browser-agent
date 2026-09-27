import type { SignedIn } from '@shared/protocol';
import { useCallback, useEffect, useState } from 'react';
import { ApiClient, type Health } from '../services/api';
import { clearAuth, loadAuth, saveAuth, type StoredAuth } from '../services/auth';
import { DEFAULT_SETTINGS, loadSettings, saveSettings, type Settings } from '../services/settings';

export interface ActiveTab {
  id: number;
  url: string;
  title: string;
}

/** The browser window whose page the agent works on: the normal window
 * hosting this panel, or — when the panel page is not hosted in a normal
 * window (e.g. opened as its own window) — the last focused normal window. */
async function targetWindowId(): Promise<number | undefined> {
  const current = await chrome.windows.getCurrent();
  if (current.type === 'normal') return current.id;
  try {
    return (await chrome.windows.getLastFocused({ windowTypes: ['normal'] })).id;
  } catch {
    return undefined; // no normal window open
  }
}

/** The active tab in the target window, kept up to date. */
export function useActiveTab(): ActiveTab | null {
  const [tab, setTab] = useState<ActiveTab | null>(null);

  useEffect(() => {
    let windowId: number | undefined;
    const refresh = async (): Promise<void> => {
      windowId = await targetWindowId();
      const [active] =
        windowId === undefined ? [] : await chrome.tabs.query({ active: true, windowId });
      setTab(
        active?.id !== undefined
          ? { id: active.id, url: active.url ?? '', title: active.title ?? '' }
          : null,
      );
    };
    const onActivated = (info: chrome.tabs.OnActivatedInfo): void => {
      if (windowId === undefined || info.windowId === windowId) void refresh();
    };
    const onUpdated = (_id: number, change: chrome.tabs.OnUpdatedInfo, t: chrome.tabs.Tab): void => {
      if (t.active && (windowId === undefined || t.windowId === windowId) && (change.title || change.url)) {
        void refresh();
      }
    };
    void refresh();
    chrome.tabs.onActivated.addListener(onActivated);
    chrome.tabs.onUpdated.addListener(onUpdated);
    return () => {
      chrome.tabs.onActivated.removeListener(onActivated);
      chrome.tabs.onUpdated.removeListener(onUpdated);
    };
  }, []);

  return tab;
}

export function useSettings(): [Settings, (s: Settings) => Promise<void>, boolean] {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    void loadSettings().then((s) => {
      setSettings(s);
      setLoaded(true);
    });
  }, []);

  const update = useCallback(async (s: Settings) => {
    await saveSettings(s);
    setSettings(await loadSettings());
  }, []);

  return [settings, update, loaded];
}

export type Connection =
  | { state: 'checking' }
  | { state: 'ok'; health: Health }
  | { state: 'degraded'; message: string }
  | { state: 'offline'; message: string };

export function useConnection(api: ApiClient, enabled: boolean): [Connection, () => void] {
  const [connection, setConnection] = useState<Connection>({ state: 'checking' });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setConnection({ state: 'checking' });
    api.health().then(
      (health) => {
        if (cancelled) return;
        setConnection(
          health.status === 'ok'
            ? { state: 'ok', health }
            : { state: 'degraded', message: health.detail ?? 'The server is not fully configured.' },
        );
      },
      (err: unknown) => {
        if (!cancelled) {
          setConnection({ state: 'offline', message: err instanceof Error ? err.message : String(err) });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [api, enabled, nonce]);

  return [connection, () => setNonce((n) => n + 1)];
}

export interface AuthState {
  auth: StoredAuth | null;
  loaded: boolean;
  signIn(signedIn: SignedIn): Promise<void>;
  signOut(): Promise<void>;
}

/** The stored sign-in for the current server. */
export function useAuth(backendUrl: string, enabled: boolean): AuthState {
  const [auth, setAuth] = useState<StoredAuth | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void loadAuth(backendUrl).then((a) => {
      if (cancelled) return;
      setAuth(a);
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [backendUrl, enabled]);

  const signIn = useCallback(
    async (signedIn: SignedIn) => setAuth(await saveAuth(backendUrl, signedIn)),
    [backendUrl],
  );
  const signOut = useCallback(async () => {
    await clearAuth();
    setAuth(null);
  }, []);

  return { auth, loaded, signIn, signOut };
}
