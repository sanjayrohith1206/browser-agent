import type { PendingQuestion } from '../sidepanel/turns';
import { Markdown } from './Markdown';

interface Props {
  question: PendingQuestion;
  onAnswer(answer: string): void;
}

/** A question the agent is waiting on. Options answer in one click; the
 * composer takes a free-form answer. */
export function QuestionCard({ question, onAnswer }: Props) {
  return (
    <div className="bubble bubble-agent question-card" role="group" aria-label="The assistant has a question">
      <Markdown text={question.question} />
      {question.options.length > 0 && (
        <div className="suggestions question-options">
          {question.options.map((option) => (
            <button key={option} type="button" className="chip" onClick={() => onAnswer(option)}>
              {option}
            </button>
          ))}
        </div>
      )}
      <p className="question-hint">
        {question.options.length > 0 ? 'Pick one, or type your own answer below.' : 'Type your answer below.'}
      </p>
    </div>
  );
}
