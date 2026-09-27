import { beforeEach, describe, expect, it } from 'vitest';
import { extractPageContent, mainContentRoot, normalizeWhitespace, visibleText } from './extract';

function setPage(html: string, title = 'Test Page'): void {
  document.documentElement.lang = 'en';
  document.head.innerHTML = `<title>${title}</title><meta name="description" content="  A test   page ">`;
  document.body.innerHTML = html;
}

const LONG = 'Browsers render web pages. '.repeat(30);

describe('visibleText', () => {
  beforeEach(() => setPage(''));

  it('keeps visible text and separates blocks', () => {
    setPage('<h1>Title</h1><p>First <b>para</b>.</p><p>Second</p>');
    expect(visibleText(document.body)).toBe('Title\n\nFirst para.\n\nSecond');
  });

  it('drops scripts, hidden elements and page chrome', () => {
    setPage(`
      <header><a href="/">Site logo</a></header>
      <nav>Home | About</nav>
      <script>var secret = 1;</script>
      <style>p { color: red }</style>
      <p hidden>hidden attr</p>
      <p style="display:none">display none</p>
      <p aria-hidden="true">aria hidden</p>
      <p>Visible</p>
      <aside>Related links</aside>
      <footer>Copyright</footer>`);
    expect(visibleText(document.body)).toBe('Visible');
  });

  it("keeps an article's own header", () => {
    setPage('<article><header><h1>Story</h1></header><p>Body</p></article>');
    expect(visibleText(document.body)).toBe('Story\n\nBody');
  });

  it('describes images and flattens tables', () => {
    setPage('<img alt="A cat"><table><tr><th>Name</th><th>Price</th></tr><tr><td>X</td><td>₹10</td></tr></table>');
    const text = visibleText(document.body);
    expect(text).toContain('[image: A cat]');
    expect(text).toContain('Name | Price |');
    expect(text).toContain('X | ₹10 |');
  });
});

describe('mainContentRoot', () => {
  it('prefers a substantial <main>', () => {
    setPage(`<nav>Menu</nav><main><p>${LONG}</p></main><div>Sidebar widget</div>`);
    expect(mainContentRoot(document).tagName).toBe('MAIN');
  });

  it('falls back to body when the landmark is tiny', () => {
    setPage(`<main>Hi</main><div><p>${LONG}</p></div>`);
    expect(mainContentRoot(document).tagName).toBe('BODY');
  });

  it('falls back to body when there are several articles', () => {
    setPage(`<article><p>${LONG}</p></article><article><p>${LONG}</p></article>`);
    expect(mainContentRoot(document).tagName).toBe('BODY');
  });
});

describe('extractPageContent', () => {
  it('returns structured content with metadata', () => {
    setPage(`<main><h1>Guide</h1><h2>Setup</h2><p>${LONG}</p><h4>ignored level</h4></main>`, 'My Guide');
    const content = extractPageContent(document);
    expect(content.title).toBe('My Guide');
    expect(content.description).toBe('A test page');
    expect(content.lang).toBe('en');
    expect(content.headings).toEqual([
      { level: 1, text: 'Guide' },
      { level: 2, text: 'Setup' },
    ]);
    expect(content.text.startsWith('Guide\n\nSetup')).toBe(true);
    expect(content.truncated).toBe(false);
    expect(content.total_chars).toBe(content.text.length);
    expect(content.url).toBe(document.location.href);
  });

  it('reports truncation', () => {
    setPage(`<p>${LONG}</p>`);
    const content = extractPageContent(document, 100);
    expect(content.text).toHaveLength(100);
    expect(content.truncated).toBe(true);
    expect(content.total_chars).toBeGreaterThan(100);
  });
});

describe('normalizeWhitespace', () => {
  it('collapses spaces and blank lines', () => {
    expect(normalizeWhitespace('  a   b \n\n\n\n c  ')).toBe('a b\n\nc');
  });
});
