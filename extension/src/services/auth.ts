// The sign-in token, kept in extension storage and tied to the server it
// came from: changing the server address means signing in again.

import type { SignedIn, User } from '@shared/protocol';

export interface StoredAuth {
  backendUrl: string;
  token: string;
  user: User;
}

const KEY = 'auth';

export async function loadAuth(backendUrl: string): Promise<StoredAuth | null> {
  const stored = (await chrome.storage.local.get(KEY))[KEY] as StoredAuth | undefined;
  return stored && stored.backendUrl === backendUrl ? stored : null;
}

export async function saveAuth(backendUrl: string, signedIn: SignedIn): Promise<StoredAuth> {
  const auth = { backendUrl, token: signedIn.token, user: signedIn.user };
  await chrome.storage.local.set({ [KEY]: auth });
  return auth;
}

export async function clearAuth(): Promise<void> {
  await chrome.storage.local.remove(KEY);
}
