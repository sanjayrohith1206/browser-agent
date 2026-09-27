// Page actions performed by the content script, plus the safety guards:
// high-impact actions (buying, sending, deleting...) only run once the user
// has approved them, which the backend signals with `confirmed`; typing into
// password and payment-card fields is always refused.

import type { ToolErrorCode } from '@shared/protocol';
import {
  accessibleName,
  type ElementRegistry,
  isContentEditable,
  isDisabled,
  isInViewport,
  isVisible,
  roleOf,
  scrollState,
} from './dom';
import { visibleText } from './extract';

export class ContentError extends Error {
  constructor(
    readonly code: ToolErrorCode,
    message: string,
    readonly details?: { action: string; reason: string },
  ) {
    super(message);
  }
}

// ---------------------------------------------------------------- guards

const HIGH_IMPACT: [RegExp, string][] = [
  [
    /\b(buy( now)?|purchase|place (your |my )?order|complete (your |my )?(order|purchase)|check ?out|proceed to (checkout|payment|pay)|pay( now)?|make (a )?payment|confirm (and pay|order|purchase|payment|booking)|book now|add payment)\b/i,
    'make a purchase or payment',
  ],
  [/\b(delete|remove account|close (my |your )?account|deactivate|erase|empty trash|permanently)\b/i, 'delete data'],
  [/\b(send|post|publish|tweet|submit (review|comment|post)|share now)\b/i, 'send or publish something'],
  [/\b(transfer|withdraw|donate|subscribe|upgrade( plan)?|start (free )?trial)\b/i, 'make a financial commitment'],
  [/\b(change password|update password|save changes|update account|change email)\b/i, 'change account settings'],
];

const CARD_FIELD = /cc-(number|csc|exp)|card.?(number|num|no)|\bcvv\b|\bcvc\b|security.?code|expir/i;

/** Why typing into this field is refused, or null if allowed. */
export function sensitiveFieldReason(el: Element): string | null {
  if (el instanceof HTMLInputElement && el.type === 'password') return 'password';
  const hints = [
    el.getAttribute('autocomplete'),
    el.getAttribute('name'),
    el.id,
    el.getAttribute('placeholder'),
    accessibleName(el),
  ].join(' ');
  return CARD_FIELD.test(hints) ? 'payment card' : null;
}

function formHasSensitiveFields(form: HTMLFormElement | null): boolean {
  if (!form) return false;
  return Array.from(form.elements).some((f) => f instanceof HTMLInputElement && sensitiveFieldReason(f) !== null);
}

/** Why clicking this element is refused, or null if allowed. */
export function highImpactReason(el: Element): string | null {
  const label = [
    accessibleName(el),
    el.getAttribute('title'),
    el instanceof HTMLInputElement ? el.value : '',
  ].join(' ');
  for (const [pattern, reason] of HIGH_IMPACT) {
    if (pattern.test(label)) return reason;
  }
  const isSubmit =
    (el instanceof HTMLButtonElement && el.type === 'submit') ||
    (el instanceof HTMLInputElement && (el.type === 'submit' || el.type === 'image'));
  if (isSubmit && formHasSensitiveFields((el as HTMLButtonElement | HTMLInputElement).form)) {
    return 'submit a form containing passwords or payment details';
  }
  return null;
}

/** Stops a high-impact action until the user approves it. The backend
 * shows `action` and `reason` to the user and re-runs the call with
 * `confirmed` if they agree. */
function needsApproval(action: string, reason: string): never {
  throw new ContentError(
    'CONFIRMATION_REQUIRED',
    `This would ${reason}, so it needs the user's approval before it runs.`,
    { action, reason },
  );
}

const NOUNS: Record<string, string> = { textbox: 'field', searchbox: 'search box', combobox: 'field' };

