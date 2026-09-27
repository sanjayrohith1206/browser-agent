import type { Turn } from '../sidepanel/turns';
import { ActivityTimeline } from './ActivityTimeline';
import { ConfirmationCard } from './ConfirmationCard';
import { Markdown } from './Markdown';
import { QuestionCard } from './QuestionCard';

const STATUS_NOTE: Partial<Record<Turn['status'], string>> = {
  cancelled: 'Stopped',
  interrupted: 'Interrupted',
};

interface Props {
  turn: Turn;
  onAnswer?(requestId: string, answer: string): void;
  onDecide?(requestId: string, approved: boolean): void;
}

export function ChatTurn({ turn, onAnswer, onDecide }: Props) {
  const note = STATUS_NOTE[turn.status];
  return (
    <section className="turn">
      <div className="bubble bubble-user">
        <p>{turn.prompt}</p>
        {turn.pageTitle && <span className="bubble-context">on {turn.pageTitle}</span>}
      </div>

      <ActivityTimeline items={turn.activity} />

      {turn.messages.map((m) =>
        m.text.trim() ? (
          <div key={m.id} className={`bubble bubble-agent${m.complete ? '' : ' is-streaming'}`}>
            <Markdown text={m.text} />
          </div>
        ) : null,
      )}

      {turn.question && onAnswer && (
        <QuestionCard question={turn.question} onAnswer={(a) => onAnswer(turn.question!.requestId, a)} />
      )}

      {turn.confirmation && onDecide && (
        <ConfirmationCard
          key={turn.confirmation.requestId}
          confirmation={turn.confirmation}
          onDecide={(approved) => onDecide(turn.confirmation!.requestId, approved)}
        />
      )}

      {turn.error && turn.status !== 'cancelled' && (
        <div className="notice notice-error" role="alert">
          {turn.error}
        </div>
      )}
      {note && <div className="turn-note">{note}</div>}
    </section>
  );
}
