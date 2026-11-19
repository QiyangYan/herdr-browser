"""Portable presentation helpers for locally generated supporting reports.

No measurements are inferred. Paragraph conversions require an exact text
match and author-supplied structured content, so changed evidence is retained.
"""
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


STYLE = """
*{box-sizing:border-box}
strong{font-weight:700}
p,li,td,th,figcaption{overflow-wrap:anywhere}
p{max-width:72ch}h1,h2,h3{line-height:1.3}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
img,video,iframe,canvas{max-width:100%}
details{scroll-margin-top:18px}summary{cursor:pointer}
:focus-visible{outline:3px solid #8d5300;outline-offset:3px}
.clarity-task-return{font:17px/1.5 system-ui;margin:0 0 22px}
.clarity-task-return a{color:#285e9c}
.clarity-table{margin:24px 0}.clarity-table h2{font-size:23px}
.clarity-table table{width:100%;border-collapse:collapse;font-size:17px;line-height:1.5}
.clarity-table td,.clarity-table th{padding:11px 10px;text-align:left;vertical-align:top;border-bottom:1px solid #d4d8df}
.clarity-table figcaption{font-size:16px;line-height:1.5;color:#536478;margin-top:12px}
.clarity-flow{margin:24px 0;container-type:inline-size}.clarity-flow h2{font-size:23px}
.clarity-flow ol{display:flex;gap:28px;list-style:none;padding:0;counter-reset:step}
.clarity-flow li{flex:1;min-width:0;position:relative;background:#edf1f5;border-top:3px solid #507bb1;padding:15px;counter-increment:step}
.clarity-flow strong,.clarity-flow span{display:block;font-size:17px;line-height:1.5}
.clarity-flow strong:before{content:counter(step) ". ";color:#285e9c}
.clarity-flow span{margin-top:8px}.clarity-flow li:not(:last-child):after{content:"→";position:absolute;right:-24px;top:20px;color:#285e9c;font-size:25px}
.clarity-flow figcaption{font-size:16px;color:#536478;line-height:1.5}
@container(max-width:600px){
.clarity-flow ol{flex-direction:column;gap:26px}
.clarity-flow li:not(:last-child):after{content:"↓";right:50%;top:auto;bottom:-27px;transform:translateX(50%)}
}
@media(max-width:600px){
table[data-clarity-table],table[data-clarity-table] tbody,table[data-clarity-table] tr,
table[data-clarity-table] td,table[data-clarity-table] th{display:block}
table[data-clarity-table] thead{display:none}
table[data-clarity-table] tr{margin:12px 0;border-bottom:1px solid #bdc8d4;padding:8px 0}
table[data-clarity-table] td,table[data-clarity-table] th{white-space:normal!important;border:0;padding:6px 8px}
table[data-clarity-table] td:before{content:attr(data-label) ": ";font-weight:600}
.clarity-flow ol{flex-direction:column;gap:26px}
.clarity-flow li:not(:last-child):after{content:"↓";right:50%;top:auto;bottom:-27px;transform:translateX(50%)}
}
"""


def inline_text(text):
    """Author-selected emphasis only; raw HTML remains escaped."""
    escaped = html.escape(text)
    return re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", escaped)


def render_table(table):
    esc = html.escape
    labels = table["columns"]
    header = "".join(f'<th scope="col">{esc(label)}</th>' for label in labels)
    rows = "".join("<tr>" + "".join(
        f'<td data-label="{esc(labels[index], quote=True)}">{inline_text(cell)}</td>'
        for index, cell in enumerate(row)) + "</tr>" for row in table["rows"])
    return (f'<figure class="clarity-table"><h2>{esc(table["title"])}</h2>'
            f'<table data-clarity-table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>'
            f'<figcaption>{inline_text(table["caption"])}</figcaption></figure>')


def render_flow(flow):
    esc = html.escape
    steps = "".join(f'<li><strong>{esc(step["title"])}</strong>'
                    f'<span>{esc(step["text"])}</span></li>' for step in flow["steps"])
    return (f'<figure class="clarity-flow"><h2>{esc(flow["title"])}</h2><ol>{steps}</ol>'
            f'<figcaption>{esc(flow["caption"])}</figcaption></figure>')


