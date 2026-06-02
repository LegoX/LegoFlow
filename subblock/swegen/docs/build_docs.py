#!/usr/bin/env python3
"""Build the SWE-gen docs site.

This intentionally stays stdlib-only so Cloudflare Pages can build the site
without a project-level Node or Python dependency graph.
"""

from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


DOCS_ROOT = Path(__file__).resolve().parent
CONTENT_ROOT = DOCS_ROOT / "content"
SITE_ROOT = DOCS_ROOT / "site"
DASHBOARD_URL = "https://swe-databoard.pages.dev/"
DOCS_URL = "https://swe-swegen-docs.pages.dev"


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    description: str
    source: Path
    output: Path
    previous_slug: str | None = None
    next_slug: str | None = None

    @property
    def url(self) -> str:
        if self.slug == "index":
            return "/"
        return f"/docs/{self.slug}/"


NAV = [
    ("index", "Motivation"),
    ("getting-started", "Getting Started"),
    ("core-concepts", "Core Concepts"),
    ("run-generation", "Run Generation"),
    ("outputs", "Outputs"),
    ("dashboard", "Dashboard"),
]


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-") or "section"


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    meta_raw = raw[4:end].strip()
    body = raw[end + 5 :]
    meta: dict[str, str] = {}
    for line in meta_raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta, body


