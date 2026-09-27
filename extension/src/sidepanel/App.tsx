import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { AuthScreen } from '../components/AuthScreen';
import { ChatTurn } from '../components/ChatTurn';
import { Composer } from '../components/Composer';
import { HistoryView } from '../components/HistoryView';
import { MemoryView } from '../components/MemoryView';
import { SettingsPanel } from '../components/SettingsPanel';
import { StatusBar } from '../components/StatusBar';
import { AgentSession } from '../services/agentSession';
import { ApiClient, ApiError } from '../services/api';
import { useActiveTab, useAuth, useConnection, useSettings } from './hooks';
import { isTerminal, turnsReducer } from './turns';

const SUGGESTIONS = ['Summarize this page', 'What are the key points here?', 'Explain this page simply'];

type View = 'chat' | 'history' | 'memory';

export function App() {
  const [settings, saveSettings, settingsLoaded] = useSettings();
  const baseApi = useMemo(() => new ApiClient(settings.backendUrl), [settings.backendUrl]);
  const [connection, recheck] = useConnection(baseApi, settingsLoaded);
  const { auth, loaded: authLoaded, signIn, signOut } = useAuth(settings.backendUrl, settingsLoaded);
  const api = useMemo(
    () => baseApi.withToken(auth?.token ?? null, () => void signOut()),
    [baseApi, auth?.token, signOut],
  );
  const tab = useActiveTab();
  const [turns, dispatch] = useReducer(turnsReducer, []);
  const [showSettings, setShowSettings] = useState(false);
  const [view, setView] = useState<View>('chat');
  const session = useRef<AgentSession | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const current = turns.at(-1) ?? null;
  const busy = current !== null && !isTerminal(current.status);

  useEffect(() => () => session.current?.dispose(), []);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, view]);

  const send = useCallback(
    (prompt: string) => {
      if (!tab) return;
      const id = crypto.randomUUID();
      dispatch({ type: 'started', id, prompt, pageTitle: tab.title });
      session.current?.dispose();
      const s = new AgentSession(api, {
        onTask: (task) => dispatch({ type: 'task_created', id, taskId: task.id }),
        onEvent: (event) => dispatch({ type: 'event', id, event }),
        onConnectionLost: (message) => dispatch({ type: 'failed', id, message }),
      });
      session.current = s;
      s.start(prompt, { tab_id: tab.id, url: tab.url, title: tab.title }).catch((err: unknown) => {
        const message =
          err instanceof ApiError || err instanceof Error ? err.message : 'Could not start the task.';
        dispatch({ type: 'failed', id, message });
        recheck();
      });
    },
    [api, tab, recheck],
  );

  const stop = useCallback(() => {
    void session.current?.stop().catch(() => undefined);
  }, []);

  const answer = useCallback((requestId: string, text: string) => {
    session.current?.reply(requestId, text);
  }, []);

  const decide = useCallback((requestId: string, approved: boolean) => {
    session.current?.decide(requestId, approved);
  }, []);

  const logOut = useCallback(async () => {
    session.current?.dispose();
    await api.logout().catch(() => undefined);
    await signOut();
    setShowSettings(false);
    setView('chat');
  }, [api, signOut]);

  const question = current?.question ?? null;
  const reachable = connection.state === 'ok' || connection.state === 'degraded';
  const needsSignIn = reachable && authLoaded && !auth;
  const toggle = (next: View): void => setView((v) => (v === next ? 'chat' : next));

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <img src="/icons/icon-48.png" alt="" width={22} height={22} />
          Browser Agent
        </h1>
        <nav className="header-actions">
          {auth && (
            <>
              <button
                type="button"
                className={`icon-button${view === 'history' ? ' is-active' : ''}`}
                aria-label="History"
                title="History"
                aria-pressed={view === 'history'}
                onClick={() => toggle('history')}
              >
                🕘
              </button>
              <button
                type="button"
                className={`icon-button${view === 'memory' ? ' is-active' : ''}`}
                aria-label="Memory"
                title="Memory"
                aria-pressed={view === 'memory'}
                onClick={() => toggle('memory')}
              >
                🧠
              </button>
            </>
          )}
          <button
            type="button"
            className="icon-button"
            aria-label="Settings"
            title="Settings"
            aria-expanded={showSettings}
            onClick={() => setShowSettings((v) => !v)}
          >
            ⚙
          </button>
        </nav>
      </header>
      <StatusBar tab={tab} connection={connection} status={current?.status ?? null} />

      {showSettings && (
        <SettingsPanel
          settings={settings}
          connection={connection}
          account={auth?.user.email ?? null}
          onSave={async (s) => {
            await saveSettings(s);
            setShowSettings(false);
          }}
          onSignOut={() => void logOut()}
          onClose={() => setShowSettings(false)}
        />
      )}

      {(connection.state === 'offline' || connection.state === 'degraded') && !showSettings && (
        <div className="notice notice-warning" role="status">
          {connection.message}{' '}
          <button type="button" className="link-button" onClick={recheck}>
            Retry
          </button>
        </div>
      )}

      {needsSignIn ? (
        <div className="chat">
          <AuthScreen api={baseApi} onSignedIn={signIn} />
        </div>
      ) : view === 'history' && auth ? (
        <HistoryView api={api} onClose={() => setView('chat')} />
      ) : view === 'memory' && auth ? (
        <MemoryView api={api} onClose={() => setView('chat')} />
      ) : (
        <>
          <div className="chat" ref={scroller}>
            {turns.length === 0 ? (
              <div className="empty">
                <img className="empty-logo" src="/icons/icon-128.png" alt="" width={72} height={72} />
                <p className="empty-title">What can I do for you?</p>
                <p className="empty-subtitle">
                  I can read and use the page you're on, work across tabs, and ask before doing anything important.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" className="chip" disabled={!tab || !auth} onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              turns.map((turn) => (
                <ChatTurn
                  key={turn.id}
                  turn={turn}
                  {...(turn === current ? { onAnswer: answer, onDecide: decide } : {})}
                />
              ))
            )}
          </div>

          <Composer
            busy={busy}
            disabled={(!tab && !question) || !auth}
            answering={question !== null}
            onSend={(text) => (question ? answer(question.requestId, text) : send(text))}
            onStop={stop}
          />
        </>
      )}
    </div>
  );
}
