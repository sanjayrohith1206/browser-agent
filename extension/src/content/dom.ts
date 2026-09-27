// A normalized, LLM-friendly model of the page's interactive elements.
//
// Elements get IDs like "el_k3f_12" that stay stable for the life of the
// document (the same element keeps its ID across snapshots). The random
// middle token changes on every page load, so an ID from a previous page can
// never silently hit a different element on the next one.

import type { ElementInfo, PageElements } from '@shared/protocol';
import { mainContentRoot, normalizeWhitespace } from './extract';

const INTERACTIVE_SELECTOR = [
  'a[href]',
  'button',
  'input:not([type="hidden"])',
  'textarea',
  'select',
  'summary',
  '[contenteditable=""]',
  '[contenteditable="true"]',
  '[onclick]',
  '[tabindex]:not([tabindex="-1"])',
  ...[
    'button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'switch',
    'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'slider',
    'spinbutton', 'treeitem', 'listbox',
  ].map((r) => `[role="${r}"]`),
].join(',');

const MAX_TEXT = 100;

// ---------------------------------------------------------------- registry

export class ElementRegistry {
  readonly token = Math.random().toString(36).slice(2, 5);
  private next = 1;
  private readonly ids = new WeakMap<Element, string>();
  private readonly elements = new Map<string, WeakRef<Element>>();

  idFor(el: Element): string {
    let id = this.ids.get(el);
    if (!id) {
      id = `el_${this.token}_${this.next++}`;
      this.ids.set(el, id);
      this.elements.set(id, new WeakRef(el));
    }
    return id;
  }

  /** The live element for an ID, or null if it is unknown or gone. */
  resolve(id: string): Element | null {
    const el = this.elements.get(id)?.deref();
    if (!el || !el.isConnected) return null;
    return el;
  }

  isFromOtherPage(id: string): boolean {
    const m = /^el_([a-z0-9]+)_\d+$/.exec(id);
    return m !== null && m[1] !== this.token;
  }
}

// --------------------------------------------------------------- traversal

