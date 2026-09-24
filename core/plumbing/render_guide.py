"""Turns guide/*.md into guide/*.html -- a small, static, standalone docs site sitting next to
the closed 8-page site spec.md §11 describes, not inside it (guide/README.md: "written so it can
eventually be lifted, close to as-is, into a public docs site alongside the main website").

    python3 -m core.plumbing.render_guide build   [--guide-dir guide] [--out-dir guide]
    python3 -m core.plumbing.render_guide verify

Deliberately a hand-rolled markdown -> HTML converter, not a dependency: `core/measure/` is
pure standard library so it can be re-implemented decades from now without trusting some other
library's future (see guide/for-developers.md's "Why numpy is banned"); the same reasoning applies
here -- a docs site is exactly the kind of thing that should still build after everything with a
CDN link has bit-rotted. It understands only what guide/*.md actually use: ATX headings, **bold**,
*italic*, `code` spans, fenced ``` code blocks, [links](url), pipe tables, and flat (optionally
wrapped) `-`/`1.` lists -- not the rest of CommonMark. A markdown construct guide/*.md doesn't use
today is not supported; add it here the day it's actually needed, per the same file's own
prefer-inaction rule.

Output sits next to its markdown source (guide/index.html next to guide/README.md, mirroring
website/index.html next to website/template.html) so every existing "../X" cross-repo link in
guide/*.md (to docs/spec.md, ../README.md, ../CONTRIBUTING.md, ...) keeps resolving correctly
without rewriting. Only links between guide/*.md files themselves are rewritten, .md -> .html
(README.md -> index.html, everything else name.md -> name.html); an external link (http(s)://) or
a link that already climbs out of guide/ (../...) is left exactly as written.
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GUIDE_DIR = ROOT / "guide"

# (source markdown, generated html, label shown in the shared nav bar) -- this is the guide's own
# closed page list, same spirit as spec.md §11's for the main site: a fixed set, not a directory
# scan, so a stray file in guide/ doesn't silently appear or disappear from the site.
PAGES: list[tuple[str, str, str]] = [
    ("README.md", "index.html", "Guide home"),
    ("for-researchers.md", "for-researchers.html", "For researchers"),
    ("for-developers.md", "for-developers.html", "For developers"),
    ("reproducing.md", "reproducing.html", "Reproducing"),
    ("data-dictionary.md", "data-dictionary.html", "Data dictionary"),
    ("glossary.md", "glossary.html", "Glossary"),
    ("faq.md", "faq.html", "FAQ"),
    ("timestamps.md", "timestamps.html", "Time proofs"),
]

_MD_TO_HTML = {md: out for md, out, _ in PAGES}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,})\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST_MARKER_RE = re.compile(r"^(\s*)(?:[-*]|\d+\.)\s+(.*)$")
_ORDERED_MARKER_RE = re.compile(r"^\s*\d+\.\s+")

_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"\*([^*]+)\*")


def slugify(raw_text: str) -> str:
    """Heading id for anchor links -- derived from the raw (pre-inline) heading text."""
    s = raw_text.lower()
    s = re.sub(r"[`*_\[\]()]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "section"


def rewrite_href(url: str) -> str:
    if url.startswith(("http://", "https://", "../", "#", "mailto:")):
        return url
    if url.endswith(".md") or ".md#" in url:
        path, _, frag = url.partition("#")
        name = Path(path).name
        out_name = _MD_TO_HTML.get(name, name[:-3] + ".html" if name.endswith(".md") else name)
        return out_name + (f"#{frag}" if frag else "")
    return url


def render_inline(text: str) -> str:
    text = html_lib.escape(text, quote=False)
    text = _CODE_SPAN_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _LINK_RE.sub(lambda m: f'<a href="{rewrite_href(m.group(2))}">{m.group(1)}</a>', text)
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


def _split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    thead = "".join(f"<th>{render_inline(c)}</th>" for c in header)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return f'<div class="twrap"><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>'


def _consume_list(lines: list[str], i: int) -> tuple[list[str], bool, int]:
    """Collects one flat list starting at lines[i] -- items plus any indented, unmarked
    continuation lines (a soft-wrapped sentence inside one item), stopping at a blank line or a
    line that isn't part of the list. Returns (item_texts, is_ordered, next_index)."""
    n = len(lines)
    m = _LIST_MARKER_RE.match(lines[i])
    ordered = bool(_ORDERED_MARKER_RE.match(lines[i]))
    items = [m.group(2).strip()]
    i += 1
    while i < n:
        line = lines[i]
        if line.strip() == "":
            break
        m = _LIST_MARKER_RE.match(line)
        if m:
            items.append(m.group(2).strip())
            i += 1
        elif line.startswith((" ", "\t")):
            items[-1] += " " + line.strip()
            i += 1
        else:
            break
    return items, ordered, i


