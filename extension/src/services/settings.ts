export interface Settings {
  backendUrl: string;
}

export const DEFAULT_SETTINGS: Settings = {
  backendUrl: 'http://127.0.0.1:8787',
};

export async function loadSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get('settings');
  const value = stored.settings as Partial<Settings> | undefined;
  return { ...DEFAULT_SETTINGS, ...value };
}

export async function saveSettings(settings: Settings): Promise<void> {
  await chrome.storage.local.set({ settings: { ...settings, backendUrl: normalizeUrl(settings.backendUrl) } });
}

export function normalizeUrl(url: string): string {
  return url.trim().replace(/\/+$/, '');
}
