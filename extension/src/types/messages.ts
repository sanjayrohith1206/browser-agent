// Messages between extension pages and the content script.

import type { ToolResult } from '@shared/protocol';

export type ContentRequest =
  | { kind: 'browser-agent/ping' }
  | { kind: 'browser-agent/tool'; tool: string; input: Record<string, unknown>; confirmed?: boolean };

export type ContentResponse = { kind: 'browser-agent/pong' } | ToolResult;
