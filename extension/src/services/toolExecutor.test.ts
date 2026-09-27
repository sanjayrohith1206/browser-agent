import type { ToolResult } from '@shared/protocol';
import { describe, expect, it, vi } from 'vitest';
import type { ContentRequest, ContentResponse } from '../types/messages';
import { isRestrictedUrl, ToolExecutor, type BrowserApi } from './toolExecutor';

interface FakeTab {
  id: number;
  windowId: number;
  url: string;
  title: string;
  status: 'loading' | 'complete';
  active: boolean;
}

/** A fake browser window (tab 7 is the task tab) + content script.
 * `respond` answers page-side tools. */
function fakeBrowser(opts: {
  tab?: Partial<FakeTab>;
  others?: Partial<FakeTab>[];
  closed?: boolean;
  injected?: boolean;
  injectError?: Error;
  respond?: (msg: Extract<ContentRequest, { kind: 'browser-agent/tool' }>, tab: FakeTab) => ContentResponse | undefined;
}) {
  const tab: FakeTab = {
    id: 7,
    windowId: 1,
    url: 'https://example.com/a',
    title: 'Example',
    status: 'complete',
    active: true,
    ...opts.tab,
  };
  const tabs = new Map<number, FakeTab>(opts.closed ? [] : [[7, tab]]);
  for (const [i, other] of (opts.others ?? []).entries()) {
    const t: FakeTab = { id: 20 + i, windowId: 1, url: 'https://other.test/', title: 'Other', status: 'complete', active: false, ...other };
    tabs.set(t.id, t);
  }
  let nextId = 100;
  let loaded = opts.injected ?? false;
  let pendingLoads = 0; // sleeps until a navigation completes
  const find = (id: number): FakeTab => {
    const t = tabs.get(id);
    if (!t) throw new Error(`No tab with id: ${id}`);
    return t;
  };
  const activate = (id: number): void => {
    for (const t of tabs.values()) if (t.windowId === find(id).windowId) t.active = t.id === id;
  };
  const api = {
    tab,
    tabs,
    injections: 0,
    getTab: vi.fn(async (id: number) => ({ ...find(id) }) as chrome.tabs.Tab),
    updateTab: vi.fn(async (id: number, props: { url?: string; active?: boolean }) => {
      const t = find(id);
      if (props.active) activate(id);
      if (props.url) {
        t.url = props.url;
        t.title = `Title of ${new URL(props.url).pathname}`;
        t.status = 'loading';
        loaded = false;
        pendingLoads = 2;
      }
    }),
    createTab: vi.fn(async (props: { windowId: number; url: string; active: boolean }) => {
      const t: FakeTab = {
        id: nextId++,
        windowId: props.windowId,
        url: props.url,
        title: `Title of ${new URL(props.url).pathname}`,
        status: 'loading',
        active: false,
      };
      tabs.set(t.id, t);
      if (props.active) activate(t.id);
      pendingLoads = 2;
      return { ...t } as chrome.tabs.Tab;
    }),
    queryTabs: vi.fn(async (windowId: number) =>
      [...tabs.values()].filter((t) => t.windowId === windowId).map((t) => ({ ...t }) as chrome.tabs.Tab),
    ),
    removeTab: vi.fn(async (id: number) => {
      find(id);
      tabs.delete(id);
    }),
    captureVisibleTab: vi.fn(async () => 'data:image/jpeg;base64,AAAA'),
    sendMessage: vi.fn(async (id: number, msg: ContentRequest) => {
      if (!loaded) throw new Error('Could not establish connection. Receiving end does not exist.');
      if (msg.kind === 'browser-agent/ping') return { kind: 'browser-agent/pong' } as const;
      return opts.respond?.(msg, find(id));
    }),
    injectContentScript: vi.fn(async () => {
      if (opts.injectError) throw opts.injectError;
      api.injections += 1;
      loaded = true;
    }),
    sleep: vi.fn(async () => {
      if (pendingLoads > 0 && --pendingLoads === 0) for (const t of tabs.values()) t.status = 'complete';
    }),
  };
  return api satisfies BrowserApi;
}