def render_markdown(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    n = len(lines)
    out: list[str] = []
    i = 0
    while i < n:
        line = lines[i]

        if line.strip() == "":
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            lang = fence.group(1)
            i += 1
            code_lines = []
            while i < n and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip the closing fence
            code = html_lib.escape("\n".join(code_lines))
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{code}</code></pre>")
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            raw = heading.group(2).strip()
            out.append(f'<h{level} id="{slugify(raw)}">{render_inline(raw)}</h{level}>')
            i += 1
            continue

        if _HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _split_table_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_table_row(lines[i]))
                i += 1
            out.append(_render_table(header, rows))
            continue

        if _LIST_MARKER_RE.match(line):
            items, ordered, i = _consume_list(lines, i)
            tag = "ol" if ordered else "ul"
            lis = "\n".join(f"<li>{render_inline(item)}</li>" for item in items)
            out.append(f"<{tag}>\n{lis}\n</{tag}>")
            continue

        if line.lstrip().startswith(">"):
            quote_lines = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote><p>{render_inline(' '.join(quote_lines))}</p></blockquote>")
            continue

        para_lines = []
        while i < n and lines[i].strip() != "" and not _starts_block(lines[i]):
            para_lines.append(lines[i].strip())
            i += 1
        out.append(f"<p>{render_inline(' '.join(para_lines))}</p>")

    return "\n".join(out)


def _starts_block(line: str) -> bool:
    return bool(
        _HEADING_RE.match(line)
        or _FENCE_RE.match(line)
        or _HR_RE.match(line)
        or _LIST_MARKER_RE.match(line)
        or line.lstrip().startswith(">")
    )


