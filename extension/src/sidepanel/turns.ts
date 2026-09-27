// Conversation state: one turn per user request, built from server events.

import { TERMINAL_STATUSES, type ServerEvent, type TaskStatus } from '@shared/protocol';

export type ActivityState = 'active' | 'done' | 'failed';

export interface ActivityItem {
  id: string;
  kind: 'understanding' | 'tool' | 'answer' | 'question' | 'confirmation';
  label: string;
  detail?: string;
  state: ActivityState;
}

export interface AgentMessage {
  id: string;
  text: string;
  complete: boolean;
}

/** A question the agent is waiting on the user to answer. */
export interface PendingQuestion {
  requestId: string;
  question: string;
  options: string[];
}

/** An action waiting for the user's approval. */
export interface PendingConfirmation {
  requestId: string;
  action: string;
  reason: string;
}

export interface Turn {
  id: string;
  prompt: string;
  pageTitle: string;
  taskId: string | null;
  status: TaskStatus | 'starting';
  activity: ActivityItem[];
  messages: AgentMessage[];
  error: string | null;
  lastSeq: number;
  /** Reasoning not yet shown: it becomes the detail of the next step. */
  pendingThought: string | null;
  question: PendingQuestion | null;
  confirmation: PendingConfirmation | null;
}

export type TurnAction =
  | { type: 'started'; id: string; prompt: string; pageTitle: string }
  | { type: 'task_created'; id: string; taskId: string }
  | { type: 'event'; id: string; event: ServerEvent }
  | { type: 'failed'; id: string; message: string };

export function isTerminal(status: Turn['status']): boolean {
  return status !== 'starting' && TERMINAL_STATUSES.includes(status);
}

export function turnsReducer(turns: Turn[], action: TurnAction): Turn[] {
  if (action.type === 'started') {
    return [
      ...turns,
      {
        id: action.id,
        prompt: action.prompt,
        pageTitle: action.pageTitle,
        taskId: null,
        status: 'starting',
        activity: [],
        messages: [],
        error: null,
        lastSeq: 0,
        pendingThought: null,
        question: null,
        confirmation: null,
      },
    ];
  }
  return turns.map((t) => (t.id === action.id ? reduceTurn(t, action) : t));
}

function reduceTurn(turn: Turn, action: Exclude<TurnAction, { type: 'started' }>): Turn {
  switch (action.type) {
    case 'task_created':
      return { ...turn, taskId: action.taskId, status: 'queued' };
    case 'failed':
      return {
        ...turn,
        status: 'failed',
        error: action.message,
        activity: settle(turn.activity, 'failed'),
        question: null,
        confirmation: null,
      };
    case 'event':
      if (action.event.seq <= turn.lastSeq) return turn; // replayed duplicate
      return { ...applyEvent(turn, action.event), lastSeq: action.event.seq };
  }
}