const PAGE_RESULT: ToolResult = { success: true, tool: 'get_page_content', result: { title: 'Example' } };

describe('page tools', () => {
  it('reports the current page from the tabs API', async () => {
    const result = await new ToolExecutor(7, fakeBrowser({})).execute('get_current_page', {});
    expect(result).toEqual({
      success: true,
      tool: 'get_current_page',
      result: { tab_id: 7, window_id: 1, url: 'https://example.com/a', title: 'Example', status: 'complete' },
    });
  });

  it('injects the content script once, then reuses it', async () => {
    const api = fakeBrowser({ respond: () => PAGE_RESULT });
    const executor = new ToolExecutor(7, api);
    expect(await executor.execute('get_page_content', {})).toEqual(PAGE_RESULT);
    expect(await executor.execute('get_elements', {})).toEqual(PAGE_RESULT);
    expect(api.injections).toBe(1);
  });

  it('refuses restricted pages without trying to inject', async () => {
    const api = fakeBrowser({ tab: { url: 'chrome://settings' } });
    const result = await new ToolExecutor(7, api).execute('get_page_content', {});
    expect(result.error?.code).toBe('PAGE_NOT_SCRIPTABLE');
    expect(api.injectContentScript).not.toHaveBeenCalled();
  });

  it('maps injection failures to PAGE_NOT_SCRIPTABLE', async () => {
    const api = fakeBrowser({ injectError: new Error('Cannot access contents of the page') });
    const result = await new ToolExecutor(7, api).execute('get_page_content', {});
    expect(result.error).toEqual({
      code: 'PAGE_NOT_SCRIPTABLE',
      message: "Couldn't access this page: Cannot access contents of the page",
    });
  });

  it('reports closed tabs and silent content scripts', async () => {
    expect((await new ToolExecutor(7, fakeBrowser({ closed: true })).execute('get_current_page', {})).error?.code).toBe(
      'TAB_NOT_FOUND',
    );
    const silent = fakeBrowser({ injected: true, respond: () => undefined });
    expect((await new ToolExecutor(7, silent).execute('get_page_content', {})).error?.code).toBe(
      'CONTENT_SCRIPT_UNAVAILABLE',
    );
  });

  it('rejects unknown tools', async () => {
    const result = await new ToolExecutor(7, fakeBrowser({})).execute('open_new_window', {});
    expect(result.error?.code).toBe('UNKNOWN_TOOL');
  });
});

describe('navigate', () => {
  it('opens http(s) URLs and waits for the load', async () => {
    const api = fakeBrowser({});
    const result = await new ToolExecutor(7, api).execute('navigate', { url: 'https://en.wikipedia.org/wiki/AI' });
    expect(api.updateTab).toHaveBeenCalledWith(7, { url: 'https://en.wikipedia.org/wiki/AI' });
    expect(result).toMatchObject({
      success: true,
      result: { url: 'https://en.wikipedia.org/wiki/AI', status: 'complete', loaded: true },
    });
  });

  it.each(['javascript:alert(1)', 'file:///etc/passwd', 'chrome://settings', 'not a url'])('rejects %s', async (url) => {
    const api = fakeBrowser({});
    const result = await new ToolExecutor(7, api).execute('navigate', { url });
    expect(result.error?.code).toBe('INVALID_URL');
    expect(api.updateTab).not.toHaveBeenCalled();
  });
});

