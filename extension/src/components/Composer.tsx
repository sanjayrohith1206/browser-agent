import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

interface Props {
  busy: boolean;
  disabled: boolean;
  /** The agent is waiting for an answer: text sent now is the answer. */
  answering?: boolean;
  onSend(prompt: string): void;
  onStop(): void;
}

export function Composer({ busy, disabled, answering = false, onSend, onStop }: Props) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = (e?: FormEvent): void => {
    e?.preventDefault();
    const prompt = text.trim();
    if (!prompt || (busy && !answering) || disabled) return;
    onSend(prompt);
    setText('');
    ref.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={answering ? 'Type your answer…' : 'Ask me to do something on this page…'}
        rows={2}
        maxLength={8000}
        aria-label="Message"
        autoFocus
      />
      {answering && (
        <button type="submit" className="button button-primary" disabled={!text.trim()}>
          Answer
        </button>
      )}
      {busy ? (
        <button type="button" className="button button-stop" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button type="submit" className="button button-primary" disabled={disabled || !text.trim()}>
          Send
        </button>
      )}
    </form>
  );
}