def inline_markup(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def render_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) < 2:
        return "\n".join(f"<p>{inline_markup(line)}</p>" for line in lines)
    header, body = rows[0], rows[2:]
    out = ["<div class=\"table-wrap\"><table>", "<thead><tr>"]
    out.extend(f"<th>{inline_markup(cell)}</th>" for cell in header)
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>")
        out.extend(f"<td>{inline_markup(cell)}</td>" for cell in row)
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def render_markdown(markdown: str) -> tuple[str, list[tuple[int, str, str]]]:
    html_parts: list[str] = []
    toc: list[tuple[int, str, str]] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    table_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            html_parts.append(f"<p>{inline_markup(' '.join(paragraph))}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            html_parts.append("<ul>")
            html_parts.extend(f"<li>{inline_markup(item)}</li>" for item in list_items)
            html_parts.append("</ul>")
            list_items = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            html_parts.append(render_table(table_lines))
            table_lines = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            flush_list()
            flush_table()
            if in_code:
                html_parts.append(
                    "<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>"
                )
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            flush_table()
            continue
        if line.startswith("|") and line.endswith("|"):
            flush_paragraph()
            flush_list()
            table_lines.append(line)
            continue
        flush_table()
        if line.startswith("#"):
            flush_paragraph()
            flush_list()
            level = len(line) - len(line.lstrip("#"))
            text = line[level:].strip()
            anchor = slugify(text)
            toc.append((level, text, anchor))
            html_parts.append(
                f"<h{level} id=\"{anchor}\">{inline_markup(text)}"
                f"<a class=\"anchor\" href=\"#{anchor}\" aria-label=\"Link to section\">#</a>"
                f"</h{level}>"
            )
            continue
        if line.startswith("- "):
            flush_paragraph()
            list_items.append(line[2:].strip())
            continue
        paragraph.append(line.strip())

    flush_paragraph()
    flush_list()
    flush_table()
    if in_code:
        html_parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(html_parts), toc


def load_pages() -> list[Page]:
    pages: list[Page] = []
    for idx, (slug, fallback_title) in enumerate(NAV):
        source = CONTENT_ROOT / f"{slug}.md"
        meta, _ = parse_frontmatter(source.read_text(encoding="utf-8"))
        output = SITE_ROOT / "index.html" if slug == "index" else SITE_ROOT / "docs" / slug / "index.html"
        pages.append(
            Page(
                slug=slug,
                title=meta.get("title", fallback_title),
                description=meta.get("description", ""),
                source=source,
                output=output,
                previous_slug=NAV[idx - 1][0] if idx > 0 else None,
                next_slug=NAV[idx + 1][0] if idx < len(NAV) - 1 else None,
            )
        )
    return pages


def page_by_slug(pages: list[Page]) -> dict[str, Page]:
    return {page.slug: page for page in pages}


def render_layout(page: Page, pages: list[Page], body_html: str, toc: list[tuple[int, str, str]]) -> str:
    lookup = page_by_slug(pages)
    nav_html = "\n".join(
        f'<a class="nav-link{" active" if nav_slug == page.slug else ""}" '
        f'href="{lookup[nav_slug].url}">{label}</a>'
        for nav_slug, label in NAV
    )
    toc_items = "\n".join(
        f'<a class="toc-level-{level}" href="#{anchor}">{html.escape(text)}</a>'
        for level, text, anchor in toc
        if level in (2, 3)
    )
    if not toc_items:
        toc_items = '<span class="muted">No sections</span>'

    previous_link = ""
    if page.previous_slug:
        prev = lookup[page.previous_slug]
        previous_link = f'<a class="pager-card" href="{prev.url}"><span>Previous</span>{prev.title}</a>'
    next_link = ""
    if page.next_slug:
        nxt = lookup[page.next_slug]
        next_link = f'<a class="pager-card next" href="{nxt.url}"><span>Next</span>{nxt.title}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page.title)} | SWE-gen Docs</title>
  <meta name="description" content="{html.escape(page.description)}">
  <link rel="stylesheet" href="/assets/styles.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">
      <span class="brand-mark">S</span>
      <span><strong>SWE-gen Docs</strong><small>Verified SWE task generation</small></span>
    </a>
    <nav class="top-actions">
      <a href="{DASHBOARD_URL}">Dashboard</a>
      <a href="https://github.com/SWE-Lego/SWE-Lego-Live">GitHub</a>
    </nav>
  </header>
  <div class="shell">
    <aside class="sidebar">
      <div class="sidebar-title">Documentation</div>
      {nav_html}
      <a class="dashboard-card" href="{DASHBOARD_URL}">
        <strong>Live Databoard</strong>
        <span>Open SWE-gen progress dashboard</span>
      </a>
    </aside>
    <main class="content">
      <p class="eyebrow">SWE-Lego Live / swegen</p>
      {body_html}
      <nav class="pager">{previous_link}{next_link}</nav>
    </main>
    <aside class="toc">
      <div class="toc-title">On this page</div>
      {toc_items}
    </aside>
  </div>
  <footer class="footer">
    Published as <a href="{DOCS_URL}">{DOCS_URL}</a>. Dashboard: <a href="{DASHBOARD_URL}">{DASHBOARD_URL}</a>.
  </footer>
</body>
</html>
"""


def write_assets() -> None:
    assets = SITE_ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "styles.css").write_text(
        """
:root {
  color-scheme: light dark;
  --bg: #fafafa;
  --panel: #ffffff;
  --text: #171717;
  --muted: #737373;
  --border: #e5e5e5;
  --accent: #2563eb;
  --accent-soft: #dbeafe;
  --code: #f5f5f5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0a0a0a;
    --panel: #111111;
    --text: #f5f5f5;
    --muted: #a3a3a3;
    --border: #262626;
    --accent: #60a5fa;
    --accent-soft: #172554;
    --code: #1a1a1a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.65;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  height: 72px;
  padding: 0 32px;
  background: color-mix(in srgb, var(--panel) 92%, transparent);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(12px);
}
.brand { display: flex; align-items: center; gap: 12px; color: var(--text); }
.brand:hover { text-decoration: none; }
.brand-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--accent);
  color: white;
  font-weight: 800;
}
.brand small { display: block; color: var(--muted); font-size: 12px; }
.top-actions { display: flex; gap: 18px; font-size: 14px; }
.shell {
  display: grid;
  grid-template-columns: 260px minmax(0, 800px) 220px;
  gap: 40px;
  max-width: 1360px;
  margin: 0 auto;
  padding: 36px 32px 56px;
}
.sidebar, .toc { position: sticky; top: 100px; align-self: start; }
.sidebar-title, .toc-title {
  margin-bottom: 12px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.nav-link {
  display: block;
  padding: 9px 12px;
  border-radius: 8px;
  color: var(--muted);
  font-size: 14px;
}
.nav-link.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 700;
}
.dashboard-card {
  display: block;
  margin-top: 24px;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel);
}
.dashboard-card strong { display: block; color: var(--text); }
.dashboard-card span { display: block; color: var(--muted); font-size: 13px; }
.content {
  min-width: 0;
  padding-bottom: 48px;
}
.eyebrow {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}
h1 {
  margin: 0 0 12px;
  font-size: clamp(2.2rem, 5vw, 4.3rem);
  line-height: 1.05;
  letter-spacing: -0.05em;
}
h2 {
  margin-top: 42px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
  font-size: 1.55rem;
}
h3 { margin-top: 28px; font-size: 1.15rem; }
p, li { color: color-mix(in srgb, var(--text) 88%, var(--muted)); }
.anchor {
  margin-left: 8px;
  color: var(--muted);
  opacity: 0;
  font-size: .8em;
}
h2:hover .anchor, h3:hover .anchor { opacity: 1; }
code {
  padding: 2px 5px;
  border-radius: 5px;
  background: var(--code);
  font-size: .92em;
}
pre {
  overflow-x: auto;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--code);
}
pre code { padding: 0; background: transparent; }
.table-wrap { overflow-x: auto; margin: 20px 0; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 700; }
.toc a, .toc .muted {
  display: block;
  margin: 8px 0;
  color: var(--muted);
  font-size: 13px;
}
.toc-level-3 { padding-left: 12px; }
.pager {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 56px;
}
.pager-card {
  display: block;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel);
  color: var(--text);
  font-weight: 700;
}
.pager-card span { display: block; color: var(--muted); font-size: 12px; font-weight: 600; text-transform: uppercase; }
.pager-card.next { text-align: right; grid-column: 2; }
.footer {
  max-width: 1360px;
  margin: 0 auto;
  padding: 24px 32px 40px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 13px;
}
@media (max-width: 1100px) {
  .shell { grid-template-columns: 220px minmax(0, 1fr); }
  .toc { display: none; }
}
@media (max-width: 760px) {
  .topbar { position: static; padding: 16px; height: auto; align-items: flex-start; }
  .top-actions { flex-direction: column; gap: 8px; }
  .shell { display: block; padding: 24px 18px; }
  .sidebar { position: static; margin-bottom: 28px; }
  .pager { grid-template-columns: 1fr; }
  .pager-card.next { grid-column: auto; text-align: left; }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if SITE_ROOT.exists():
        shutil.rmtree(SITE_ROOT)
    pages = load_pages()
    write_assets()
    for page in pages:
        meta, body = parse_frontmatter(page.source.read_text(encoding="utf-8"))
        body_html, toc = render_markdown(body)
        page.output.parent.mkdir(parents=True, exist_ok=True)
        page.output.write_text(render_layout(page, pages, body_html, toc), encoding="utf-8")
    print(f"Built {len(pages)} pages into {SITE_ROOT}")


if __name__ == "__main__":
    main()