class Structure(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.offsets = [0]
        for match in re.finditer("\n", source):
            self.offsets.append(match.end())
        self.paragraph = None
        self.paragraphs = []
        self.table = None
        self.tables = []
        self.row = None
        self.cell = None
        self.anchors = []
        self.detail_stack = []
        self.feed(source)

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "details":
            self.detail_stack.append("clarity-source" in values.get("class", "").split())
        if tag == "p" and not any(self.detail_stack):
            self.paragraph = [self.position(), []]
        if tag == "a":
            self.anchors.append((self.position(), values.get("href", "")))
        if tag == "table":
            self.table = {"start": self.position(), "tag": self.get_starttag_text(),
                          "rows": [], "complex": False}
        if self.table and tag == "tr":
            self.row = []
        if self.table and tag in ("td", "th"):
            if values.get("rowspan", "1") != "1" or values.get("colspan", "1") != "1":
                self.table["complex"] = True
            self.cell = {"start": self.position(), "tag": self.get_starttag_text(),
                         "attrs": values, "kind": tag, "text": []}

    def handle_data(self, data):
        if self.paragraph:
            self.paragraph[1].append(data)
        if self.cell:
            self.cell["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "details" and self.detail_stack:
            self.detail_stack.pop()
        if tag == "p" and self.paragraph:
            start, parts = self.paragraph
            self.paragraphs.append((start, self.position() + 4, " ".join("".join(parts).split())))
            self.paragraph = None
        if tag in ("td", "th") and self.cell:
            if self.row is not None:
                self.row.append(self.cell)
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = None
        if tag == "table" and self.table:
            self.tables.append(self.table)
            self.table = None


def enhance_report(source, main_url="", main_title="Main task", conversions=()):
    """Improve only presentation; preserve scripts, controls, data and claims."""
    parser = Structure(source)
    changes = []
    for table in parser.tables:
        rows = table["rows"]
        if table["complex"] or not rows or not rows[0] or not all(c["kind"] == "th" for c in rows[0]):
            continue
        labels = [" ".join("".join(c["text"]).split()) for c in rows[0]]
        if not all(len(row) == len(labels) for row in rows):
            continue
        if "data-clarity-table" not in table["tag"]:
            changes.append((table["start"], table["start"] + len(table["tag"]),
                            table["tag"][:-1] + " data-clarity-table>"))
        for row in rows[1:]:
            for index, cell in enumerate(row):
                if cell["kind"] == "td" and "data-label" not in cell["attrs"]:
                    changes.append((cell["start"], cell["start"] + len(cell["tag"]),
                                    cell["tag"][:-1] + f' data-label="{html.escape(labels[index], quote=True)}">'))
    targets = {" ".join(entry["match"].split()): entry for entry in conversions}
    for start, end, text in parser.paragraphs:
        if text in targets:
            entry = targets[text]
            rendered = render_table(entry["table"]) if "table" in entry else render_flow(entry["flow"])
            # Preserve the exact previous paragraph as an inspectable source.
            rendered += ('<details class="clarity-source"><summary>Exact source wording</summary>'
                         + source[start:end] + "</details>")
            changes.append((start, end, rendered))
    # Paragraph and table edits are disjoint; template JS is never interpreted.
    for start, end, replacement in sorted(changes, reverse=True):
        source = source[:start] + replacement + source[end:]
    style = '<style id="herdr-report-clarity">' + STYLE + "</style>"
    if 'id="herdr-report-clarity"' in source:
        source = re.sub(r'<style id="herdr-report-clarity">.*?</style>',
                        lambda _: style, source, count=1, flags=re.DOTALL)
    else:
        location = source.lower().rfind("</style>")
        if location >= 0:
            source = source[:location + 8] + style + source[location + 8:]
        else:
            source = re.sub(r"(?i)(<body[^>]*>|<main[^>]*>)", lambda m: style + m[0], source, count=1)
    if main_url:
        url = urlsplit(main_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("Return navigation requires a normal HTTP/HTTPS main-task URL.")
        # An existing return link takes precedence, rather than adding duplicates.
        first_heading = re.search(r"(?i)<h1\b", source)
        prefix = first_heading.start() if first_heading else len(source)
        has_return = any(offset < prefix and href == main_url for offset, href in Structure(source).anchors)
        if not has_return:
            nav = (f'<p class="clarity-task-return"><a href="{html.escape(main_url, quote=True)}">'
                   f'← {html.escape(main_title)}</a></p>')
            container = r"(?i)<main\b[^>]*>" if re.search(r"(?i)<main\b", source) else r"(?i)<body\b[^>]*>"
            source, count = re.subn(container, lambda m: m[0] + nav, source, count=1)
            if not count:
                source = source[:prefix] + nav + source[prefix:]
    # Reuse the same portable inspector as task pages; never infer plot values
    # from pixels. Existing plot controls/scripts remain the authority for data.
    if 'id="herdr-figure-inspection"' not in source:
        here = Path(__file__).parent
        candidates = (here / "interactive_curve.js", here / "report_assets/interactive_curve.js")
        asset = next((path for path in candidates if path.exists()), None)
        if asset:
            renderer = "" if "window.InteractiveDiagram" in source else asset.read_text()
            source += ('<script id="herdr-figure-inspection">' + renderer
                       + "\nInteractiveDiagram.mountAll(document);</script>")
    return source


def enhance_from_config(source, output_path):
    """Report builders reuse the sibling configuration after each refresh."""
    path = Path(output_path)
    config_path = path.parent / "report_presentation.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    page = config.get("pages", {}).get(path.name, {})
    return enhance_report(source, page.get("main_url", config.get("main_url", "")),
                          page.get("main_title", config.get("main_title", "Main task")),
                          page.get("conversions", []))
