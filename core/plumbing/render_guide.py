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
guide/*.md (to ../README.md, ...) keeps resolving correctly
without rewriting. Only links between guide/*.md files themselves are rewritten, .md -> .html
(README.md -> index.html, everything else name.md -> name.html); an external link (http(s)://) or
a link that already climbs out of guide/ (../...) is left exactly as written.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
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
    ("for-analysts.md", "for-analysts.html", "For data analysts"),
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
:root{--ink:#15161a;--logo:#55585f;--dim:#5a5c66;--faint:#8c8f99;--line:#e4e4e8;--bg:#fff;--raise:#fafafb;--link:#1a4fd6}
@media(prefers-color-scheme:dark){
  :root{--logo:var(--ink);--ink:#e8edf3;--dim:#aeb9c7;--faint:#7f8b99;--line:#34404d;--bg:#14181d;--raise:#1d242c;--link:#79aef2}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Inter,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:0 20px}
header{border-bottom:1px solid var(--line);padding:16px 0;margin-bottom:8px;position:sticky;top:0;background:var(--bg);backdrop-filter:blur(8px);z-index:50}
header .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px}
.brand{display:inline-flex;align-items:center;gap:8px;font-size:14px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--ink);text-decoration:none;margin-right:8px}
.brand .logo{width:36px;height:36px;display:block;flex:none;background:var(--logo,currentColor);-webkit-mask:url(../logo.png?v=2) center/contain no-repeat;mask:url(../logo.png?v=2) center/contain no-repeat}
.brand .logo.spin{animation:logospin .8s cubic-bezier(.25,.6,.35,1)}
@keyframes logospin{to{transform:rotate(180deg)}}
@media(prefers-reduced-motion:reduce){.brand .logo.spin{animation:none}}
.brand .bt{display:flex;flex-direction:column}
.brand a{display:block;line-height:0}
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
html.theme-white{--ink:#15161a;--logo:#55585f;--dim:#5a5c66;--faint:#8c8f99;--line:#e4e4e8;--bg:#fff;--raise:#fafafb;--link:#1a4fd6}
html.theme-blue{--logo:var(--ink);--ink:#d9e7f5;--dim:#a9bed2;--faint:#7189a1;--line:#29435d;--bg:#071a2b;--raise:#0d263d;--link:#65c7f7}
html.theme-dark{--logo:var(--ink);--ink:#e8edf3;--dim:#aeb9c7;--faint:#7f8b99;--line:#34404d;--bg:#14181d;--raise:#1d242c;--link:#79aef2}
.themes{position:fixed;top:14px;right:18px;z-index:80;display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:9px;background:var(--bg);box-shadow:0 4px 16px rgba(20,20,24,.08)}
@media(max-width:760px){.themes{position:static;width:fit-content;margin:8px 10px 0 auto}}
html.theme-dark .themes{box-shadow:0 4px 16px rgba(0,0,0,.3)}
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
.search{position:relative;margin:20px 0 28px}
.search label{display:block;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);margin-bottom:7px}
.search input{width:100%;font:14px system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--raise);border:1px solid var(--line);border-radius:7px;padding:10px 13px}
.search input::placeholder{color:var(--faint)}
.search input:focus{outline:2px solid var(--link);outline-offset:1px}
.search-results{position:absolute;top:calc(100% + 6px);left:0;right:0;max-height:60vh;overflow-y:auto;background:var(--bg);border:1px solid var(--line);border-radius:9px;box-shadow:0 10px 30px rgba(20,20,24,.12);z-index:90}
html.theme-dark .search-results,html.theme-blue .search-results{box-shadow:0 10px 30px rgba(0,0,0,.4)}
.search-results a{display:block;padding:9px 12px;text-decoration:none;border-bottom:1px solid var(--line)}
.search-results a:last-child{border-bottom:0}
.search-results a.on,.search-results a:hover{background:var(--raise)}
.sr-t{display:block;font-size:12.5px;font-weight:650;color:var(--ink);margin-bottom:2px}
.sr-s{display:block;font-size:12px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
p.fine{font-size:12.5px;line-height:1.55;color:var(--faint)}
"""


def _page_title(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.*)$", md_text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


_SECTION_RE = re.compile(r'<h([1-4]) id="([^"]+)">(.*?)</h\1>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html_fragment: str) -> str:
    """Rendered HTML -> plain text for the search index: strip tags, unescape entities, collapse
    whitespace. Reused, not reimplemented, from already-rendered body_html -- no second pass over
    the raw markdown."""
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", html_fragment))).strip()


def _page_sections(body_html: str) -> list[tuple[str, str, str]]:
    """(heading_text, anchor, section_body_html) for every heading in one rendered page, plus a
    leading ("", "", ...) entry for any content before the first heading (or the whole page, if it
    has none)."""
    matches = list(_SECTION_RE.finditer(body_html))
    if not matches:
        return [("", "", body_html)]
    sections = []
    if matches[0].start() > 0:
        sections.append(("", "", body_html[: matches[0].start()]))
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_html)
        sections.append((_plain_text(m.group(3)), m.group(2), body_html[m.end() : end]))
    return sections


def build_search_index(rendered_pages: list[tuple[str, str, str]]) -> list[dict]:
    """One entry per section (a heading plus the text under it, or a page's lead-in text) across
    every guide page. `rendered_pages` is [(out_name, page_title, body_html), ...] -- plain data,
    no ranking or tokenizing here: SEARCH_SCRIPT does simple substring matching client-side, same
    spirit as the rest of this module (no dependency, understands only what's actually needed)."""
    index: list[dict] = []
    for out_name, page_title, body_html in rendered_pages:
        for heading, anchor, section_html in _page_sections(body_html):
            snippet = _plain_text(section_html)
            if not heading and not snippet:
                continue
            index.append({"page": out_name, "title": page_title, "heading": heading, "anchor": anchor, "snippet": snippet[:220]})
    return index


def _embed_json(data) -> str:
    """json.dumps, made safe to sit inside a <script> element: a literal `</` in a string value
    (e.g. a snippet quoting a closing tag) would otherwise end the script element early."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


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
(function(){var l=document.querySelector('.brand a'),s=l&&l.querySelector('.logo');if(!s)return;
['mouseenter','click'].forEach(function(e){l.addEventListener(e,function(){s.classList.add('spin');});});
s.addEventListener('animationend',function(){s.classList.remove('spin');});})();
</script>"""


# Client-side search over GUIDE_SEARCH_INDEX (embedded per page, see _embed_json/build_search_index).
# No third-party code, no fetch -- the index is inline, so this works from file:// too, not just
# http(s). Plain substring matching, ranked by where the match landed (heading/title beats
# snippet) -- same "understands only what's needed" spirit as the rest of this module; a fuzzier
# search is not something guide/*.md has ever needed. Progressive enhancement: without JavaScript
# the search box simply does nothing, same stance as COPY_SCRIPT.
SEARCH_SCRIPT = """<script>
(function(){
  var input=document.getElementById('guide-search-input');
  var panel=document.getElementById('guide-search-results');
  if(!input||!panel||typeof GUIDE_SEARCH_INDEX==='undefined')return;
  var items=[],active=-1;

  function esc(s){return String(s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}

  function linkFor(it){return it.page+(it.anchor?'#'+it.anchor:'');}

  function render(){
    if(!items.length){panel.hidden=true;panel.innerHTML='';return;}
    panel.innerHTML=items.map(function(it,i){
      var sub=it.heading&&it.heading!==it.title?esc(it.title)+' &rsaquo; '+esc(it.heading):esc(it.title);
      return '<a href="'+linkFor(it)+'"'+(i===active?' class="on"':'')+'>'+
             '<span class="sr-t">'+sub+'</span><span class="sr-s">'+esc(it.snippet.slice(0,120))+'</span></a>';
    }).join('');
    panel.hidden=false;
  }

  function search(q){
    q=q.trim().toLowerCase();
    active=-1;
    if(q.length<2){items=[];render();return;}
    var scored=[];
    GUIDE_SEARCH_INDEX.forEach(function(it){
      var h=(it.heading||'').toLowerCase(),t=(it.title||'').toLowerCase(),s=(it.snippet||'').toLowerCase();
      var score=-1;
      if(h.indexOf(q)===0||t.indexOf(q)===0)score=0;
      else if(h.indexOf(q)>-1)score=1;
      else if(t.indexOf(q)>-1)score=2;
      else if(s.indexOf(q)>-1)score=3;
      if(score>-1)scored.push([score,it]);
    });
    scored.sort(function(a,b){return a[0]-b[0];});
    items=scored.slice(0,8).map(function(p){return p[1];});
    render();
  }

  function moveActive(delta){
    if(!items.length)return;
    active=(active+delta+items.length)%items.length;
    render();
  }

  input.addEventListener('input',function(){search(input.value);});
  input.addEventListener('keydown',function(e){
    if(panel.hidden&&e.key!=='Escape')return;
    if(e.key==='ArrowDown'){e.preventDefault();moveActive(1);}
    else if(e.key==='ArrowUp'){e.preventDefault();moveActive(-1);}
    else if(e.key==='Enter'&&active>-1){location.href=linkFor(items[active]);}
    else if(e.key==='Escape'){panel.hidden=true;input.blur();}
  });
  document.addEventListener('click',function(e){
    if(e.target!==input&&!panel.contains(e.target))panel.hidden=true;
  });
  document.addEventListener('keydown',function(e){
    var el=document.activeElement,tag=el&&el.tagName?el.tagName.toLowerCase():'';
    if(e.key==='/'&&tag!=='input'&&tag!=='textarea'){e.preventDefault();input.focus();input.select();}
  });
})();
</script>"""


# Sits in the body of the guide's home page (README.md -> index.html) rather than in the shared
# header: a search box makes sense as the first thing a reader landing on the guide reaches for,
# not as a permanent fixture on every page. See _promote_before_marker_paragraph/SEARCH_HOME_MARKER
# below for exactly where.
SEARCH_WIDGET_HTML = """<div class="search">
<label for="guide-search-input">Search this guide</label>
<input id="guide-search-input" type="search" placeholder="Search guide: type to search…" aria-label="Search the guide" autocomplete="off">
<div id="guide-search-results" class="search-results" hidden></div>
</div>"""

# README.md's own words -- identifies the paragraph the search box goes before, and which then
# becomes a small, asterisked footnote under it. Plain text, not markup, so it still matches after
# render_markdown's inline formatting runs (no bold/code/links land on this particular sentence
# today).
SEARCH_HOME_MARKER = "this guide is what needs fixing."

FOOTNOTE_CLASS = "fine"


def _promote_before_marker_paragraph(body_html: str, marker: str, widget_html: str, footnote_class: str) -> str:
    """Moves `widget_html` to sit right before the <p> containing `marker`, and marks that
    paragraph with `footnote_class` (see PAGE_CSS's .fine) plus a literal leading asterisk, so it
    reads as a small footnote under the search box rather than the page's lead sentence. Leaves
    body_html unchanged if the marker isn't found, rather than raising -- a rewording of that
    sentence in README.md should not break the build, only silently drop the search box (caught
    instead by _verify's own check that it's present)."""
    at = body_html.find(marker)
    if at == -1:
        return body_html
    start = body_html.rfind("<p>", 0, at)
    if start == -1:
        return body_html
    before, para_open, rest = body_html[:start], body_html[start : start + 3], body_html[start + 3 :]
    return before + widget_html + para_open.replace("<p>", f'<p class="{footnote_class}">* ') + rest


def render_page(current_out_name: str, title: str, body_html: str, search_index: list[dict] | None = None) -> str:
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
<link rel="icon" type="image/png" href="../logo.png?v=2">
<link rel="apple-touch-icon" href="../logo.png?v=2">
{THEME_HEAD_SCRIPT}
<style>{PAGE_CSS}</style>
</head>
<body>
<header><div class="wrap">
<div class="brand"><a href="../index.html" aria-label="LLM-Archive home"><span class="logo" aria-hidden="true"></span></a><span class="bt">LLM-Archive<small>Guide</small></span></div>
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
and <a href="https://github.com/LLM-Archive/llm-archive.github.io/blob/master/SPEC.md">SPEC.md</a> for the project itself.
</footer>
{THEME_SCRIPT}
{COPY_SCRIPT}
<script>const GUIDE_SEARCH_INDEX = {_embed_json(search_index or [])};</script>
{SEARCH_SCRIPT}
</body>
</html>
"""


def build_guide(guide_dir: Path = DEFAULT_GUIDE_DIR, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or guide_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    for md_name, out_name, nav_label in PAGES:
        text = (guide_dir / md_name).read_text(encoding="utf-8")
        title = _page_title(text, nav_label)
        body_html = render_markdown(text)
        if out_name == "index.html":
            body_html = _promote_before_marker_paragraph(body_html, SEARCH_HOME_MARKER, SEARCH_WIDGET_HTML, FOOTNOTE_CLASS)
        rendered.append((out_name, title, body_html))
    search_index = build_search_index(rendered)
    written = []
    for out_name, title, body_html in rendered:
        page_html = render_page(out_name, title, body_html, search_index)
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

    got = render_inline("[`../README.md`](../README.md) §10")
    if 'href="../README.md"' not in got:
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
    if "http://" in SEARCH_SCRIPT or "https://" in SEARCH_SCRIPT or "fetch(" in SEARCH_SCRIPT or "src=" in SEARCH_SCRIPT:
        errors.append("render_page: the search script must not load anything external (index is embedded inline)")
    if "GUIDE_SEARCH_INDEX" not in page_html:
        errors.append("render_page: every page should embed GUIDE_SEARCH_INDEX (search.js needs it, even on pages without the search box itself)")

    promoted = _promote_before_marker_paragraph(f"<p>x {SEARCH_HOME_MARKER}</p>", SEARCH_HOME_MARKER, SEARCH_WIDGET_HTML, FOOTNOTE_CLASS)
    want = SEARCH_WIDGET_HTML + f'<p class="{FOOTNOTE_CLASS}">* x {SEARCH_HOME_MARKER}</p>'
    if promoted != want:
        errors.append(f"_promote_before_marker_paragraph: got {promoted!r}, want {want!r}")
    if _promote_before_marker_paragraph("<p>unrelated</p>", SEARCH_HOME_MARKER, SEARCH_WIDGET_HTML, FOOTNOTE_CLASS) != "<p>unrelated</p>":
        errors.append("_promote_before_marker_paragraph: a missing marker must leave body_html unchanged, not raise or corrupt it")

    sections = _page_sections('<p>orphan lead-in</p><h2 id="w">W</h2><p>body text</p>')
    if sections[0] != ("", "", "<p>orphan lead-in</p>") or sections[1][:2] != ("W", "w"):
        errors.append(f"_page_sections: content before the first heading should get its own leading entry, got {sections!r}")

    sample = [
        ("a.html", "Page A", '<h1 id="page-a">Page A</h1><p>intro text</p><h2 id="widgets">Widgets</h2><p>about widgets here</p>'),
        ("b.html", "Page B", '<h1 id="page-b">Page B</h1><p>nothing to do with the other page</p>'),
    ]
    idx = build_search_index(sample)
    if not any(e["page"] == "a.html" and e["heading"] == "Widgets" and "widgets" in e["snippet"] for e in idx):
        errors.append(f"build_search_index: missing expected 'Widgets' section, got {idx!r}")
    if not any(e["page"] == "a.html" and e["heading"] == "Page A" and "intro text" in e["snippet"] for e in idx):
        errors.append("build_search_index: intro text right after the H1 should belong to the H1's own section")
    if not any(e["page"] == "b.html" for e in idx):
        errors.append("build_search_index: a page with only its title heading should still get an entry")

    embedded = _embed_json([{"snippet": "a </script> tag and a <!-- comment -->"}])
    if "</script" in embedded or "<!--" in embedded:
        errors.append(f"_embed_json: did not escape a sequence that could break out of <script>, got {embedded!r}")

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
        if 'id="guide-search-input"' not in index_html or 'id="guide-search-results"' not in index_html:
            errors.append("build_guide: index.html (the guide's home page) should carry the search box")

        faq_html = (out_dir / "faq.html").read_text(encoding="utf-8")
        if 'id="guide-search-input"' in faq_html:
            errors.append("build_guide: the search box belongs on index.html only, not on every page")
        if 'href="glossary.html"' not in faq_html:
            errors.append("build_guide: faq.html should link to glossary.html, not glossary.md")
        if 'href="../README.md"' not in faq_html:
            errors.append("build_guide: faq.html should keep the cross-repo link to ../README.md")

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
