import type { SignedIn } from '@shared/protocol';
import { useEffect, useState, type FormEvent } from 'react';
import type { ApiClient } from '../services/api';

interface Props {
  api: ApiClient;
  onSignedIn(signedIn: SignedIn): Promise<void>;
}

/** Sign in, or create the first account on a new server. */
export function AuthScreen({ api, onSignedIn }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.authStatus().then(
      ({ registration_open }) => {
        if (cancelled) return;
        setRegistrationOpen(registration_open);
        if (registration_open) setMode('register');
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [api]);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const signedIn =
        mode === 'register' ? await api.register(email, password, name) : await api.login(email, password);
      await onSignedIn(signedIn);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const registering = mode === 'register';
  return (
    <form className="auth" onSubmit={(e) => void submit(e)}>
      <img className="empty-logo" src="/icons/icon-128.png" alt="" width={56} height={56} />
      <h2>{registering ? 'Create your account' : 'Sign in'}</h2>
      <p className="auth-subtitle">
        {registering
          ? 'Your tasks, history and memory are kept private to this account.'
          : 'Sign in to the assistant server to continue.'}
      </p>
      {registering && (
        <>
          <label htmlFor="auth-name">Name (optional)</label>
          <input id="auth-name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={100} />
        </>
      )}
      <label htmlFor="auth-email">Email</label>
      <input
        id="auth-email"
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        autoComplete="email"
        autoFocus
      />
      <label htmlFor="auth-password">Password</label>
      <input
        id="auth-password"
        type="password"
        required
        minLength={registering ? 10 : 1}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete={registering ? 'new-password' : 'current-password'}
      />
      {registering && <p className="field-hint">At least 10 characters.</p>}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="button button-primary" disabled={busy}>
        {busy ? 'Please wait…' : registering ? 'Create account' : 'Sign in'}
      </button>
      {registrationOpen && (
        <button type="button" className="link-button auth-switch" onClick={() => setMode(registering ? 'login' : 'register')}>
          {registering ? 'I already have an account' : 'Create an account'}
        </button>
      )}
    </form>
  );
}