/** querySelectorAll that also searches open shadow roots. */
export function deepQueryAll(root: Document | Element | ShadowRoot, selector: string): Element[] {
  const out: Element[] = [];
  const visit = (scope: Document | Element | ShadowRoot): void => {
    out.push(...Array.from(scope.querySelectorAll(selector)));
    for (const el of Array.from(scope.querySelectorAll('*'))) {
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(root);
  return out;
}

export function isVisible(el: Element): boolean {
  if (!el.isConnected) return false;
  const check = (el as Element & { checkVisibility?: (o: object) => boolean }).checkVisibility;
  if (typeof check === 'function') {
    if (!check.call(el, { visibilityProperty: true, contentVisibilityAuto: true })) return false;
  } else {
    for (let n: Element | null = el; n; n = n.parentElement) {
      if (n.hasAttribute('hidden')) return false;
      const style = getComputedStyle(n);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
  }
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

export function isInViewport(el: Element): boolean {
  const r = el.getBoundingClientRect();
  const view = el.ownerDocument.defaultView;
  const h = view?.innerHeight ?? 0;
  const w = view?.innerWidth ?? 0;
  return r.bottom > 0 && r.right > 0 && r.top < h && r.left < w;
}

export function isDisabled(el: Element): boolean {
  return el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true';
}

// ----------------------------------------------------------- semantics

export function roleOf(el: Element): string {
  const explicit = el.getAttribute('role')?.split(/\s+/)[0];
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  if (el instanceof HTMLInputElement) {
    switch (el.type) {
      case 'button':
      case 'submit':
      case 'reset':
      case 'image':
        return 'button';
      case 'checkbox':
        return 'checkbox';
      case 'radio':
        return 'radio';
      case 'range':
        return 'slider';
      case 'number':
        return 'spinbutton';
      case 'search':
        return el.hasAttribute('list') ? 'combobox' : 'searchbox';
      default:
        return el.hasAttribute('list') ? 'combobox' : 'textbox';
    }
  }
  switch (tag) {
    case 'a':
      return el.hasAttribute('href') ? 'link' : 'generic';
    case 'button':
    case 'summary':
      return 'button';
    case 'textarea':
      return 'textbox';
    case 'select':
      return (el as HTMLSelectElement).multiple ? 'listbox' : 'combobox';
  }
  if (isContentEditable(el)) return 'textbox';
  return tag;
}

export function isContentEditable(el: Element): boolean {
  return el instanceof HTMLElement && el.isContentEditable;
}

function textOf(el: Element | null | undefined): string {
  if (!el) return '';
  const parts: string[] = [];
  const walk = (n: Node): void => {
    if (n.nodeType === Node.TEXT_NODE) parts.push(n.textContent ?? '');
    else if (n instanceof Element) {
      if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(n.tagName)) return;
      if (n.tagName === 'IMG') parts.push(` ${n.getAttribute('alt') ?? ''} `);
      if (n.tagName === 'svg' || n.tagName === 'SVG') {
        parts.push(` ${n.getAttribute('aria-label') ?? n.querySelector('title')?.textContent ?? ''} `);
        return;
      }
      n.childNodes.forEach(walk);
    }
  };
  walk(el);
  return normalizeWhitespace(parts.join(' ')).replace(/\s+/g, ' ');
}

function clip(s: string | null | undefined, max = MAX_TEXT): string {
  const t = (s ?? '').replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

/** A simplified accessible-name computation. */
export function accessibleName(el: Element): string {
  const doc = el.ownerDocument;
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((id) => textOf(doc.getElementById(id)))
      .join(' ')
      .trim();
    if (text) return clip(text);
  }
  const aria = el.getAttribute('aria-label');
  if (aria?.trim()) return clip(aria);

  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) {
    const labels = Array.from(el.labels ?? [])
      .map((l) => textOf(l))
      .join(' ')
      .trim();
    if (labels) return clip(labels);
    if (el instanceof HTMLInputElement && ['button', 'submit', 'reset'].includes(el.type)) {
      return clip(el.value || (el.type === 'submit' ? 'Submit' : el.type === 'reset' ? 'Reset' : ''));
    }
    if (el instanceof HTMLInputElement && el.type === 'image') return clip(el.alt);
  }
  const text = textOf(el);
  if (text && !(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
    return clip(text);
  }
  const fallback = el.getAttribute('title') ?? el.getAttribute('placeholder') ?? el.getAttribute('alt');
  return clip(fallback);
}

const CONTEXT_CONTAINERS =
  'li, tr, [role="row"], [role="listitem"], fieldset, [role="dialog"], [role="group"], article, section, form';

/** Nearby text that tells similar elements apart (e.g. which product an
 * "Add to cart" button belongs to). */
export function nearbyContext(el: Element, ownName: string): string {
  const container = el.parentElement?.closest(CONTEXT_CONTAINERS);
  if (!container) return '';
  let text = '';
  if (container.tagName === 'FIELDSET') {
    text = textOf(container.querySelector('legend'));
  } else if (container.getAttribute('aria-label')) {
    text = container.getAttribute('aria-label') ?? '';
  } else {
    const heading = container.querySelector('h1, h2, h3, h4, h5, h6, [role="heading"], legend, caption');
    text = heading ? textOf(heading) : textOf(container);
  }
  if (ownName) text = text.replace(ownName, ' ');
  return clip(text, 80);
}

function isSensitiveValue(el: Element): boolean {
  return (
    el instanceof HTMLInputElement &&
    (el.type === 'password' || /cc-|card|csc|cvv|cvc/i.test(`${el.autocomplete} ${el.name} ${el.id}`))
  );
}

export function describeElement(el: Element, registry: ElementRegistry): ElementInfo {
  const role = roleOf(el);
  const name = accessibleName(el);
  const info: ElementInfo = {
    id: registry.idFor(el),
    tag: el.tagName.toLowerCase(),
    role,
    in_viewport: isInViewport(el),
  };
  if (name) info.name = name;

  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
    const text = clip(textOf(el));
    if (text && text !== name) info.text = text;
  }
  if (el instanceof HTMLInputElement) {
    if (el.type !== 'text') info.type = el.type;
    if (el.type === 'checkbox' || el.type === 'radio') info.checked = el.checked;
    else if (el.value && !['button', 'submit', 'reset', 'image'].includes(el.type)) {
      info.value = isSensitiveValue(el) ? '••••' : clip(el.value);
    }
  } else if (el instanceof HTMLTextAreaElement && el.value) {
    info.value = clip(el.value);
  } else if (el instanceof HTMLSelectElement) {
    const selected = el.selectedOptions[0];
    if (selected) info.value = clip(selected.text);
    info.options = Array.from(el.options)
      .slice(0, 25)
      .map((o) => clip(o.text, 60));
  } else if (isContentEditable(el)) {
    const v = clip(el.textContent);
    if (v) info.value = v;
  }

  const placeholder = el.getAttribute('placeholder');
  if (placeholder && placeholder !== name) info.placeholder = clip(placeholder);
  if (el instanceof HTMLAnchorElement && /^https?:/i.test(el.href)) info.href = clip(el.href, 150);

  const ariaChecked = el.getAttribute('aria-checked');
  if (ariaChecked && info.checked === undefined) info.checked = ariaChecked === 'true';
  const ariaSelected = el.getAttribute('aria-selected');
  if (ariaSelected) info.selected = ariaSelected === 'true';
  const ariaExpanded = el.getAttribute('aria-expanded');
  if (ariaExpanded) info.expanded = ariaExpanded === 'true';
  if (isDisabled(el)) info.disabled = true;

  const context = nearbyContext(el, name);
  if (context && context !== name) info.context = context;
  return info;
}

// ------------------------------------------------------------ snapshots

export function interactiveElements(doc: Document): Element[] {
  const seen = new Set<Element>();
  const out: Element[] = [];
  for (const el of deepQueryAll(doc, INTERACTIVE_SELECTOR)) {
    if (seen.has(el)) continue;
    seen.add(el);
    if (el instanceof HTMLAnchorElement && /^javascript:/i.test(el.getAttribute('href') ?? '') && !accessibleName(el)) {
      continue;
    }
    if (isVisible(el)) out.push(el);
  }
  return out;
}

export function scrollState(doc: Document): PageElements['scroll'] {
  const view = doc.defaultView;
  const scroller = doc.scrollingElement ?? doc.documentElement;
  const viewportHeight = view?.innerHeight ?? 0;
  return {
    y: Math.round(scroller.scrollTop),
    max_y: Math.max(0, Math.round(scroller.scrollHeight - viewportHeight)),
    viewport_height: viewportHeight,
  };
}

export function snapshot(
  doc: Document,
  registry: ElementRegistry,
  options: { scope?: 'viewport' | 'page'; max?: number } = {},
): PageElements {
  const max = options.max ?? 150;
  const described = interactiveElements(doc).map((el) => describeElement(el, registry));
  const inView = described.filter((e) => e.in_viewport);
  const rest = options.scope === 'viewport' ? [] : described.filter((e) => !e.in_viewport);
  const ordered = [...inView, ...rest];
  return {
    url: doc.location?.href ?? '',
    title: normalizeWhitespace(doc.title),
    elements: ordered.slice(0, max),
    total: ordered.length,
    truncated: ordered.length > max,
    scroll: scrollState(doc),
  };
}

// --------------------------------------------------------------- search

const STOPWORDS = new Set(['the', 'a', 'an', 'to', 'of', 'on', 'in', 'for', 'with', 'and', 'or', 'this', 'that', 'element', 'page']);

// Words in a description that indicate the kind of element wanted.
const ROLE_HINTS: Record<string, string[]> = {
  button: ['button'],
  btn: ['button'],
  link: ['link'],
  input: ['textbox', 'searchbox', 'combobox', 'spinbutton'],
  field: ['textbox', 'searchbox', 'combobox', 'spinbutton'],
  box: ['textbox', 'searchbox', 'combobox'],
  textbox: ['textbox', 'searchbox'],
  bar: ['textbox', 'searchbox', 'combobox'],
  checkbox: ['checkbox', 'switch'],
  toggle: ['checkbox', 'switch'],
  radio: ['radio'],
  dropdown: ['combobox', 'listbox', 'button'],
  select: ['combobox', 'listbox'],
  menu: ['menuitem', 'button', 'combobox'],
  tab: ['tab'],
  option: ['option'],
};

export interface ElementMatch extends ElementInfo {
  score: number;
}

function words(s: string): string[] {
  return s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

export function findElements(
  doc: Document,
  registry: ElementRegistry,
  description: string,
  roleFilter?: string,
  limit = 5,
): ElementMatch[] {
  const all = words(description);
  const hinted = new Set(all.flatMap((w) => ROLE_HINTS[w] ?? []));
  const terms = all.filter((w) => !STOPWORDS.has(w) && !(w in ROLE_HINTS));
  const phrase = terms.join(' ');
  const textish = hinted.size === 0 || [...hinted].some((r) => r === 'textbox' || r === 'searchbox');

  const matches: ElementMatch[] = [];
  for (const el of interactiveElements(doc)) {
    const info = describeElement(el, registry);
    if (roleFilter && info.role !== roleFilter) continue;

    const name = (info.name ?? '').toLowerCase();
    const primary = words(`${info.name ?? ''} ${info.text ?? ''} ${info.placeholder ?? ''}`);
    const secondary = words(
      `${info.context ?? ''} ${el.getAttribute('name') ?? ''} ${el.id} ${el.getAttribute('title') ?? ''} ${info.value ?? ''}`,
    );

    let score = 0;
    for (const t of terms) {
      if (words(name).includes(t)) score += 3;
      else if (primary.includes(t)) score += 2;
      else if (primary.some((w) => w.startsWith(t) || t.startsWith(w))) score += 1;
      else if (secondary.includes(t)) score += 1;
    }
    if (phrase && name === phrase) score += 6;
    else if (phrase && name.startsWith(phrase)) score += 3;

    if (hinted.size > 0) score += hinted.has(info.role) ? 3 : -4;
    if (textish && terms.includes('search')) {
      const isSearchField =
        info.role === 'searchbox' ||
        el.getAttribute('type') === 'search' ||
        el.getAttribute('name') === 'q' ||
        (el.closest('form')?.getAttribute('role') === 'search' && info.role === 'textbox');
      if (isSearchField) score += 4;
    }
    if (terms.length === 0 && hinted.has(info.role)) score += 1;
    if (info.disabled) score -= 2;
    if (info.in_viewport) score += 0.5;

    if (score > 0.5) matches.push({ ...info, score });
  }
  return matches.sort((a, b) => b.score - a.score).slice(0, limit);
}

// ---------------------------------------------------------------- links

export interface LinkInfo {
  id: string;
  text: string;
  url: string;
  in_main: boolean;
}

export function listLinks(doc: Document, registry: ElementRegistry, max = 100): { links: LinkInfo[]; total: number } {
  const main = mainContentRoot(doc);
  const seen = new Set<string>();
  const links: LinkInfo[] = [];
  for (const a of deepQueryAll(doc, 'a[href]')) {
    if (!(a instanceof HTMLAnchorElement) || !/^https?:/i.test(a.href) || !isVisible(a)) continue;
    const text = accessibleName(a);
    const key = `${a.href}|${text}`;
    if (!text || seen.has(key)) continue;
    seen.add(key);
    links.push({ id: registry.idFor(a), text, url: clip(a.href, 200), in_main: main.contains(a) });
  }
  const ordered = [...links.filter((l) => l.in_main), ...links.filter((l) => !l.in_main)];
  return { links: ordered.slice(0, max), total: ordered.length };
}
