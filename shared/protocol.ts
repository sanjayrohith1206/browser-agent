// Wire protocol between the extension and the Python backend.
// The Python mirror lives in backend/app/agent/events.py and
// backend/app/tools/types.py; keep the two in sync.

import toolManifest from './tools.json';

export type ToolName = (typeof toolManifest.tools)[number]['name'];
export type ToolRisk = 'read' | 'interact' | 'high_impact';
/** Where a tool runs: in the extension, or in the backend's agent loop
 * (ask_user, finish_task). The extension only ever receives browser tools. */
export type ToolRunner = 'browser' | 'agent';

export interface ToolDefinition {
  name: ToolName;
  description: string;
  risk: ToolRisk;
  runs_in?: ToolRunner;
  parameters: Record<string, unknown>;
}

export const TOOL_DEFINITIONS = toolManifest.tools as ToolDefinition[];

export interface ToolError {
  code: ToolErrorCode;
  message: string;
  /** CONFIRMATION_REQUIRED: what the action is and why it needs approval,
   * in words the user will see. */
  details?: { action: string; reason: string };
}

export type ToolErrorCode =
  | 'INVALID_INPUT'
  | 'UNKNOWN_TOOL'
  | 'TAB_NOT_FOUND'
  | 'PAGE_NOT_SCRIPTABLE'
  | 'CONTENT_SCRIPT_UNAVAILABLE'
  | 'TIMEOUT'
  | 'EXECUTION_FAILED'
  | 'BROWSER_DISCONNECTED'
  | 'ELEMENT_NOT_FOUND'
  | 'ELEMENT_NOT_INTERACTABLE'
  | 'OPTION_NOT_FOUND'
  | 'CONFIRMATION_REQUIRED'
  | 'SENSITIVE_FIELD'
  | 'INVALID_URL'
  | 'NAVIGATION_FAILED'
  | 'TAB_NOT_VISIBLE'
  | 'STEP_LIMIT'
  | 'NOT_RUN'
  | 'DOMAIN_BLOCKED'
  | 'USER_DECLINED';

export interface ToolResult<T = unknown> {
  success: boolean;
  tool: string;
  result?: T;
  error?: ToolError;
  /** Recovery guidance for the model, added by the backend. */
  hint?: string;
}

export interface CurrentPage {
  tab_id: number;
  window_id: number;
  url: string;
  title: string;
  status: 'loading' | 'complete' | 'unknown';
}

/** One tab in list_tabs. */
export interface TabSummary {
  tab_id: number;
  url: string;
  title: string;
  active: boolean;
  is_task_tab: boolean;
  opened_by_agent: boolean;
}

export interface PageContent {
  url: string;
  title: string;
  description: string;
  lang: string;
  headings: { level: number; text: string }[];
  text: string;
  total_chars: number;
  truncated: boolean;
}

/** One interactive element in a page snapshot. Empty fields are omitted. */
export interface ElementInfo {
  id: string;
  tag: string;
  role: string;
  name?: string; // accessible name
  text?: string;
  type?: string;
  placeholder?: string;
  value?: string;
  href?: string;
  context?: string; // nearby label / heading text
  checked?: boolean;
  selected?: boolean;
  disabled?: boolean;
  expanded?: boolean;
  options?: string[]; // <select> option labels
  in_viewport: boolean;
}

export interface PageElements {
  url: string;
  title: string;
  elements: ElementInfo[];
  total: number;
  truncated: boolean;
  scroll: { y: number; max_y: number; viewport_height: number };
}

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'waiting' // paused until the user answers
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

export const TERMINAL_STATUSES: readonly TaskStatus[] = [
  'completed',
  'failed',
  'cancelled',
  'interrupted',
];

interface EventBase {
  seq: number;
  task_id: string;
  timestamp: string;
}

export type ServerEvent = EventBase &
  (
    | { type: 'task_status'; status: TaskStatus; message?: string }
    | { type: 'agent_thinking'; message: string }
    | { type: 'agent_message_delta'; message_id: string; delta: string }
    // The response is being regenerated: discard its streamed text.
    | { type: 'agent_message_reset'; message_id: string }
    | { type: 'agent_message'; message_id: string; message: string }
    | {
        type: 'tool_start';
        call_id: string;
        tool: string;
        input: Record<string, unknown>;
        message: string;
        /** The user approved this call: run it even if it is high impact. */
        confirmed?: boolean;
      }
    | {
        type: 'tool_result';
        call_id: string;
        tool: string;
        success: boolean;
        message: string;
        error?: ToolError;
      }
    | { type: 'user_question'; request_id: string; question: string; options?: string[] }
    | { type: 'user_reply'; request_id: string; message: string }
    // An action waiting for the user's approval.
    | { type: 'confirmation_request'; request_id: string; message: string; reason: string }
    | { type: 'confirmation_result'; request_id: string; approved: boolean }
    | { type: 'error'; message: string; code?: string }
  );

export type ClientMessage =
  | { type: 'tool_result'; call_id: string; result: ToolResult }
  | { type: 'user_reply'; request_id: string; answer: string }
  | { type: 'confirmation_reply'; request_id: string; approved: boolean }
  | { type: 'stop' };

export type TaskOutcome = 'done' | 'partial' | 'blocked';

export interface PageContext {
  tab_id: number;
  url: string;
  title: string;
}

export interface CreateTaskRequest {
  prompt: string;
  page?: PageContext;
}

export interface TabInfo {
  tab_id: number;
  url: string;
  title: string;
  opened_by_agent: boolean;
}

export interface TaskStep {
  call_id: string;
  tool: string;
  input: Record<string, unknown>;
  tab_id?: number;
  success?: boolean;
  message?: string;
  error?: ToolError;
  started_at: string;
  finished_at?: string;
}

export interface Task {
  id: string;
  goal: string;
  status: TaskStatus;
  current_url?: string;
  current_tab?: number;
  tabs: TabInfo[];
  steps: TaskStep[];
  result?: string;
  error?: string;
  outcome?: TaskOutcome;
  created_at: string;
  updated_at: string;
}

/** A task as listed in history. */
export interface TaskSummary {
  id: string;
  goal: string;
  status: TaskStatus;
  outcome?: TaskOutcome;
  current_url?: string;
  created_at: string;
  updated_at: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface SignedIn {
  token: string;
  user: User;
}

export interface MemoryItem {
  id: string;
  content: string;
  created_at: string;
  updated_at: string;
}
