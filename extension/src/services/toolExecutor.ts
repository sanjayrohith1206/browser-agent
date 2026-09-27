// Executes browser tools requested by the agent. The task works in one tab
// at a time (the task tab) and can open, switch between and close tabs in
// its window. Tab-level tools (navigation, tabs, wait, screenshots) run here
// with Chrome APIs; page-level tools run in the content script.

import type { CurrentPage, TabSummary, ToolError, ToolErrorCode, ToolResult } from '@shared/protocol';
import type { ContentRequest, ContentResponse } from '../types/messages';

/** The slice of the Chrome API the executor uses (injectable for tests). */
export interface BrowserApi {
  getTab(tabId: number): Promise<chrome.tabs.Tab>;
  updateTab(tabId: number, props: { url?: string; active?: boolean }): Promise<void>;
  createTab(props: { windowId: number; url: string; active: boolean; openerTabId?: number }): Promise<chrome.tabs.Tab>;
  queryTabs(windowId: number): Promise<chrome.tabs.Tab[]>;
  removeTab(tabId: number): Promise<void>;
  captureVisibleTab(windowId: number): Promise<string>;
  sendMessage(tabId: number, message: ContentRequest): Promise<ContentResponse | undefined>;
  injectContentScript(tabId: number): Promise<void>;
  sleep(ms: number): Promise<void>;
}