PAGE_CSS = """
:root{--ink:#15161a;--dim:#5a5c66;--faint:#8c8f99;--line:#e4e4e8;--bg:#fff;--raise:#fafafb;--link:#1a4fd6}
@media(prefers-color-scheme:dark){
  :root{--ink:#e8edf3;--dim:#aeb9c7;--faint:#7f8b99;--line:#34404d;--bg:#14181d;--raise:#1d242c;--link:#79aef2}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Inter,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:0 20px}
header{border-bottom:1px solid var(--line);padding:16px 0;margin-bottom:8px}
header .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px}
.brand{font-size:14px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--ink);text-decoration:none;margin-right:8px}
.brand small{display:block;font-size:10px;font-weight:600;letter-spacing:.08em;color:var(--faint)}
nav{display:flex;flex-wrap:wrap;gap:2px}
nav a{font-size:13px;color:var(--dim);text-decoration:none;padding:5px 9px;border-radius:6px}
nav a.on{color:var(--ink);font-weight:650;background:var(--raise)}
main{padding:28px 0 80px}
h1{font-size:clamp(22px,3.6vw,28px);line-height:1.3;font-weight:660;letter-spacing:-.02em;margin:0 0 16px}
h2{font-size:clamp(19px,3.2vw,23px);line-height:1.3;letter-spacing:-.015em;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3,h4{font-size:15px;font-weight:700;margin:22px 0 8px}
h1:target,h2:target,h3:target,h4:target{scroll-margin-top:16px}
p{margin:0 0 14px}
ul,ol{margin:0 0 14px;padding-left:22px}
li{margin-bottom:6px}
a{color:var(--link)}
code{font:13px ui-monospace,"SF Mono",Menlo,monospace;background:var(--raise);padding:1px 5px;border-radius:4px;border:1px solid var(--line)}
pre{background:var(--raise);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow-x:auto;margin:0 0 16px}
pre code{background:none;border:0;padding:0;font-size:13px;line-height:1.55}
html.theme-white{--ink:#15161a;--dim:#5a5c66;--faint:#8c8f99;--line:#e4e4e8;--bg:#fff;--raise:#fafafb;--link:#1a4fd6}
html.theme-blue{--ink:#d9e7f5;--dim:#a9bed2;--faint:#7189a1;--line:#29435d;--bg:#071a2b;--raise:#0d263d;--link:#65c7f7}
html.theme-dark{--ink:#e8edf3;--dim:#aeb9c7;--faint:#7f8b99;--line:#34404d;--bg:#14181d;--raise:#1d242c;--link:#79aef2}
.themes{position:fixed;top:14px;right:18px;z-index:80;display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:9px;background:var(--bg);box-shadow:0 4px 16px rgba(20,20,24,.08)}
html.theme-dark .themes{box-shadow:0 4px 16px rgba(0,0,0,.3)}
@media(max-width:760px){.themes{position:static;width:fit-content;margin:8px 10px 0 auto}}
.themes button{border:0;border-radius:6px;background:transparent;color:var(--dim);cursor:pointer;font:11px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif;padding:6px 8px}
.themes button:hover,.themes button.on{background:var(--raise);color:var(--ink);font-weight:650}
.codewrap{position:relative;margin:0 0 16px}
.codewrap pre{margin:0}
.copybtn{position:absolute;top:8px;right:8px;font:600 12px system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--dim);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer;opacity:.85}
.copybtn:hover,.copybtn:focus-visible{opacity:1;color:var(--ink);border-color:var(--faint)}
.copybtn.done{color:var(--ink);border-color:var(--ink);opacity:1}
blockquote{margin:0 0 16px;padding:2px 16px;border-left:3px solid var(--line);color:var(--dim)}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}
.twrap{overflow-x:auto;margin:0 0 18px}
table{width:100%;border-collapse:collapse;font-size:14.5px}
th{text-align:left;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);padding:0 14px 8px 0;border-bottom:2px solid var(--line);white-space:nowrap}
td{padding:10px 14px 10px 0;border-bottom:1px solid var(--line);vertical-align:top}
footer{border-top:1px solid var(--line);padding:18px 0;font-size:12.5px;color:var(--faint)}
footer a{color:var(--faint)}
"""


def _page_title(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.*)$", md_text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


# Adds a "Copy" button to every fenced code block. Progressive enhancement only: the <pre><code>
# markup render_markdown emits is unchanged, so without JavaScript the blocks read exactly as before.
# No third-party code -- navigator.clipboard where available, a textarea + execCommand fallback
# where it is not (plain http, file://, older browsers).
COPY_SCRIPT = """<script>
(function(){
  function copyText(text){
    if(navigator.clipboard&&window.isSecureContext){return navigator.clipboard.writeText(text);}
    return new Promise(function(resolve,reject){
      var t=document.createElement('textarea');t.value=text;t.setAttribute('readonly','');
      t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();
      try{document.execCommand('copy')?resolve():reject();}catch(e){reject(e);}
      document.body.removeChild(t);
    });
  }
  document.querySelectorAll('pre').forEach(function(pre){
    var code=pre.querySelector('code');if(!code)return;
    var wrap=document.createElement('div');wrap.className='codewrap';
    pre.parentNode.insertBefore(wrap,pre);wrap.appendChild(pre);
    var b=document.createElement('button');b.type='button';b.className='copybtn';
    b.textContent='Copy';b.setAttribute('aria-label','Copy code to clipboard');
    b.addEventListener('click',function(){
      copyText(code.textContent).then(function(){b.textContent='Copied';b.classList.add('done');},
        function(){b.textContent='Press Ctrl+C';});
      setTimeout(function(){b.textContent='Copy';b.classList.remove('done');},1600);
    });
    wrap.appendChild(b);
  });
})();
</script>"""


