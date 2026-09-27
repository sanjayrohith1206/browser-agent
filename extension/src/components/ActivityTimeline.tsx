import { useState } from 'react';
import type { ActivityItem } from '../sidepanel/turns';

const ICON: Record<ActivityItem['state'], string> = { active: '→', done: '✓', failed: '✕' };

export function ActivityTimeline({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) return null;
  return (
    <ol className="timeline" aria-label="Assistant activity">
      {items.map((item) => (
        <TimelineItem key={item.id} item={item} />
      ))}
    </ol>
  );
}

function TimelineItem({ item }: { item: ActivityItem }) {
  const [open, setOpen] = useState(false);
  const expandable = Boolean(item.detail);
  return (
    <li className={`timeline-item is-${item.state}`}>
      <span className="timeline-icon" aria-hidden="true">
        {item.state === 'active' ? <span className="spinner" /> : ICON[item.state]}
      </span>
      <div className="timeline-body">
        {expandable ? (
          <button
            type="button"
            className="timeline-label is-toggle"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {item.label}
          </button>
        ) : (
          <span className="timeline-label">{item.label}</span>
        )}
        {expandable && open && <p className="timeline-detail">{item.detail}</p>}
      </div>
    </li>
  );
}
