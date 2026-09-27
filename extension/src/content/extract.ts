// Readable-content extraction. Pure DOM code so it runs in the content
// script and in tests (jsdom) alike.

import type { PageContent } from '@shared/protocol';

export const DEFAULT_MAX_CHARS = 40_000;

// Never content, wherever they appear.
const SKIP_TAGS = new Set([
  'SCRIPT',
  'STYLE',
  'NOSCRIPT',
  'TEMPLATE',
  'SVG',
  'CANVAS',
  'IFRAME',
  'OBJECT',
  'EMBED',
  'HEAD',
]);

// Page chrome rather than content.
const BOILERPLATE_SELECTOR = [
  'nav',
  'aside',
  'footer',
  '[role="navigation"]',
  '[role="complementary"]',
  '[role="contentinfo"]',
  '[role="banner"]',
  '[aria-modal="true"]',
].join(',');

const BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'BLOCKQUOTE', 'BR', 'DD', 'DETAILS', 'DIV', 'DL', 'DT',
  'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
  'HEADER', 'HR', 'LI', 'MAIN', 'OL', 'P', 'PRE', 'SECTION', 'SUMMARY', 'TABLE',
  'TR', 'UL',
]);

const MAIN_SELECTORS = ['main', '[role="main"]', 'article'];

function isHidden(el: Element): boolean {
  if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true') return true;
  const view = el.ownerDocument.defaultView;
  if (!view) return false;
  const style = view.getComputedStyle(el);
  return style.display === 'none' || style.visibility === 'hidden';
}

function isBoilerplate(el: Element, root: Element): boolean {
  if (el === root) return false;
  if (el.matches(BOILERPLATE_SELECTOR)) return true;
  // A site-wide <header> is chrome; an article's own header holds its title.
  return el.tagName === 'HEADER' && !el.closest('main, article, [role="main"]');
}

/** Visible text under root, with block elements separated by newlines. */
export function visibleText(root: Element): string {
  const out: string[] = [];

  const walk = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      out.push(node.textContent ?? '');
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as Element;
    if (SKIP_TAGS.has(el.tagName.toUpperCase()) || isHidden(el) || isBoilerplate(el, root)) return;

    const block = BLOCK_TAGS.has(el.tagName.toUpperCase());
    if (block) out.push('\n');
    if (el.tagName === 'IMG') {
      const alt = el.getAttribute('alt')?.trim();
      if (alt) out.push(` [image: ${alt}] `);
    }
    for (const child of Array.from(el.childNodes)) walk(child);
    if (el.tagName === 'TD' || el.tagName === 'TH') out.push(' | ');
    if (block) out.push('\n');
  };

  walk(root);
  return normalizeWhitespace(out.join(''));
}

export function normalizeWhitespace(text: string): string {
  return text
    .split('\n')
    .map((line) => line.replace(/[ \t\f\v ]+/g, ' ').trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** The element holding the page's main content: a landmark when it carries
 * most of the text, otherwise the body. */
export function mainContentRoot(doc: Document): Element {
  const body = doc.body ?? doc.documentElement;
  const bodyLength = visibleText(body).length;
  for (const selector of MAIN_SELECTORS) {
    const candidates = Array.from(doc.querySelectorAll(selector)).filter((el) => !isHidden(el));
    if (candidates.length !== 1) continue;
    const candidate = candidates[0]!;
    const length = visibleText(candidate).length;
    if (length >= 500 || (bodyLength > 0 && length / bodyLength >= 0.4)) return candidate;
  }
  return body;
}

export function extractHeadings(root: Element, limit = 50): PageContent['headings'] {
  const headings: PageContent['headings'] = [];
  for (const el of Array.from(root.querySelectorAll('h1, h2, h3'))) {
    if (headings.length >= limit) break;
    if (isHidden(el)) continue;
    const text = normalizeWhitespace(el.textContent ?? '');
    if (text) headings.push({ level: Number(el.tagName[1]), text: text.slice(0, 300) });
  }
  return headings;
}

export function extractPageContent(doc: Document, maxChars = DEFAULT_MAX_CHARS): PageContent {
  const root = mainContentRoot(doc);
  const text = visibleText(root);
  const description =
    doc.querySelector('meta[name="description"]')?.getAttribute('content') ??
    doc.querySelector('meta[property="og:description"]')?.getAttribute('content') ??
    '';
  return {
    url: doc.location?.href ?? '',
    title: normalizeWhitespace(doc.title),
    description: normalizeWhitespace(description).slice(0, 1000),
    lang: doc.documentElement.lang || '',
    headings: extractHeadings(root),
    text: text.slice(0, maxChars),
    total_chars: text.length,
    truncated: text.length > maxChars,
  };
}