describe('actions', () => {
  it('reports when a click navigates', async () => {
    const api = fakeBrowser({
      injected: true,
      respond: (msg, tab) => {
        // The click starts a navigation, like following a link.
        tab.url = 'https://example.com/results?q=ai';
        tab.status = 'loading';
        return { success: true, tool: msg.tool, result: { clicked: 'button "Search"' } };
      },
    });
    api.sleep.mockImplementation(async () => {
      api.tab.status = 'complete';
    });
    const result = await new ToolExecutor(7, api).execute('click_element', { element_id: 'el_x_1' });
    expect(result).toEqual({
      success: true,
      tool: 'click_element',
      result: {
        clicked: 'button "Search"',
        navigated: true,
        tab_id: 7,
        url: 'https://example.com/results?q=ai',
        title: 'Example',
      },
    });
  });

  it('treats a page that unloads mid-click as a navigation', async () => {
    const api = fakeBrowser({ injected: true });
    api.sendMessage.mockImplementation(async (_id: number, msg: ContentRequest) => {
      if (msg.kind === 'browser-agent/ping') return { kind: 'browser-agent/pong' };
      api.tab.url = 'https://example.com/next';
      throw new Error('The message port closed before a response was received.');
    });
    const result = await new ToolExecutor(7, api).execute('click_element', { element_id: 'el_x_1' });
    expect(result).toMatchObject({ success: true, result: { navigated: true, url: 'https://example.com/next' } });
  });

  it('opens new-tab links in a new tab and continues there', async () => {
    const api = fakeBrowser({
      injected: true,
      respond: (msg) => ({
        success: true,
        tool: msg.tool,
        result: { clicked: 'link "Docs"', open_in_new_tab: 'https://example.com/docs' },
      }),
    });
    const executor = new ToolExecutor(7, api);
    const result = await executor.execute('click_element', { element_id: 'el_x_1' });
    expect(api.createTab).toHaveBeenCalledWith({ windowId: 1, url: 'https://example.com/docs', active: true, openerTabId: 7 });
    expect(result.result).toMatchObject({ navigated: true, opened_new_tab: true, tab_id: 100, url: 'https://example.com/docs' });
    expect(result.result).not.toHaveProperty('open_in_new_tab');
    expect(executor.currentTabId).toBe(100);
  });

  it('passes approval through to the page', async () => {
    const api = fakeBrowser({ injected: true, respond: (msg) => ({ success: true, tool: msg.tool, result: {} }) });
    await new ToolExecutor(7, api).execute('click_element', { element_id: 'el_x_1' }, true);
    const toolCall = api.sendMessage.mock.calls.find(([, m]) => m.kind === 'browser-agent/tool');
    expect(toolCall?.[1]).toMatchObject({ tool: 'click_element', confirmed: true });
  });

  it('passes page-side refusals through untouched', async () => {
    const refusal: ToolResult = {
      success: false,
      tool: 'click_element',
      error: { code: 'CONFIRMATION_REQUIRED', message: 'This would make a purchase.' },
    };
    const result = await new ToolExecutor(7, fakeBrowser({ injected: true, respond: () => refusal })).execute(
      'click_element',
      { element_id: 'el_x_1' },
    );
    expect(result).toEqual(refusal);
  });
});

