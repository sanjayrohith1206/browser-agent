import type { ServerEvent } from '@shared/protocol';
import { describe, expect, it } from 'vitest';
import { turnFromHistory, turnsReducer, type Turn, type TurnAction } from './turns';

let seq = 0;
function ev(event: Record<string, unknown>): TurnAction {
  seq += 1;
  return {
    type: 'event',
    id: 't1',
    event: { seq, task_id: 'task', timestamp: '2026-01-01T00:00:00Z', ...event } as ServerEvent,
  };
}

function run(actions: TurnAction[]): Turn {
  seq = 0;
  const turns = [
    { type: 'started', id: 't1', prompt: 'Summarize', pageTitle: 'Page' } as const,
    { type: 'task_created', id: 't1', taskId: 'task' } as const,
    ...actions,
  ].reduce(turnsReducer, [] as Turn[]);
  return turns[0]!;
}

describe('turnsReducer', () => {
  it('builds the activity timeline and streamed answer', () => {
    const turn = run([
      ev({ type: 'task_status', status: 'queued' }),
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'agent_thinking', message: 'Need the page text.' }),
      ev({ type: 'agent_message_delta', message_id: 'm0', delta: 'Let me read it.' }),
      ev({ type: 'agent_message', message_id: 'm0', message: 'Let me read it.' }),
      ev({ type: 'tool_start', call_id: 'c1', tool: 'get_page_content', input: {}, message: 'Reading the page' }),
      ev({ type: 'tool_result', call_id: 'c1', tool: 'get_page_content', success: true, message: 'Read “Page”' }),
      ev({ type: 'agent_message_delta', message_id: 'm1', delta: 'The page ' }),
      ev({ type: 'agent_message_delta', message_id: 'm1', delta: 'is about X.' }),
    ]);

    expect(turn.status).toBe('running');
    expect(turn.activity.map((a) => [a.kind, a.label, a.state, a.detail])).toEqual([
      ['understanding', 'Understanding your request', 'done', undefined],
      // Reasoning is attached to the step it led to.
      ['tool', 'Read “Page”', 'done', 'Need the page text.'],
      ['answer', 'Preparing the answer', 'active', undefined],
    ]);
    expect(turn.messages).toEqual([
      { id: 'm0', text: 'Let me read it.', complete: true },
      { id: 'm1', text: 'The page is about X.', complete: false },
    ]);
  });

  it('finalizes on completion and ignores replayed events', () => {
    seq = 0;
    const base = run([
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'agent_message_delta', message_id: 'm1', delta: 'Hi' }),
      ev({ type: 'agent_message', message_id: 'm1', message: 'Hi there' }),
      ev({ type: 'task_status', status: 'completed' }),
    ]);
    expect(base.status).toBe('completed');
    expect(base.messages).toEqual([{ id: 'm1', text: 'Hi there', complete: true }]);
    expect(base.activity.every((a) => a.state === 'done')).toBe(true);

    const replay: TurnAction = {
      type: 'event',
      id: 't1',
      event: { seq: 2, task_id: 'task', timestamp: '', type: 'agent_message_delta', message_id: 'm1', delta: 'dup' },
    };
    expect(turnsReducer([base], replay)[0]).toBe(base);
  });

  it('marks failed tools and failed tasks', () => {
    const turn = run([
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'tool_start', call_id: 'c1', tool: 'get_page_content', input: {}, message: 'Reading the page' }),
      ev({
        type: 'tool_result',
        call_id: 'c1',
        tool: 'get_page_content',
        success: false,
        message: "This page can't be read by extensions",
        error: { code: 'PAGE_NOT_SCRIPTABLE', message: 'chrome:// page' },
      }),
      ev({ type: 'task_status', status: 'failed', message: 'Could not finish.' }),
    ]);
    const tool = turn.activity.find((a) => a.kind === 'tool');
    expect(tool).toMatchObject({ state: 'failed', detail: 'chrome:// page' });
    expect(turn.status).toBe('failed');
    expect(turn.error).toBe('Could not finish.');
  });

  it('discards a message when the server regenerates it', () => {
    const turn = run([
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'agent_message_delta', message_id: 'm1', delta: 'Half an ans' }),
      ev({ type: 'agent_message_reset', message_id: 'm1' }),
      ev({ type: 'agent_message_delta', message_id: 'm1', delta: 'Full answer.' }),
    ]);
    expect(turn.messages).toEqual([{ id: 'm1', text: 'Full answer.', complete: false }]);
  });

  it('records client-side failures', () => {
    const turns = turnsReducer(
      [run([ev({ type: 'task_status', status: 'running' })])],
      { type: 'failed', id: 't1', message: 'Lost connection' },
    );
    expect(turns[0]).toMatchObject({ status: 'failed', error: 'Lost connection' });
    expect(turns[0]!.activity[0]!.state).toBe('failed');
  });
  it('shows a question until the user answers it', () => {
    const asked = run([
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'agent_thinking', message: 'The size matters here.' }),
      ev({ type: 'user_question', request_id: 'q1', question: 'Which size?', options: ['S', 'L'] }),
      ev({ type: 'task_status', status: 'waiting' }),
    ]);
    expect(asked.status).toBe('waiting');
    expect(asked.question).toEqual({ requestId: 'q1', question: 'Which size?', options: ['S', 'L'] });
    expect(asked.activity.at(-1)).toMatchObject({
      kind: 'question',
      label: 'Asked you: Which size?',
      state: 'active',
      detail: 'The size matters here.',
    });

    seq = 4;
    const answered = [
      ev({ type: 'user_reply', request_id: 'q1', message: 'L' }),
      ev({ type: 'task_status', status: 'running' }),
    ].reduce(turnsReducer, [asked])[0]!;
    expect(answered.status).toBe('running');
    expect(answered.question).toBeNull();
    expect(answered.activity.at(-1)).toMatchObject({ state: 'done', detail: 'You answered: L' });
  });

  it('drops an unanswered question when the task ends', () => {
    const turn = run([
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'user_question', request_id: 'q1', question: 'Which size?' }),
      ev({ type: 'task_status', status: 'cancelled' }),
    ]);
    expect(turn.question).toBeNull();
    expect(turn.activity.at(-1)).toMatchObject({ kind: 'question', state: 'failed' });
  });
  it('pauses a high-impact step for approval, then records the decision', () => {
    const turn = run([
      ev({ type: 'task_status', status: 'running' }),
      ev({ type: 'tool_start', call_id: 'c1', tool: 'click_element', input: {}, message: 'Clicking' }),
      ev({
        type: 'tool_result',
        call_id: 'c1',
        tool: 'click_element',
        success: false,
        message: 'Paused for your approval',
        error: { code: 'CONFIRMATION_REQUIRED', message: 'purchase' },
      }),
      ev({
        type: 'confirmation_request',
        request_id: 'k1',
        message: 'Click the “Place order” button',
        reason: 'This would make a purchase or payment.',
      }),
      ev({ type: 'task_status', status: 'waiting' }),
    ]);
    expect(turn.activity.find((a) => a.id === 'c1')).toMatchObject({ state: 'done', label: 'Paused for your approval' });
    expect(turn.confirmation).toEqual({
      requestId: 'k1',
      action: 'Click the “Place order” button',
      reason: 'This would make a purchase or payment.',
    });

    seq = 5;
    const declined = [ev({ type: 'confirmation_result', request_id: 'k1', approved: false })].reduce(turnsReducer, [
      turn,
    ])[0]!;
    expect(declined.confirmation).toBeNull();
    expect(declined.activity.at(-1)).toMatchObject({
      label: 'You declined: Click the “Place order” button',
      state: 'failed',
    });
  });

  it('rebuilds a past task from its stored events', () => {
    const events = [
      { type: 'task_status', status: 'queued' },
      { type: 'task_status', status: 'running' },
      { type: 'user_question', request_id: 'q1', question: 'Which size?' },
      { type: 'agent_message', message_id: 'final_1', message: 'Ordered size M.' },
      { type: 'task_status', status: 'completed' },
    ].map((e, i) => ({ seq: i + 1, task_id: 'x', timestamp: '2026-01-01T00:00:00Z', ...e }) as ServerEvent);
    const turn = turnFromHistory('x', 'Buy the shirt', events);
    expect(turn).toMatchObject({ prompt: 'Buy the shirt', status: 'completed', question: null });
    expect(turn.messages).toEqual([{ id: 'final_1', text: 'Ordered size M.', complete: true }]);
  });
});