function applyEvent(turn: Turn, event: ServerEvent): Turn {
  switch (event.type) {
    case 'task_status': {
      if (event.status === 'running') {
        return {
          ...turn,
          status: 'running',
          activity: upsert(turn.activity, {
            id: 'understanding',
            kind: 'understanding',
            label: 'Understanding your request',
            state: 'active',
          }),
        };
      }
      if (TERMINAL_STATUSES.includes(event.status)) {
        return {
          ...turn,
          status: event.status,
          error: event.status === 'completed' ? null : (event.message ?? turn.error),
          activity: settle(turn.activity, event.status === 'completed' ? 'done' : 'failed'),
          pendingThought: null,
          question: null,
          confirmation: null,
        };
      }
      return { ...turn, status: event.status };
    }
    case 'agent_thinking':
      return {
        ...turn,
        pendingThought: turn.pendingThought ? `${turn.pendingThought}\n\n${event.message}` : event.message,
      };
    case 'tool_start': {
      // Text written just before a tool call was a preamble, not the answer;
      // its reasoning belongs to this step instead.
      const last = turn.activity.at(-1);
      const preamble = last?.kind === 'answer' ? last : undefined;
      const thought = turn.pendingThought ?? preamble?.detail;
      return {
        ...turn,
        pendingThought: null,
        activity: [
          ...settle(preamble ? turn.activity.slice(0, -1) : turn.activity, 'done'),
          {
            id: event.call_id,
            kind: 'tool',
            label: event.message,
            state: 'active',
            ...(thought ? { detail: thought } : {}),
          },
        ],
      };
    }
    case 'tool_result': {
      // A high-impact step pauses for approval: not a failure.
      const paused = event.error?.code === 'CONFIRMATION_REQUIRED';
      return {
        ...turn,
        activity: turn.activity.map((a) =>
          a.id === event.call_id
            ? {
                ...a,
                label: event.message,
                state: event.success || paused ? 'done' : 'failed',
                ...(event.error && !paused ? { detail: event.error.message } : {}),
              }
            : a,
        ),
      };
    }
    case 'agent_message_delta': {
      const exists = turn.activity.some((a) => a.id === `answer-${event.message_id}`);
      const activity: ActivityItem[] = exists
        ? turn.activity
        : [
            ...settle(turn.activity, 'done'),
            {
              id: `answer-${event.message_id}`,
              kind: 'answer',
              label: 'Preparing the answer',
              state: 'active',
              ...(turn.pendingThought ? { detail: turn.pendingThought } : {}),
            },
          ];
      return {
        ...turn,
        activity,
        pendingThought: exists ? turn.pendingThought : null,
        messages: appendDelta(turn.messages, event.message_id, event.delta),
      };
    }
    case 'agent_message_reset':
      return { ...turn, messages: turn.messages.filter((m) => m.id !== event.message_id) };
    case 'agent_message':
      return {
        ...turn,
        activity: turn.activity.map((a) =>
          a.id === `answer-${event.message_id}` ? { ...a, state: 'done' } : a,
        ),
        messages: finalize(turn.messages, event.message_id, event.message),
      };
    case 'user_question':
      return {
        ...turn,
        pendingThought: null,
        question: { requestId: event.request_id, question: event.question, options: event.options ?? [] },
        activity: [
          ...settle(turn.activity, 'done'),
          {
            id: event.request_id,
            kind: 'question',
            label: `Asked you: ${event.question}`,
            state: 'active',
            ...(turn.pendingThought ? { detail: turn.pendingThought } : {}),
          },
        ],
      };
    case 'user_reply':
      return {
        ...turn,
        question: turn.question?.requestId === event.request_id ? null : turn.question,
        activity: turn.activity.map((a) =>
          a.id === event.request_id ? { ...a, state: 'done', detail: `You answered: ${event.message}` } : a,
        ),
      };
    case 'confirmation_request':
      return {
        ...turn,
        confirmation: { requestId: event.request_id, action: event.message, reason: event.reason },
        activity: [
          ...settle(turn.activity, 'done'),
          {
            id: event.request_id,
            kind: 'confirmation',
            label: `Asked for your approval: ${event.message}`,
            state: 'active',
            detail: event.reason,
          },
        ],
      };
    case 'confirmation_result':
      return {
        ...turn,
        confirmation: turn.confirmation?.requestId === event.request_id ? null : turn.confirmation,
        activity: turn.activity.map((a) =>
          a.id === event.request_id
            ? {
                ...a,
                label: `${event.approved ? 'You approved' : 'You declined'}: ${a.label.replace(/^Asked for your approval: /, '')}`,
                state: event.approved ? 'done' : 'failed',
              }
            : a,
        ),
      };
    case 'error':
      return { ...turn, error: event.message };
  }
}

function settle(items: ActivityItem[], state: Exclude<ActivityState, 'active'>): ActivityItem[] {
  return items.map((a) => (a.state === 'active' ? { ...a, state } : a));
}

function upsert(items: ActivityItem[], item: ActivityItem): ActivityItem[] {
  return items.some((a) => a.id === item.id) ? items : [...items, item];
}

function appendDelta(messages: AgentMessage[], id: string, delta: string): AgentMessage[] {
  if (!messages.some((m) => m.id === id)) return [...messages, { id, text: delta, complete: false }];
  return messages.map((m) => (m.id === id ? { ...m, text: m.text + delta } : m));
}

function finalize(messages: AgentMessage[], id: string, text: string): AgentMessage[] {
  if (!messages.some((m) => m.id === id)) return [...messages, { id, text, complete: true }];
  return messages.map((m) => (m.id === id ? { ...m, text, complete: true } : m));
}

/** Rebuilds a past task's conversation from its stored events. */
export function turnFromHistory(taskId: string, goal: string, events: ServerEvent[]): Turn {
  const actions: TurnAction[] = [
    { type: 'started', id: taskId, prompt: goal, pageTitle: '' },
    { type: 'task_created', id: taskId, taskId },
    ...events.map((event): TurnAction => ({ type: 'event', id: taskId, event })),
  ];
  const turn = actions.reduce(turnsReducer, [] as Turn[])[0]!;
  // A past task can't be answered or approved any more.
  return { ...turn, question: null, confirmation: null };
}