/** 'the “Place order” button', for approval dialogs. */
function actionTarget(el: Element): string {
  const name = accessibleName(el);
  const role = roleOf(el);
  const noun = NOUNS[role] ?? role;
  return name ? `the “${name.length > 60 ? `${name.slice(0, 59)}…` : name}” ${noun}` : `the ${noun}`;
}

// -------------------------------------------------------------- helpers

export function resolveElement(registry: ElementRegistry, id: string): Element {
  const el = registry.resolve(id);
  if (el) return el;
  const why = registry.isFromOtherPage(id)
    ? 'it came from an earlier page'
    : 'it is no longer on the page';
  throw new ContentError('ELEMENT_NOT_FOUND', `Element ${id} can't be used because ${why}. Call get_elements again.`);
}

function prepare(el: Element): void {
  if (!isInViewport(el)) el.scrollIntoView({ block: 'center', inline: 'nearest' });
  if (!isVisible(el)) {
    throw new ContentError('ELEMENT_NOT_INTERACTABLE', 'The element is hidden, so it cannot be used.');
  }
  if (isDisabled(el)) {
    throw new ContentError('ELEMENT_NOT_INTERACTABLE', 'The element is disabled.');
  }
  highlight(el);
}

function describe(el: Element): string {
  const name = accessibleName(el);
  return name ? `${roleOf(el)} "${name}"` : roleOf(el);
}

/** Briefly outlines the element so the user can see what the agent does. */
export function highlight(el: Element): void {
  const r = el.getBoundingClientRect();
  const box = document.createElement('div');
  box.setAttribute('aria-hidden', 'true');
  Object.assign(box.style, {
    position: 'fixed',
    left: `${r.left - 3}px`,
    top: `${r.top - 3}px`,
    width: `${r.width + 6}px`,
    height: `${r.height + 6}px`,
    border: '2px solid #3b5bdb',
    borderRadius: '6px',
    boxShadow: '0 0 0 4px rgba(59, 91, 219, 0.25)',
    pointerEvents: 'none',
    zIndex: '2147483647',
    transition: 'opacity 0.4s ease',
  } satisfies Partial<CSSStyleDeclaration>);
  document.documentElement.appendChild(box);
  setTimeout(() => {
    box.style.opacity = '0';
    setTimeout(() => box.remove(), 450);
  }, 900);
}

function pointerSequence(el: Element): void {
  const r = el.getBoundingClientRect();
  const init = {
    bubbles: true,
    cancelable: true,
    composed: true,
    clientX: r.left + r.width / 2,
    clientY: r.top + r.height / 2,
    button: 0,
  };
  const Pointer = typeof PointerEvent === 'function' ? PointerEvent : MouseEvent;
  el.dispatchEvent(new Pointer('pointerover', init));
  el.dispatchEvent(new MouseEvent('mouseover', init));
  el.dispatchEvent(new Pointer('pointerdown', init));
  el.dispatchEvent(new MouseEvent('mousedown', init));
  if (el instanceof HTMLElement) el.focus({ preventScroll: true });
  el.dispatchEvent(new Pointer('pointerup', init));
  el.dispatchEvent(new MouseEvent('mouseup', init));
}

// ------------------------------------------------------------- actions

export interface ClickResult {
  clicked: string;
  /** Set for links that would open a new tab: the caller opens the URL in a
   * new tab it controls and continues there. */
  open_in_new_tab?: string;
}

export function click(registry: ElementRegistry, id: string, confirmed = false): ClickResult {
  const el = resolveElement(registry, id);
  const reason = highImpactReason(el);
  if (reason && !confirmed) needsApproval(`Click ${actionTarget(el)}`, reason);
  prepare(el);

  const anchor = el.closest('a[href]');
  if (anchor instanceof HTMLAnchorElement && anchor.target === '_blank' && /^https?:/i.test(anchor.href)) {
    return { clicked: describe(el), open_in_new_tab: anchor.href };
  }
  pointerSequence(el);
  if (el instanceof HTMLElement) el.click();
  else el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true }));
  return { clicked: describe(el) };
}