describe('tabs', () => {
  it('lists the tabs in the task window', async () => {
    const api = fakeBrowser({ others: [{ title: 'Mail', url: 'https://mail.test/' }, { windowId: 2 }] });
    const result = await new ToolExecutor(7, api).execute('list_tabs', {});
    expect(result.result).toEqual({
      tabs: [
        { tab_id: 7, url: 'https://example.com/a', title: 'Example', active: true, is_task_tab: true, opened_by_agent: false },
        { tab_id: 20, url: 'https://mail.test/', title: 'Mail', active: false, is_task_tab: false, opened_by_agent: false },
      ],
      current_tab_id: 7,
    });
  });

  it('opens a tab, works in it, and returns to the previous tab when it is closed', async () => {
    const api = fakeBrowser({ injected: true, respond: (msg, tab) => ({ success: true, tool: msg.tool, result: { url: tab.url } }) });
    const executor = new ToolExecutor(7, api);
    const opened = await executor.execute('open_tab', { url: 'https://shop.test/b' });
    expect(opened.result).toMatchObject({ tab_id: 100, url: 'https://shop.test/b', loaded: true, opened_by_agent: true });
    expect(executor.currentTabId).toBe(100);
    expect((await executor.execute('get_page_content', {})).result).toEqual({ url: 'https://shop.test/b' });

    const closed = await executor.execute('close_tab', { tab_id: 100 });
    expect(closed.result).toMatchObject({ closed_tab_id: 100, current_tab: { tab_id: 7 } });
    expect(executor.currentTabId).toBe(7);
    expect(api.tab.active).toBe(true);
  });

  it('switches to a tab in the same window only', async () => {
    const api = fakeBrowser({ others: [{ title: 'Mail' }, { windowId: 2 }] });
    const executor = new ToolExecutor(7, api);
    expect((await executor.execute('switch_tab', { tab_id: 20 })).result).toMatchObject({ tab_id: 20, title: 'Mail' });
    expect(api.tabs.get(20)?.active).toBe(true);
    expect((await executor.execute('switch_tab', { tab_id: 21 })).error?.code).toBe('TAB_NOT_FOUND');
    expect((await executor.execute('switch_tab', { tab_id: 99 })).error?.code).toBe('TAB_NOT_FOUND');
  });

  it("needs approval to close the user's own tabs", async () => {
    const api = fakeBrowser({ others: [{ title: 'Mail' }] });
    const executor = new ToolExecutor(7, api);
    const refused = await executor.execute('close_tab', { tab_id: 20 });
    expect(refused.error).toMatchObject({
      code: 'CONFIRMATION_REQUIRED',
      details: { action: 'Close the tab “Mail”', reason: 'close one of your own tabs' },
    });
    expect(api.removeTab).not.toHaveBeenCalled();
    expect((await executor.execute('close_tab', { tab_id: 20 }, true)).success).toBe(true);
    expect(api.tabs.has(20)).toBe(false);
  });

  it('rejects non-web addresses for new tabs', async () => {
    const api = fakeBrowser({});
    expect((await new ToolExecutor(7, api).execute('open_tab', { url: 'file:///etc/passwd' })).error?.code).toBe('INVALID_URL');
    expect(api.createTab).not.toHaveBeenCalled();
  });
});

describe('wait and screenshots', () => {
  it('waits until text appears', async () => {
    let checks = 0;
    const api = fakeBrowser({
      injected: true,
      respond: (msg) => ({ success: true, tool: msg.tool, result: { found: ++checks >= 3 } }),
    });
    const result = await new ToolExecutor(7, api).execute('wait', { seconds: 10, until_text: 'Results' });
    expect(result.result).toMatchObject({ found: true });
    expect(checks).toBe(3);
  });

  it('captures the visible tab, but only when the task tab is showing', async () => {
    const api = fakeBrowser({});
    expect((await new ToolExecutor(7, api).execute('take_screenshot', {})).result).toEqual({
      image_data_url: 'data:image/jpeg;base64,AAAA',
      url: 'https://example.com/a',
      title: 'Example',
    });
    const hidden = fakeBrowser({ tab: { active: false } });
    expect((await new ToolExecutor(7, hidden).execute('take_screenshot', {})).error?.code).toBe('TAB_NOT_VISIBLE');
    expect(hidden.captureVisibleTab).not.toHaveBeenCalled();
  });
});

describe('isRestrictedUrl', () => {
  it.each([
    ['chrome://extensions', true],
    ['chrome-extension://abc/page.html', true],
    ['https://chromewebstore.google.com/detail/x', true],
    ['about:blank', true],
    ['', true],
    ['https://example.com', false],
    ['http://localhost:3000', false],
  ])('%s -> %s', (url, expected) => {
    expect(isRestrictedUrl(url)).toBe(expected);
  });
});
