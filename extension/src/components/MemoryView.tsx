import type { MemoryItem } from '@shared/protocol';
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import type { ApiClient } from '../services/api';

const MAX_CHARS = 500;

/** Notes the assistant keeps in mind in every task. Only the user edits
 * them; the assistant reads them. */
export function MemoryView({ api, onClose }: { api: ApiClient; onClose(): void }) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [maxItems, setMaxItems] = useState(100);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (action: () => Promise<void>) => {
    try {
      await action();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void run(async () => {
      const res = await api.listMemory();
      setItems(res.items);
      setMaxItems(res.max_items);
    });
  }, [api, run]);

  const add = (e: FormEvent): void => {
    e.preventDefault();
    const content = draft.trim();
    if (!content) return;
    void run(async () => {
      const item = await api.addMemory(content);
      setItems((prev) => [...prev, item]);
      setDraft('');
    });
  };

  const save = (): void => {
    if (!editing) return;
    const { id, text } = editing;
    void run(async () => {
      const item = await api.updateMemory(id, text.trim());
      setItems((prev) => prev.map((m) => (m.id === id ? item : m)));
      setEditing(null);
    });
  };

  const remove = (id: string): void => {
    void run(async () => {
      await api.deleteMemory(id);
      setItems((prev) => prev.filter((m) => m.id !== id));
    });
  };

  return (
    <div className="panel-view">
      <div className="panel-view-header">
        <h2>Memory</h2>
        <button type="button" className="icon-button" aria-label="Close memory" onClick={onClose}>
          ✕
        </button>
      </div>
      <p className="panel-intro">
        Things the assistant should always know about you, like preferences or details you often need. It reads
        these in every task; only you can change them.
      </p>
      {error && (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      )}
      <ul className="memory-list">
        {items.map((item) => (
          <li key={item.id} className="memory-item">
            {editing?.id === item.id ? (
              <>
                <textarea
                  value={editing.text}
                  maxLength={MAX_CHARS}
                  onChange={(e) => setEditing({ id: item.id, text: e.target.value })}
                  aria-label="Edit memory"
                  autoFocus
                />
                <div className="memory-actions">
                  <button type="button" className="button" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                  <button type="button" className="button button-primary" disabled={!editing.text.trim()} onClick={save}>
                    Save
                  </button>
                </div>
              </>
            ) : (
              <>
                <p>{item.content}</p>
                <div className="memory-actions">
                  <button type="button" className="link-button" onClick={() => setEditing({ id: item.id, text: item.content })}>
                    Edit
                  </button>
                  <button type="button" className="link-button" onClick={() => remove(item.id)}>
                    Delete
                  </button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
      {items.length < maxItems ? (
        <form className="memory-add" onSubmit={add}>
          <textarea
            value={draft}
            maxLength={MAX_CHARS}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. I prefer aisle seats and vegetarian meals"
            aria-label="New memory"
            rows={2}
          />
          <button type="submit" className="button button-primary" disabled={!draft.trim()}>
            Remember
          </button>
        </form>
      ) : (
        <p className="panel-empty">Memory is full. Delete something to add more.</p>
      )}
    </div>
  );
}
