// Content script. Injected on demand into the tab the agent operates on and
// answers tool requests from the side panel.

import type { ToolResult } from '@shared/protocol';
import type { ContentRequest, ContentResponse } from '../types/messages';
import { clearInput, click, ContentError, pageHasText, scrollPage, selectOption, typeText } from './actions';
import { ElementRegistry, findElements, listLinks, snapshot } from './dom';
import { DEFAULT_MAX_CHARS, extractPageContent } from './extract';

declare global {
  interface Window {
    __browserAgentContentLoaded?: boolean;
  }
}

const registry = new ElementRegistry();

const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown, fallback: number): number => (typeof v === 'number' ? v : fallback);
const bool = (v: unknown): boolean => v === true;

/** Runs a page-side tool. Exported for tests. */
export function runTool(tool: string, input: Record<string, unknown>, confirmed = false): ToolResult {
  const ok = (result: unknown): ToolResult => ({ success: true, tool, result });
  try {
    switch (tool) {
      case 'get_page_content':
        return ok(extractPageContent(document, num(input.max_chars, DEFAULT_MAX_CHARS)));
      case 'get_elements':
        return ok(
          snapshot(document, registry, {
            scope: input.scope === 'viewport' ? 'viewport' : 'page',
            max: num(input.max_elements, 150),
          }),
        );
      case 'find_element': {
        const matches = findElements(document, registry, str(input.description), str(input.role) || undefined);
        return ok({ matches, note: matches.length === 0 ? 'No matching elements. Try get_elements.' : undefined });
      }
      case 'get_links':
        return ok(listLinks(document, registry, num(input.max_links, 100)));
      case 'click_element':
        return ok(click(registry, str(input.element_id), confirmed));
      case 'type_text':
        return ok(
          typeText(registry, str(input.element_id), str(input.text), {
            append: bool(input.append),
            pressEnter: bool(input.press_enter),
            confirmed,
          }),
        );
      case 'clear_input':
        return ok(clearInput(registry, str(input.element_id)));
      case 'select_option':
        return ok(selectOption(registry, str(input.element_id), str(input.option)));
      case 'scroll_page':
        return ok(
          scrollPage(registry, {
            direction: (['down', 'up', 'top', 'bottom'] as const).find((d) => d === input.direction) ?? 'down',
            amount: num(input.amount, 0.8),
            ...(str(input.element_id) ? { elementId: str(input.element_id) } : {}),
          }),
        );
      case 'page_has_text':
        return ok({ found: pageHasText(str(input.text)) });
      default:
        return {
          success: false,
          tool,
          error: { code: 'UNKNOWN_TOOL', message: `The page cannot run "${tool}".` },
        };
    }
  } catch (err) {
    if (err instanceof ContentError) {
      return {
        success: false,
        tool,
        error: { code: err.code, message: err.message, ...(err.details ? { details: err.details } : {}) },
      };
    }
    return {
      success: false,
      tool,
      error: { code: 'EXECUTION_FAILED', message: err instanceof Error ? err.message : String(err) },
    };
  }
}

// Re-injection into a page that already has the script is a no-op.
if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage && !window.__browserAgentContentLoaded) {
  window.__browserAgentContentLoaded = true;

  chrome.runtime.onMessage.addListener(
    (message: ContentRequest, _sender, sendResponse: (response: ContentResponse) => void) => {
      if (message.kind === 'browser-agent/ping') {
        sendResponse({ kind: 'browser-agent/pong' });
      } else if (message.kind === 'browser-agent/tool') {
        sendResponse(runTool(message.tool, message.input, message.confirmed === true));
      }
    },
  );
}
