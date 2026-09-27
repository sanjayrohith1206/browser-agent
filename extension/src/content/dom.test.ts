import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { installFakeLayout } from '../test/layout';
import { accessibleName, ElementRegistry, findElements, listLinks, roleOf, snapshot } from './dom';

beforeAll(installFakeLayout);

let registry: ElementRegistry;
beforeEach(() => {
  registry = new ElementRegistry();
  document.body.innerHTML = '';
});

const $ = (sel: string): Element => document.querySelector(sel)!;

describe('roles and names', () => {
  it('computes implicit roles', () => {
    document.body.innerHTML = `
      <a id="a" href="/x">x</a><button id="b">b</button><input id="c" type="checkbox">
      <input id="d" type="search"><input id="e"><textarea id="f"></textarea>
      <select id="g"><option>1</option></select><div id="h" role="tab">t</div>
      <div id="i" contenteditable="true"></div>`;
    expect(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((id) => roleOf($(`#${id}`)))).toEqual([
      'link', 'button', 'checkbox', 'searchbox', 'textbox', 'textbox', 'combobox', 'tab',
    ]);
  });

  it('follows aria-labelledby, aria-label, labels, then text and placeholder', () => {
    document.body.innerHTML = `
      <span id="lbl">Departure city</span><input id="a" aria-labelledby="lbl">
      <button id="b" aria-label="Search">🔍</button>
      <label for="c">Email address</label><input id="c">
      <label>Remember me <input id="d" type="checkbox"></label>
      <input id="e" placeholder="Search products">
      <a id="f" href="/"><img alt="Home"></a>
      <input id="g" type="submit" value="Go">`;
    expect(['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => accessibleName($(`#${id}`)))).toEqual([
      'Departure city', 'Search', 'Email address', 'Remember me', 'Search products', 'Home', 'Go',
    ]);
  });
});

describe('snapshot', () => {
  it('lists visible interactive elements, viewport first, with details', () => {
    document.body.innerHTML = `
      <div data-top="2000"><a href="https://example.com/below">Below the fold</a></div>
      <form role="search"><input name="q" placeholder="Search" value="old"></form>
      <button aria-label="Search">Search</button>
      <button style="display:none">Hidden</button>
      <p>Not interactive</p>
      <select name="sort"><option>Relevance</option><option selected>Price</option></select>
      <input type="password" value="secret">
      <button disabled>Disabled</button>`;
    const snap = snapshot(document, registry);
    expect(snap.total).toBe(6);
    const names = snap.elements.map((e) => e.name);
    // An unlabeled <select> has no name; its current choice is its value.
    expect(names).toEqual(['Search', 'Search', undefined, undefined, 'Disabled', 'Below the fold']);
    const [input, button, select, password, disabled, link] = snap.elements;
    expect(input).toMatchObject({ tag: 'input', role: 'textbox', value: 'old', in_viewport: true });
    expect(button).toMatchObject({ tag: 'button', role: 'button', name: 'Search' });
    expect(select).toMatchObject({ value: 'Price', options: ['Relevance', 'Price'] });
    expect(password).toMatchObject({ type: 'password', value: '••••' });
    expect(disabled?.disabled).toBe(true);
    expect(link).toMatchObject({ role: 'link', href: 'https://example.com/below', in_viewport: false });
    expect(snap.elements.every((e) => /^el_[a-z0-9]+_\d+$/.test(e.id))).toBe(true);
  });

  it('keeps IDs stable across snapshots and supports viewport scope and limits', () => {
    document.body.innerHTML = `<button>One</button><button>Two</button><div data-top="3000"><button>Far</button></div>`;
    const first = snapshot(document, registry);
    const second = snapshot(document, registry);
    expect(second.elements.map((e) => e.id)).toEqual(first.elements.map((e) => e.id));
    expect(snapshot(document, registry, { scope: 'viewport' }).elements.map((e) => e.name)).toEqual(['One', 'Two']);
    const limited = snapshot(document, registry, { max: 1 });
    expect(limited).toMatchObject({ total: 3, truncated: true });
    expect(limited.elements).toHaveLength(1);
  });

  it('adds nearby context to tell similar buttons apart', () => {
    document.body.innerHTML = `
      <ul>
        <li><h3>Laptop A</h3><span>₹70,000</span><button>Add to cart</button></li>
        <li><h3>Laptop B</h3><span>₹85,000</span><button>Add to cart</button></li>
      </ul>`;
    expect(snapshot(document, registry).elements.map((e) => e.context)).toEqual(['Laptop A', 'Laptop B']);
  });

  it('finds elements inside open shadow roots', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    host.attachShadow({ mode: 'open' }).innerHTML = '<button>Shadow button</button>';
    expect(snapshot(document, registry).elements.map((e) => e.name)).toEqual(['Shadow button']);
  });
});

describe('findElements', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <nav><a href="/news">News</a><a href="/search-tips">Search tips</a></nav>
      <form role="search">
        <input name="q" placeholder="Search the web">
        <button aria-label="Search">Search</button>
      </form>
      <button>Sign in</button>`;
  });

  it('identifies the search button by meaning', () => {
    const [best] = findElements(document, registry, 'search button');
    expect(best).toMatchObject({ tag: 'button', name: 'Search' });
  });

  it('identifies the search box', () => {
    const [best] = findElements(document, registry, 'search box');
    // The placeholder is the input's accessible name here.
    expect(best).toMatchObject({ tag: 'input', name: 'Search the web' });
  });

  it('matches plain text and respects a role filter', () => {
    expect(findElements(document, registry, 'Sign in')[0]).toMatchObject({ name: 'Sign in' });
    expect(findElements(document, registry, 'search', 'link').map((m) => m.name)).toEqual(['Search tips']);
  });

  it('returns nothing for unrelated descriptions', () => {
    expect(findElements(document, registry, 'shopping cart checkout')).toEqual([]);
  });
});

describe('listLinks', () => {
  it('lists main-content links first, deduplicated, http(s) only', () => {
    document.body.innerHTML = `
      <nav><a href="https://example.com/home">Home</a></nav>
      <main><p>${'text '.repeat(200)}</p>
        <a href="https://example.com/a">Article A</a>
        <a href="https://example.com/a">Article A</a>
        <a href="javascript:void(0)">JS link</a>
      </main>`;
    const { links, total } = listLinks(document, registry);
    expect(total).toBe(2);
    expect(links.map((l) => [l.text, l.in_main])).toEqual([
      ['Article A', true],
      ['Home', false],
    ]);
  });
});
