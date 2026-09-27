import type { TaskSummary } from '@shared/protocol';
import { useCallback, useEffect, useState } from 'react';
import type { ApiClient } from '../services/api';
import { turnFromHistory, type Turn } from '../sidepanel/turns';
import { ChatTurn } from './ChatTurn';

const PAGE_SIZE = 30;

const STATUS: Record<TaskSummary['status'], string> = {
  queued: 'Not started',
  running: 'Running',
  waiting: 'Waiting',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Stopped',
  interrupted: 'Interrupted',
};

/** Past tasks, newest first; open one to read it again. */
export function HistoryView({ api, onClose }: { api: ApiClient; onClose(): void }) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<{ task: TaskSummary; turn: Turn } | null>(null);

  const load = useCallback(
    async (before?: string) => {
      try {
        const page = await api.listTasks(PAGE_SIZE, before);
        setTasks((prev) => (before ? [...prev, ...page] : page));
        setMore(page.length === PAGE_SIZE);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [api],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const show = async (task: TaskSummary): Promise<void> => {
    try {
      setOpen({ task, turn: turnFromHistory(task.id, task.goal, await api.taskEvents(task.id)) });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async (task: TaskSummary): Promise<void> => {
    try {
      await api.deleteTask(task.id);
      setTasks((prev) => prev.filter((t) => t.id !== task.id));
      setOpen(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="panel-view">
      <div className="panel-view-header">
        {open ? (
          <button type="button" className="link-button" onClick={() => setOpen(null)}>
            ← All tasks
          </button>
        ) : (
          <h2>History</h2>
        )}
        <button type="button" className="icon-button" aria-label="Close history" onClick={onClose}>
          ✕
        </button>
      </div>
      {error && (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      )}
      {open ? (
        <div className="chat">
          <p className="history-meta">{new Date(open.task.created_at).toLocaleString()}</p>
          <ChatTurn turn={open.turn} />
          <button type="button" className="button history-delete" onClick={() => void remove(open.task)}>
            Delete this task
          </button>
        </div>
      ) : tasks.length === 0 && !error ? (
        <p className="panel-empty">Tasks you run will appear here.</p>
      ) : (
        <ul className="history-list">
          {tasks.map((task) => (
            <li key={task.id}>
              <button type="button" className="history-item" onClick={() => void show(task)}>
                <span className="history-goal">{task.goal}</span>
                <span className="history-meta">
                  <span className={`pill is-${task.status}`}>{STATUS[task.status]}</span>
                  {new Date(task.created_at).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
          {more && (
            <li>
              <button type="button" className="button history-more" onClick={() => void load(tasks.at(-1)?.created_at)}>
                Show older
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
