// jsdom has no layout engine. Give elements a box so visibility and viewport
// checks behave as in a browser: every element is 100x20 at data-top (px,
// default 10); elements that are display:none (or inside one) get an empty box.

export function installFakeLayout(): void {
  Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
    for (let n: Element | null = this; n; n = n.parentElement) {
      if (n.hasAttribute('hidden') || getComputedStyle(n).display === 'none') {
        return new DOMRect(0, 0, 0, 0);
      }
    }
    const top = Number(this.closest('[data-top]')?.getAttribute('data-top') ?? 10);
    return new DOMRect(10, top, 100, 20);
  };
  Element.prototype.scrollIntoView = function (): void {};
}
