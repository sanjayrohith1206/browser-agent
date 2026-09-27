import { useState, type FormEvent } from 'react';
import type { Connection } from '../sidepanel/hooks';
import type { Settings } from '../services/settings';

interface Props {
  settings: Settings;
  connection: Connection;
  /** The signed-in account's email, if any. */
  account: string | null;
  onSave(settings: Settings): Promise<void>;
  onSignOut(): void;
  onClose(): void;
}

export function SettingsPanel({ settings, connection, account, onSave, onSignOut, onClose }: Props) {
  const [url, setUrl] = useState(settings.backendUrl);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error();
    } catch {
      setError('Enter a full address, like http://127.0.0.1:8787');
      return;
    }
    setError(null);
    await onSave({ ...settings, backendUrl: url });
  };

  return (
    <form className="settings" onSubmit={(e) => void submit(e)}>
      <h2>Settings</h2>
      <label htmlFor="backend-url">Assistant server</label>
      <input
        id="backend-url"
        type="url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        spellCheck={false}
      />
      {error && <p className="field-error">{error}</p>}
      <p className="settings-status">{describe(connection)}</p>
      {account && (
        <p className="settings-status">
          Signed in as {account}.{' '}
          <button type="button" className="link-button" onClick={onSignOut}>
            Sign out
          </button>
        </p>
      )}
      <div className="settings-actions">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
        <button type="submit" className="button button-primary">
          Save
        </button>
      </div>
    </form>
  );
}

function describe(c: Connection): string {
  switch (c.state) {
    case 'checking':
      return 'Checking connection…';
    case 'ok':
      return `Connected · ${c.health.llm_provider ?? ''} ${c.health.llm_model ?? ''}`.trim();
    case 'degraded':
    case 'offline':
      return c.message;
  }
}
