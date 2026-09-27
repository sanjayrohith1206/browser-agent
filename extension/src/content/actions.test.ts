import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { installFakeLayout } from '../test/layout';
import type { PageElements } from '@shared/protocol';
import { interactiveElements } from './dom';
import { runTool } from './index';

beforeAll(installFakeLayout);

// runTool uses the content script's own registry, so look IDs up through a
// snapshot. In these tests every element is in the viewport, so snapshot
// order is document order.
function idOf(selector: string): string {
  const target = document.querySelector(selector)!;
  const index = interactiveElements(document).indexOf(target);
  const snap = runTool('get_elements', {}).result as PageElements;
  const id = snap.elements[index]?.id;
  if (!id) throw new Error(`no element for ${selector}`);
  return id;
}

function run(tool: string, input: Record<string, unknown>) {
  return runTool(tool, input);
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('click_element', () => {
  it('clicks with a realistic event sequence', () => {
    document.body.innerHTML = '<button data-name="Load more">Load more</button>';
    const events: string[] = [];
    const btn = document.querySelector('button')!;
    for (const t of ['pointerdown', 'mousedown', 'mouseup', 'click']) btn.addEventListener(t, () => events.push(t));
    const res = run('click_element', { element_id: idOf('button') });
    expect(res).toMatchObject({ success: true, result: { clicked: 'button "Load more"' } });
    expect(events.filter((e) => e !== 'pointerdown')).toEqual(['mousedown', 'mouseup', 'click']);
  });

  it('hands new-tab links to the executor to open', () => {
    document.body.innerHTML = '<a href="https://example.com/doc" target="_blank" data-name="Docs">Docs</a>';
    const res = run('click_element', { element_id: idOf('a') });
    expect(res.result).toEqual({ clicked: 'link "Docs"', open_in_new_tab: 'https://example.com/doc' });
  });

  it.each([
    ['Place your order', 'make a purchase or payment'],
    ['Buy now', 'make a purchase or payment'],
    ['Delete account', 'delete data'],
    ['Send', 'send or publish something'],
    ['Subscribe', 'make a financial commitment'],
  ])('refuses high-impact click "%s"', (label, reason) => {
    document.body.innerHTML = `<button data-name="${label}">${label}</button>`;
    const onClick = vi.fn();
    document.querySelector('button')!.addEventListener('click', onClick);
    const res = run('click_element', { element_id: idOf('button') });
    expect(res.success).toBe(false);
    expect(res.error?.code).toBe('CONFIRMATION_REQUIRED');
    expect(res.error?.message).toContain(reason);
    expect(res.error?.details).toEqual({ action: `Click the “${label}” button`, reason });
    expect(onClick).not.toHaveBeenCalled();
  });

  it('runs a high-impact click once the user has approved it', () => {
    document.body.innerHTML = '<button data-name="Place your order">Place your order</button>';
    const onClick = vi.fn();
    document.querySelector('button')!.addEventListener('click', onClick);
    const res = runTool('click_element', { element_id: idOf('button') }, true);
    expect(res).toMatchObject({ success: true, result: { clicked: 'button "Place your order"' } });
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('refuses submitting a form that holds a password', () => {
    document.body.innerHTML = `<form><input type="password"><button type="submit" data-name="Continue">Continue</button></form>`;
    expect(run('click_element', { element_id: idOf('button') }).error?.code).toBe('CONFIRMATION_REQUIRED');
  });

  it('reports disabled, hidden and unknown elements', () => {
    document.body.innerHTML = '<button disabled data-name="Next">Next</button>';
    expect(run('click_element', { element_id: idOf('button') }).error?.code).toBe('ELEMENT_NOT_INTERACTABLE');
    expect(run('click_element', { element_id: 'el_zzz_1' }).error).toMatchObject({
      code: 'ELEMENT_NOT_FOUND',
      message: expect.stringContaining('earlier page'),
    });
  });

  it('reports elements that were removed from the page', () => {
    document.body.innerHTML = '<button data-name="Temp">Temp</button>';
    const id = idOf('button');
    document.body.innerHTML = '';
    expect(run('click_element', { element_id: id }).error?.code).toBe('ELEMENT_NOT_FOUND');
  });
});

describe('type_text', () => {
  it('replaces the value and fires input events (React-compatible setter)', () => {
    document.body.innerHTML = '<input value="old" data-name="City" aria-label="City">';
    const input = document.querySelector('input')!;
    const seen: string[] = [];
    input.addEventListener('input', () => seen.push(input.value));
    const res = run('type_text', { element_id: idOf('input'), text: 'Chennai' });
    expect(res).toMatchObject({ success: true, result: { value: 'Chennai', submitted: false } });
    expect(input.value).toBe('Chennai');
    expect(seen.at(-1)).toBe('Chennai');
  });

  it('appends when asked', () => {
    document.body.innerHTML = '<textarea aria-label="Notes" data-name="Notes">Hello</textarea>';
    run('type_text', { element_id: idOf('textarea'), text: ' world', append: true });
    expect(document.querySelector('textarea')!.value).toBe('Hello world');
  });

  it('submits the form on Enter', () => {
    document.body.innerHTML = '<form><input name="q" aria-label="Search" data-name="Search"></form>';
    const onSubmit = vi.fn((e: Event) => e.preventDefault());
    document.querySelector('form')!.addEventListener('submit', onSubmit);
    const res = run('type_text', { element_id: idOf('input'), text: 'AI news', press_enter: true });
    expect(res.result).toMatchObject({ value: 'AI news', submitted: true });
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("lets the page's own Enter handler take over", () => {
    document.body.innerHTML = '<form><input aria-label="Search" data-name="Search"></form>';
    const input = document.querySelector('input')!;
    input.addEventListener('keydown', (e) => e.key === 'Enter' && e.preventDefault());
    const onSubmit = vi.fn((e: Event) => e.preventDefault());
    document.querySelector('form')!.addEventListener('submit', onSubmit);
    expect(run('type_text', { element_id: idOf('input'), text: 'x', press_enter: true }).result).toMatchObject({
      submitted: true,
    });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('types into the editable element inside a wrapper', () => {
    document.body.innerHTML = '<div role="combobox" aria-label="Destination" data-name="Destination"><input></div>';
    run('type_text', { element_id: idOf('[role=combobox]'), text: 'Bangalore' });
    expect(document.querySelector('input')!.value).toBe('Bangalore');
  });

  it('refuses password and card fields', () => {
    document.body.innerHTML = `<input type="password" aria-label="Password" data-name="Password">`;
    expect(run('type_text', { element_id: idOf('input'), text: 'x' }).error?.code).toBe('SENSITIVE_FIELD');
    document.body.innerHTML = `<input autocomplete="cc-number" aria-label="Card" data-name="Card">`;
    expect(run('type_text', { element_id: idOf('input'), text: '4111' }).error?.code).toBe('SENSITIVE_FIELD');
    // Approval never unlocks typing secrets.
    expect(runTool('type_text', { element_id: idOf('input'), text: '4111' }, true).error?.code).toBe('SENSITIVE_FIELD');
  });

  it('refuses non-editable elements', () => {
    document.body.innerHTML = '<button data-name="Go">Go</button>';
    expect(run('type_text', { element_id: idOf('button'), text: 'x' }).error?.code).toBe('ELEMENT_NOT_INTERACTABLE');
  });
});

describe('clear_input and select_option', () => {
  it('clears inputs', () => {
    document.body.innerHTML = '<input aria-label="Name" value="abc" data-name="Name">';
    run('clear_input', { element_id: idOf('input') });
    expect(document.querySelector('input')!.value).toBe('');
  });

  it('selects by text, value or partial text', () => {
    document.body.innerHTML = `<select aria-label="Sort" data-name="Relevance">
      <option value="rel">Relevance</option><option value="price_asc">Price: Low to High</option></select>`;
    const select = document.querySelector('select')!;
    const onChange = vi.fn();
    select.addEventListener('change', onChange);
    const id = idOf('select');
    expect(run('select_option', { element_id: id, option: 'price: low to high' }).result).toEqual({
      selected: 'Price: Low to High',
      value: 'price_asc',
    });
    expect(run('select_option', { element_id: id, option: 'rel' }).result).toMatchObject({ value: 'rel' });
    expect(run('select_option', { element_id: id, option: 'Low to' }).result).toMatchObject({ value: 'price_asc' });
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(run('select_option', { element_id: id, option: 'Newest' }).error).toMatchObject({
      code: 'OPTION_NOT_FOUND',
      message: expect.stringContaining('Relevance | Price: Low to High'),
    });
  });
});

describe('page_has_text', () => {
  it('checks visible text case-insensitively', () => {
    document.body.innerHTML = '<p>Results for AI News</p><p hidden>secret</p>';
    expect(run('page_has_text', { text: 'ai news' }).result).toEqual({ found: true });
    expect(run('page_has_text', { text: 'secret' }).result).toEqual({ found: false });
  });
});