type TextField = HTMLInputElement | HTMLTextAreaElement;

const NON_TEXT_INPUTS = new Set([
  'checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file', 'color', 'range', 'hidden',
]);

/** The element text can be typed into: the target itself or, for wrappers
 * such as custom comboboxes, the single editable element inside it. */
function editableTarget(el: Element): TextField | HTMLElement {
  if (el instanceof HTMLTextAreaElement) return el;
  if (el instanceof HTMLInputElement && !NON_TEXT_INPUTS.has(el.type)) return el;
  if (isContentEditable(el)) return el as HTMLElement;
  const inner = el.querySelectorAll('input, textarea, [contenteditable="true"], [contenteditable=""]');
  const editable = Array.from(inner).filter(
    (c) => !(c instanceof HTMLInputElement && NON_TEXT_INPUTS.has(c.type)) && isVisible(c),
  );
  if (editable.length === 1) return editableTarget(editable[0]!);
  throw new ContentError('ELEMENT_NOT_INTERACTABLE', `You can't type into ${describe(el)}.`);
}

function setNativeValue(el: TextField, value: string): void {
  // Use the prototype setter so frameworks that track the value (React) see
  // the change.
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, value);
  el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: value }));
}

function currentText(el: TextField | HTMLElement): string {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement ? el.value : (el.textContent ?? '');
}

function replaceContent(el: TextField | HTMLElement, text: string, append: boolean): void {
  el.focus({ preventScroll: true });
  const before = currentText(el);
  const expected = append ? before + text : text;

  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    if (append) el.setSelectionRange?.(el.value.length, el.value.length);
    else el.select();
  } else {
    const sel = el.ownerDocument.getSelection();
    const range = el.ownerDocument.createRange();
    range.selectNodeContents(el);
    if (append) range.collapse(false);
    sel?.removeAllRanges();
    sel?.addRange(range);
  }

  // execCommand produces the same beforeinput/input events as real typing;
  // fall back to setting the value directly when it is unavailable.
  let ok = false;
  try {
    ok = text === '' ? el.ownerDocument.execCommand('delete') : el.ownerDocument.execCommand('insertText', false, text);
  } catch {
    ok = false;
  }
  if (!ok || currentText(el) !== expected) {
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) setNativeValue(el, expected);
    else {
      el.textContent = expected;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true }));
    }
  }
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function pressEnter(el: TextField | HTMLElement, confirmed: boolean): boolean {
  const init = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
  const notHandled = el.dispatchEvent(new KeyboardEvent('keydown', init));
  el.dispatchEvent(new KeyboardEvent('keypress', init));
  el.dispatchEvent(new KeyboardEvent('keyup', init));
  if (!notHandled) return true; // the page's own key handler took it

  // Synthetic key events don't trigger implicit form submission, so do it
  // explicitly for single-line fields (and search-style text areas).
  const form = (el as TextField).form ?? el.closest('form');
  const singleLine =
    el instanceof HTMLInputElement ||
    (el instanceof HTMLTextAreaElement && ['combobox', 'searchbox'].includes(roleOf(el)));
  if (form && singleLine) {
    if (formHasSensitiveFields(form) && !confirmed) {
      needsApproval('Submit the form', 'submit a form containing passwords or payment details');
    }
    form.requestSubmit();
    return true;
  }
  return false;
}

export interface TypeResult {
  typed_into: string;
  value: string;
  submitted: boolean;
}

