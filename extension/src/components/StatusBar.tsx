import type { Connection, ActiveTab } from '../sidepanel/hooks';
import type { Turn } from '../sidepanel/turns';

const STATUS_LABEL: Record<Turn['status'], string> = {
  starting: 'Starting',
  queued: 'Starting',
  running: 'Working',
  waiting: 'Needs you',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Stopped',
  interrupted: 'Interrupted',
};

interface Props {
  tab: ActiveTab | null;
  connection: Connection;
  status: Turn['status'] | null;
}

export function StatusBar({ tab, connection, status }: Props) {
  return (
    <div className="statusbar">
      <span className={`dot is-${connection.state}`} title={connectionTitle(connection)} />
      <span className="statusbar-page" title={tab?.url}>
        {tab ? tab.title || tab.url || 'New tab' : 'No page'}
      </span>
      {status && <span className={`pill is-${status}`}>{STATUS_LABEL[status]}</span>}
    </div>
  );
}

function connectionTitle(c: Connection): string {
  switch (c.state) {
    case 'checking':
      return 'Connecting…';
    case 'ok':
      return `Connected (${c.health.llm_model ?? 'model'})`;
    case 'degraded':
    case 'offline':
      return c.message;
  }
}