# Theme picker, the same three themes and the same localStorage key as the main site
# (website/template.html), so a choice made on either is kept on the other -- the guide is served
# from the same origin. The early script runs in <head> and sets the class on <html> before the page
# is painted, so there is no flash of the wrong theme. Default 'blue', like the main site. Without
# JavaScript the page keeps following the system light/dark setting (PAGE_CSS's media query).
THEME_HEAD_SCRIPT = """<script>
(function(){var t='blue';try{t=localStorage.getItem('llm-archive-theme')||'blue';}catch(e){}
if(t!=='white'&&t!=='dark'&&t!=='blue')t='blue';document.documentElement.classList.add('theme-'+t);})();
</script>"""

THEME_SCRIPT = """<script>
(function(){
  var buttons=document.querySelectorAll('.themes [data-theme]');
  function apply(t){
    var c=document.documentElement.classList;c.remove('theme-blue','theme-dark','theme-white');c.add('theme-'+t);
    buttons.forEach(function(b){var on=b.dataset.theme===t;b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on));});
    try{localStorage.setItem('llm-archive-theme',t);}catch(e){}
  }
  var cur='blue';['white','dark','blue'].forEach(function(t){if(document.documentElement.classList.contains('theme-'+t))cur=t;});
  buttons.forEach(function(b){b.addEventListener('click',function(){apply(b.dataset.theme);});});
  apply(cur);
})();
</script>"""