export function typeText(
  registry: ElementRegistry,
  id: string,
  text: string,
  options: { append?: boolean; pressEnter?: boolean; confirmed?: boolean } = {},
): TypeResult {
  const target = editableTarget(resolveElement(registry, id));
  const sensitive = sensitiveFieldReason(target);
  if (sensitive) {
    throw new ContentError(
      'SENSITIVE_FIELD',
      `This is a ${sensitive} field. The assistant never types ${sensitive} details; ask the user to fill it in themselves.`,
    );
  }
  prepare(target);
  replaceContent(target, text, options.append ?? false);
  const submitted = options.pressEnter ? pressEnter(target, options.confirmed ?? false) : false;
  return { typed_into: describe(target), value: currentText(target).slice(0, 200), submitted };
}

export function clearInput(registry: ElementRegistry, id: string): TypeResult {
  const target = editableTarget(resolveElement(registry, id));
  prepare(target);
  replaceContent(target, '', false);
  return { typed_into: describe(target), value: currentText(target), submitted: false };
}

export function selectOption(
  registry: ElementRegistry,
  id: string,
  option: string,
): { selected: string; value: string } {
  const el = resolveElement(registry, id);
  if (!(el instanceof HTMLSelectElement)) {
    throw new ContentError(
      'ELEMENT_NOT_INTERACTABLE',
      `${describe(el)} is not a dropdown list. If it is a custom dropdown, click it to open it, then click the option.`,
    );
  }
  prepare(el);
  const wanted = option.trim().toLowerCase();
  const options = Array.from(el.options);
  const match =
    options.find((o) => o.text.trim().toLowerCase() === wanted) ??
    options.find((o) => o.value.toLowerCase() === wanted) ??
    options.find((o) => o.text.trim().toLowerCase().includes(wanted));
  if (!match) {
    const available = options.map((o) => o.text.trim()).slice(0, 30).join(' | ');
    throw new ContentError('OPTION_NOT_FOUND', `No option matches "${option}". Available: ${available}`);
  }
  el.value = match.value;
  match.selected = true;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { selected: match.text.trim(), value: match.value };
}

/** The element that actually scrolls: the document, or — for app-style
 * layouts — the largest scrollable container. */
function scrollContainer(doc: Document): Element {
  const root = doc.scrollingElement ?? doc.documentElement;
  if (root.scrollHeight > root.clientHeight + 10) return root;
  let best: Element = root;
  let bestArea = 0;
  for (const el of Array.from(doc.querySelectorAll('*'))) {
    if (el.scrollHeight <= el.clientHeight + 10) continue;
    const overflow = getComputedStyle(el).overflowY;
    if (overflow !== 'auto' && overflow !== 'scroll') continue;
    const area = el.clientWidth * el.clientHeight;
    if (area > bestArea) {
      best = el;
      bestArea = area;
    }
  }
  return best;
}

export function scrollPage(
  registry: ElementRegistry,
  options: { direction?: 'down' | 'up' | 'top' | 'bottom'; amount?: number; elementId?: string },
): { y: number; max_y: number; at_top: boolean; at_bottom: boolean } {
  if (options.elementId) {
    resolveElement(registry, options.elementId).scrollIntoView({ block: 'center', inline: 'nearest' });
  } else {
    const container = scrollContainer(document);
    const step = container.clientHeight * (options.amount ?? 0.8);
    switch (options.direction ?? 'down') {
      case 'down':
        container.scrollTop += step;
        break;
      case 'up':
        container.scrollTop -= step;
        break;
      case 'top':
        container.scrollTop = 0;
        break;
      case 'bottom':
        container.scrollTop = container.scrollHeight;
        break;
    }
  }
  const container = scrollContainer(document);
  const y = Math.round(container.scrollTop);
  const maxY = Math.max(0, Math.round(container.scrollHeight - container.clientHeight));
  const state = container === (document.scrollingElement ?? document.documentElement) ? scrollState(document) : null;
  return {
    y: state?.y ?? y,
    max_y: state?.max_y ?? maxY,
    at_top: y <= 1,
    at_bottom: y >= maxY - 1,
  };
}

export function pageHasText(text: string): boolean {
  return visibleText(document.body).toLowerCase().includes(text.trim().toLowerCase());
}