export const chromeBrowserApi: BrowserApi = {
  getTab: (tabId) => chrome.tabs.get(tabId),
  updateTab: async (tabId, props) => {
    await chrome.tabs.update(tabId, props);
  },
  createTab: (props) => chrome.tabs.create(props),
  queryTabs: (windowId) => chrome.tabs.query({ windowId }),
  removeTab: (tabId) => chrome.tabs.remove(tabId),
  captureVisibleTab: (windowId) => chrome.tabs.captureVisibleTab(windowId, { format: 'jpeg', quality: 70 }),
  sendMessage: (tabId, message) =>
    chrome.tabs.sendMessage<ContentRequest, ContentResponse | undefined>(tabId, message, {
      frameId: 0,
    }),
  injectContentScript: async (tabId) => {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  },
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

// Pages Chrome never lets extensions script.
const RESTRICTED_URL = [
  /^chrome:/i,
  /^chrome-extension:/i,
  /^chrome-search:/i,
  /^edge:/i,
  /^about:/i,
  /^view-source:/i,
  /^devtools:/i,
  /^https:\/\/chromewebstore\.google\.com\//i,
  /^https:\/\/chrome\.google\.com\/webstore/i,
];

export function isRestrictedUrl(url: string | undefined): boolean {
  return !url || RESTRICTED_URL.some((re) => re.test(url));
}

const PAGE_TOOLS = new Set(['get_page_content', 'get_elements', 'find_element', 'get_links', 'scroll_page']);
const ACTION_TOOLS = new Set(['click_element', 'type_text', 'clear_input', 'select_option']);

const CONTENT_TIMEOUT_MS = 20_000;
const NAVIGATION_TIMEOUT_MS = 20_000;
const SETTLE_DELAY_MS = 400;
const SETTLE_TIMEOUT_MS = 15_000;

interface Settled {
  navigated: boolean;
  tab_id: number;
  url: string;
  title: string;
  still_loading?: true;
}

export class ToolExecutor {
  /** The tab the task is working in. */
  private tabId: number;
  private windowId: number | null = null;
  /** Tabs this task opened; it may close these without asking. */
  private readonly opened = new Set<number>();
  /** Tabs worked in before the current one, most recent last. */
  private readonly previous: number[] = [];

  constructor(
    tabId: number,
    private readonly api: BrowserApi = chromeBrowserApi,
  ) {
    this.tabId = tabId;
  }

  get currentTabId(): number {
    return this.tabId;
  }

  /** Runs a tool. `confirmed` means the user approved this call, so a
   * high-impact action may go ahead. */
  async execute(tool: string, input: Record<string, unknown>, confirmed = false): Promise<ToolResult> {
    try {
      if (tool === 'get_current_page') return ok(tool, await this.currentPage());
      if (tool === 'navigate') return ok(tool, await this.navigate(String(input.url ?? '')));
      if (tool === 'wait') return ok(tool, await this.wait(input));
      if (tool === 'take_screenshot') return ok(tool, await this.screenshot());
      if (tool === 'list_tabs') return ok(tool, await this.listTabs());
      if (tool === 'open_tab') return ok(tool, await this.openTab(String(input.url ?? '')));
      if (tool === 'switch_tab') return ok(tool, await this.switchTab(Number(input.tab_id)));
      if (tool === 'close_tab') return ok(tool, await this.closeTab(Number(input.tab_id), confirmed));
      if (PAGE_TOOLS.has(tool)) return await this.inPage(tool, input);
      if (ACTION_TOOLS.has(tool)) return await this.act(tool, input, confirmed);
      return fail(tool, 'UNKNOWN_TOOL', `This version of the extension can't run "${tool}".`);
    } catch (err) {
      if (err instanceof ToolFailure) return fail(tool, err.code, err.message, err.details);
      return fail(tool, 'EXECUTION_FAILED', err instanceof Error ? err.message : String(err));
    }
  }

  // ------------------------------------------------------------ tab tools

  private async tab(): Promise<chrome.tabs.Tab> {
    try {
      const tab = await this.api.getTab(this.tabId);
      this.windowId = tab.windowId;
      return tab;
    } catch {
      throw new ToolFailure(
        'TAB_NOT_FOUND',
        'The tab this task was working in has been closed. Use list_tabs, then switch_tab or open_tab.',
      );
    }
  }

  private async window(): Promise<number> {
    if (this.windowId === null) await this.tab();
    return this.windowId!;
  }

  private async currentPage(): Promise<CurrentPage> {
    const tab = await this.tab();
    return {
      tab_id: this.tabId,
      window_id: tab.windowId,
      url: tab.url ?? '',
      title: tab.title ?? '',
      status: tab.status === 'loading' || tab.status === 'complete' ? tab.status : 'unknown',
    };
  }

  private async navigate(raw: string): Promise<CurrentPage & { loaded: boolean }> {
    const url = webUrl(raw);
    try {
      await this.api.updateTab(this.tabId, { url: url.toString() });
    } catch (err) {
      throw new ToolFailure('NAVIGATION_FAILED', err instanceof Error ? err.message : String(err));
    }
    await this.api.sleep(250);
    const loaded = await this.waitForLoad(NAVIGATION_TIMEOUT_MS);
    return { ...(await this.currentPage()), loaded };
  }

  private async listTabs(): Promise<{ tabs: TabSummary[]; current_tab_id: number }> {
    const tabs = await this.api.queryTabs(await this.window());
    return {
      tabs: tabs
        .filter((t) => t.id !== undefined)
        .map((t) => ({
          tab_id: t.id!,
          url: t.url ?? '',
          title: t.title ?? '',
          active: t.active,
          is_task_tab: t.id === this.tabId,
          opened_by_agent: this.opened.has(t.id!),
        })),
      current_tab_id: this.tabId,
    };
  }

  private async openTab(raw: string): Promise<CurrentPage & { loaded: boolean; opened_by_agent: true }> {
    const url = webUrl(raw);
    const opener = this.tabId;
    let created: chrome.tabs.Tab;
    try {
      created = await this.api.createTab({ windowId: await this.window(), url: url.toString(), active: true, openerTabId: opener });
    } catch (err) {
      throw new ToolFailure('NAVIGATION_FAILED', err instanceof Error ? err.message : String(err));
    }
    if (created.id === undefined) throw new ToolFailure('NAVIGATION_FAILED', 'The tab could not be opened.');
    this.moveTo(created.id);
    this.opened.add(created.id);
    await this.api.sleep(250);
    const loaded = await this.waitForLoad(NAVIGATION_TIMEOUT_MS);
    return { ...(await this.currentPage()), loaded, opened_by_agent: true };
  }

  private async switchTab(tabId: number): Promise<CurrentPage> {
    let target: chrome.tabs.Tab;
    try {
      target = await this.api.getTab(tabId);
    } catch {
      throw new ToolFailure('TAB_NOT_FOUND', `There is no tab ${tabId}. Use list_tabs to see the open tabs.`);
    }
    if (target.windowId !== (await this.window().catch(() => target.windowId))) {
      throw new ToolFailure('TAB_NOT_FOUND', `Tab ${tabId} is in another window. Use list_tabs to see this window's tabs.`);
    }
    await this.api.updateTab(tabId, { active: true });
    if (tabId !== this.tabId) this.moveTo(tabId);
    return this.currentPage();
  }

  private async closeTab(
    tabId: number,
    confirmed: boolean,
  ): Promise<{ closed_tab_id: number; current_tab: CurrentPage | null }> {
    let target: chrome.tabs.Tab;
    try {
      target = await this.api.getTab(tabId);
    } catch {
      throw new ToolFailure('TAB_NOT_FOUND', `There is no tab ${tabId}.`);
    }
    if (!this.opened.has(tabId) && !confirmed) {
      const title = target.title || target.url || 'untitled';
      throw new ToolFailure(
        'CONFIRMATION_REQUIRED',
        "This tab wasn't opened by the assistant, so closing it needs the user's approval.",
        { action: `Close the tab “${title.length > 60 ? `${title.slice(0, 59)}…` : title}”`, reason: 'close one of your own tabs' },
      );
    }
    await this.api.removeTab(tabId);
    this.opened.delete(tabId);
    const wasCurrent = tabId === this.tabId;
    this.forget(tabId);
    if (!wasCurrent) return { closed_tab_id: tabId, current_tab: await this.currentPage().catch(() => null) };

    // Continue in the tab we came from, or whichever tab is now showing.
    for (let back = this.previous.pop(); back !== undefined; back = this.previous.pop()) {
      try {
        await this.api.getTab(back);
        this.tabId = back;
        await this.api.updateTab(back, { active: true });
        return { closed_tab_id: tabId, current_tab: await this.currentPage() };
      } catch {
        // That one is gone too.
      }
    }
    const showing = (await this.api.queryTabs(this.windowId ?? target.windowId)).find((t) => t.active);
    if (showing?.id !== undefined) {
      this.tabId = showing.id;
      return { closed_tab_id: tabId, current_tab: await this.currentPage() };
    }
    return { closed_tab_id: tabId, current_tab: null };
  }

  private moveTo(tabId: number): void {
    this.previous.push(this.tabId);
    if (this.previous.length > 20) this.previous.shift();
    this.tabId = tabId;
  }

  private forget(tabId: number): void {
    for (let i = this.previous.length - 1; i >= 0; i--) if (this.previous[i] === tabId) this.previous.splice(i, 1);
  }

  private async waitForLoad(timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      if ((await this.tab()).status === 'complete') return true;
      if (Date.now() >= deadline) return false;
      await this.api.sleep(200);
    }
  }

  /** After an action: give the page a moment, and if it started loading a
   * new document, wait for that to finish. */
  private async settle(urlBefore: string): Promise<Settled> {
    await this.api.sleep(SETTLE_DELAY_MS);
    const loaded = await this.waitForLoad(SETTLE_TIMEOUT_MS);
    const tab = await this.tab();
    return {
      navigated: (tab.url ?? '') !== urlBefore,
      tab_id: this.tabId,
      url: tab.url ?? '',
      title: tab.title ?? '',
      ...(loaded ? {} : { still_loading: true as const }),
    };
  }

  private async wait(input: Record<string, unknown>): Promise<{ waited_seconds: number; found?: boolean }> {
    const seconds = typeof input.seconds === 'number' ? input.seconds : 2;
    const text = typeof input.until_text === 'string' && input.until_text.trim() ? input.until_text : null;
    const started = Date.now();
    const deadline = started + seconds * 1000;
    const elapsed = (): number => Math.round((Date.now() - started) / 100) / 10;

    while (Date.now() < deadline) {
      if (text) {
        const res = await this.inPage('page_has_text', { text }).catch(() => null);
        const found = res?.success && (res.result as { found?: boolean } | undefined)?.found;
        if (found) return { waited_seconds: elapsed(), found: true };
      }
      await this.api.sleep(text ? 300 : Math.min(250, Math.max(0, deadline - Date.now())));
    }
    return text ? { waited_seconds: elapsed(), found: false } : { waited_seconds: elapsed() };
  }

  private async screenshot(): Promise<{ image_data_url: string; url: string; title: string }> {
    const tab = await this.tab();
    if (!tab.active) {
      throw new ToolFailure(
        'TAB_NOT_VISIBLE',
        "The task's tab isn't the one showing in its window, so it can't be captured. Continue with text tools instead.",
      );
    }
    const dataUrl = await this.api.captureVisibleTab(tab.windowId);
    return { image_data_url: dataUrl, url: tab.url ?? '', title: tab.title ?? '' };
  }

  // ----------------------------------------------------------- page tools

  /** Runs an action in the page, then waits for the page to settle. */
  private async act(tool: string, input: Record<string, unknown>, confirmed: boolean): Promise<ToolResult> {
    const urlBefore = (await this.tab()).url ?? '';
    let result: ToolResult;
    try {
      result = await this.inPage(tool, input, confirmed);
    } catch (err) {
      // A click that navigates can unload the page before it answers.
      if (!(err instanceof ToolFailure) && isUnloadError(err)) {
        result = { success: true, tool, result: { note: 'The page began loading while the action ran.' } };
      } else {
        throw err;
      }
    }
    if (!result.success) return result;

    const data = (result.result ?? {}) as Record<string, unknown>;
    if (typeof data.open_in_new_tab === 'string') {
      // The link opens a new tab: open it ourselves and continue there.
      const opened = await this.openTab(data.open_in_new_tab);
      delete data.open_in_new_tab;
      return {
        ...result,
        result: {
          ...data,
          navigated: true,
          opened_new_tab: true,
          tab_id: opened.tab_id,
          url: opened.url,
          title: opened.title,
          note: 'The link opened in a new tab, which is now the task tab.',
        },
      };
    }
    return { ...result, result: { ...data, ...(await this.settle(urlBefore)) } };
  }

  /** Runs a tool inside the page, injecting the content script if needed. */
  private async inPage(tool: string, input: Record<string, unknown>, confirmed = false): Promise<ToolResult> {
    const tab = await this.tab();
    if (isRestrictedUrl(tab.url)) {
      throw new ToolFailure(
        'PAGE_NOT_SCRIPTABLE',
        `Chrome does not allow extensions to read or use this page (${tab.url || 'unknown address'}).`,
      );
    }
    await this.ensureContentScript();
    const response = await withTimeout(
      this.api.sendMessage(this.tabId, {
        kind: 'browser-agent/tool',
        tool,
        input,
        ...(confirmed ? { confirmed: true } : {}),
      }),
      CONTENT_TIMEOUT_MS,
    );
    if (!response || !('success' in response)) {
      throw new ToolFailure('CONTENT_SCRIPT_UNAVAILABLE', 'The page did not answer.');
    }
    return response;
  }

  private async ensureContentScript(): Promise<void> {
    try {
      const pong = await this.api.sendMessage(this.tabId, { kind: 'browser-agent/ping' });
      if (pong && 'kind' in pong) return;
    } catch {
      // No listener yet: inject below.
    }
    try {
      await this.api.injectContentScript(this.tabId);
    } catch (err) {
      throw new ToolFailure(
        'PAGE_NOT_SCRIPTABLE',
        `Couldn't access this page: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }
}

function isUnloadError(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return /message port closed|receiving end does not exist|back\/forward cache|page (was )?unloaded/i.test(message);
}

class ToolFailure extends Error {
  constructor(
    readonly code: ToolErrorCode,
    message: string,
    readonly details?: ToolError['details'],
  ) {
    super(message);
  }
}

function webUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ToolFailure('INVALID_URL', `"${raw}" is not a valid address. Use a full http(s) URL.`);
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new ToolFailure('INVALID_URL', 'Only http and https addresses can be opened.');
  }
  return url;
}

function ok(tool: string, result: unknown): ToolResult {
  return { success: true, tool, result };
}

function fail(tool: string, code: ToolErrorCode, message: string, details?: ToolError['details']): ToolResult {
  return { success: false, tool, error: { code, message, ...(details ? { details } : {}) } };
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new ToolFailure('TIMEOUT', 'The page took too long to respond.')),
      ms,
    );
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e: unknown) => {
        clearTimeout(timer);
        reject(e instanceof Error ? e : new Error(String(e)));
      },
    );
  });
}