def render_page(current_out_name: str, title: str, body_html: str) -> str:
    def _nav_link(out_name: str, label: str) -> str:
        cls = ' class="on"' if out_name == current_out_name else ""
        return f'<a{cls} href="{out_name}">{label}</a>'

    nav = "\n".join(_nav_link(out_name, label) for _, out_name, label in PAGES)
    suffix = "" if "LLM-Archive" in title else " — LLM-Archive Guide"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(title)}{suffix}</title>
{THEME_HEAD_SCRIPT}
<style>{PAGE_CSS}</style>
</head>
<body>
<header><div class="wrap">
<a class="brand" href="index.html">LLM-Archive<small>Guide</small></a>
<nav>{nav}</nav>
</div></header>
<div class="themes" role="group" aria-label="Theme picker">
<button type="button" data-theme="blue">Blue</button>
<button type="button" data-theme="white">Light</button>
<button type="button" data-theme="dark">Dark</button>
</div>
<main class="wrap">
{body_html}
</main>
<footer class="wrap">
Written for people using LLM-Archive — see <a href="../README.md">../README.md</a>
and <a href="../docs/spec.md">docs/spec.md</a> for the project itself.
</footer>
{THEME_SCRIPT}
{COPY_SCRIPT}
</body>
</html>
"""


def build_guide(guide_dir: Path = DEFAULT_GUIDE_DIR, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or guide_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for md_name, out_name, nav_label in PAGES:
        text = (guide_dir / md_name).read_text(encoding="utf-8")
        title = _page_title(text, nav_label)
        page_html = render_page(out_name, title, render_markdown(text))
        out_path = out_dir / out_name
        out_path.write_text(page_html, encoding="utf-8")
        written.append(out_path)
    return written


def _verify() -> list[str]:
    errors = []

    if render_inline("**bold**") != "<strong>bold</strong>":
        errors.append(f"render_inline: bold, got {render_inline('**bold**')!r}")
    if render_inline("*em*") != "<em>em</em>":
        errors.append(f"render_inline: italic, got {render_inline('*em*')!r}")
    if render_inline("a `code` span") != "a <code>code</code> span":
        errors.append(f"render_inline: code span, got {render_inline('a `code` span')!r}")
    if render_inline("x < y & z") != "x &lt; y &amp; z":
        errors.append(f"render_inline: html-escaping, got {render_inline('x < y & z')!r}")

    got = render_inline("[`glossary.md`](glossary.md)")
    want = '<a href="glossary.html"><code>glossary.md</code></a>'
    if got != want:
        errors.append(f"render_inline: sibling .md link, got {got!r}, want {want!r}")

    got = render_inline("[`../docs/spec.md`](../docs/spec.md) §10")
    if 'href="../docs/spec.md"' not in got:
        errors.append(f"render_inline: cross-repo ../ link should be left unrewritten, got {got!r}")

    got = render_inline("[README](README.md)")
    if 'href="index.html"' not in got:
        errors.append(f"rewrite_href: README.md should map to index.html, got {got!r}")

    got = render_inline("[Croissant](https://mlcommons.org/croissant/)")
    if 'href="https://mlcommons.org/croissant/"' not in got:
        errors.append(f"rewrite_href: an absolute URL should be left unrewritten, got {got!r}")

    heading_html = render_markdown("## `open-lane/<year>.jsonl`")
    if '<h2 id="open-lane-year-jsonl">' not in heading_html or "&lt;year&gt;" not in heading_html:
        errors.append(f"render_markdown: heading with inline code/angle-brackets, got {heading_html!r}")

    list_html = render_markdown("- one\n- two, wrapped onto\n  a second line\n- three")
    if list_html.count("<li>") != 3 or "two, wrapped onto a second line" not in list_html:
        errors.append(f"render_markdown: wrapped list item not joined, got {list_html!r}")

    ordered_html = render_markdown("1. first\n2. second")
    if "<ol>" not in ordered_html or ordered_html.count("<li>") != 2:
        errors.append(f"render_markdown: ordered list, got {ordered_html!r}")

    table_html = render_markdown("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
    if table_html.count("<th>") != 2 or table_html.count("<td>") != 4:
        errors.append(f"render_markdown: table, got {table_html!r}")

    code_html = render_markdown("```bash\npython3 <file>.json\n```")
    if "<pre><code" not in code_html or "&lt;file&gt;" not in code_html or "python3 <file>" in code_html:
        errors.append(f"render_markdown: fenced code block not escaped verbatim, got {code_html!r}")

    page_html = render_page("x.html", "T", "<pre><code>x</code></pre>")
    if "copybtn" not in page_html or "clipboard" not in page_html or "</script>" not in page_html:
        errors.append("render_page: the copy-button script is missing from the page")
    for needle in ('data-theme="blue"', 'data-theme="white"', 'data-theme="dark"', "llm-archive-theme"):
        if needle not in page_html:
            errors.append(f"render_page: the theme picker is missing {needle}")
    for name, blob in (("THEME_HEAD_SCRIPT", THEME_HEAD_SCRIPT), ("THEME_SCRIPT", THEME_SCRIPT)):
        if "http://" in blob or "https://" in blob or "src=" in blob:
            errors.append(f"render_page: {name} must not load anything external")
    if "http://" in COPY_SCRIPT or "https://" in COPY_SCRIPT or "src=" in COPY_SCRIPT:
        errors.append("render_page: the copy-button script must not load anything external")

    if not DEFAULT_GUIDE_DIR.exists():
        return errors

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        written = build_guide(DEFAULT_GUIDE_DIR, out_dir)
        if len(written) != len(PAGES):
            errors.append(f"build_guide: wrote {len(written)} file(s), want {len(PAGES)}")
        for path in written:
            if not path.exists():
                errors.append(f"build_guide: {path} was not written")

        index_html = (out_dir / "index.html").read_text(encoding="utf-8")
        nav_block = index_html.split("<nav>", 1)[1].split("</nav>", 1)[0]
        if nav_block.count("<a ") != len(PAGES):
            errors.append("build_guide: index.html nav should list every page")
        if "**" in re.sub(r"<pre>.*?</pre>", "", index_html, flags=re.S):
            errors.append("build_guide: index.html has unconverted '**' outside a code block")

        faq_html = (out_dir / "faq.html").read_text(encoding="utf-8")
        if 'href="glossary.html"' not in faq_html:
            errors.append("build_guide: faq.html should link to glossary.html, not glossary.md")
        if 'href="../docs/spec.md"' not in faq_html:
            errors.append("build_guide: faq.html should keep the cross-repo link to ../docs/spec.md")

    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="write guide/*.html from guide/*.md")
    p_build.add_argument("--guide-dir", type=Path, default=DEFAULT_GUIDE_DIR)
    p_build.add_argument("--out-dir", type=Path, default=None)

    sub.add_parser("verify", help="run this module's own hand-worked cases")

    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/render_guide.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    written = build_guide(args.guide_dir, args.out_dir)
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
