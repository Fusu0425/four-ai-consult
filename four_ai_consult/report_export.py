"""Self-contained, mobile-friendly report export without executing model HTML."""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HTML_FILTER = "网页报告（推荐手机分享） (*.html);;Markdown（便于编辑） (*.md)"


def _inline(text: str, source_targets: dict[str, str]) -> str:
    tokens: list[str] = []

    def token(value: str) -> str:
        index = len(tokens)
        tokens.append(value)
        return f"\x00{index}\x00"

    def code(match: re.Match[str]) -> str:
        return token(f"<code>{html.escape(match.group(1))}</code>")

    text = re.sub(r"`([^`\n]+)`", code, text)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), html.unescape(match.group(2).strip())
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return token(html.escape(label))
        return token(
            f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{html.escape(label)}</a>"
        )

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", link, text)
    rendered = html.escape(text)
    rendered = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", rendered)

    def citation(match: re.Match[str]) -> str:
        source_id = match.group(1)
        target = source_targets.get(source_id, "")
        badge = f'<span class="citation">{source_id}</span>'
        return f'<a class="citation-link" href="#{target}">{badge}</a>' if target else badge

    rendered = re.sub(r"\[(S\d+-\d+)\]", citation, rendered)
    for index, value in enumerate(tokens):
        rendered = rendered.replace(html.escape(f"\x00{index}\x00"), value)
    return rendered


def _split_table_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", line)
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _is_table_rule(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def render_markdown(markdown: str, source_targets: dict[str, str] | None = None) -> tuple[str, list[tuple[int, str, str]]]:
    """Render the report Markdown subset, escaping every raw HTML fragment."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    heading_targets = {}
    heading_count = 0
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            heading_count += 1
            label = re.sub(r"[`*_]", "", match.group(2)).strip()
            heading_targets.setdefault(label, f"section-{heading_count}")
    source_targets = {
        source_id: heading_targets.get(target, target if target.startswith("section-") else "")
        for source_id, target in (source_targets or {}).items()
    }
    output: list[str] = []
    headings: list[tuple[int, str, str]] = []
    paragraph: list[str] = []
    list_kind = ""
    fence = ""
    code_lines: list[str] = []
    section_open = False

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = ""

    def flush_paragraph() -> None:
        if paragraph:
            output.append("<p>" + "<br>".join(_inline(line, source_targets) for line in paragraph) + "</p>")
            paragraph.clear()

    def begin_section() -> None:
        nonlocal section_open
        if not section_open:
            output.append('<section class="card">')
            section_open = True

    index = 0
    while index < len(lines):
        line = lines[index]
        fence_match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                fence, code_lines = "", []
            else:
                code_lines.append(line)
            index += 1
            continue
        if fence_match:
            flush_paragraph()
            close_list()
            begin_section()
            fence = fence_match.group(1)
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            label = re.sub(r"[`*_]", "", heading.group(2)).strip()
            anchor = f"section-{len(headings) + 1}"
            headings.append((level, label, anchor))
            if level == 1 and section_open:
                output.append("</section>")
                section_open = False
            begin_section()
            output.append(f'<h{level} id="{anchor}">{_inline(heading.group(2), source_targets)}</h{level}>')
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _is_table_rule(lines[index + 1]):
            flush_paragraph()
            close_list()
            begin_section()
            headers = _split_table_row(line)
            output.append('<div class="table-wrap"><table><thead><tr>')
            output.extend(f"<th>{_inline(cell, source_targets)}</th>" for cell in headers)
            output.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = _split_table_row(lines[index])
                cells.extend([""] * (len(headers) - len(cells)))
                output.append("<tr>" + "".join(f"<td>{_inline(cell, source_targets)}</td>" for cell in cells[: len(headers)]) + "</tr>")
                index += 1
            output.append("</tbody></table></div>")
            continue
        item = re.match(r"^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$", line)
        if item:
            flush_paragraph()
            begin_section()
            kind = "ol" if item.group(2) else "ul"
            if list_kind != kind:
                close_list()
                output.append(f"<{kind}>")
                list_kind = kind
            output.append(f"<li>{_inline(item.group(3), source_targets)}</li>")
            index += 1
            continue
        if re.match(r"^\s*(?:---+|___+|\*\*\*+)\s*$", line):
            flush_paragraph()
            close_list()
            begin_section()
            output.append("<hr>")
            index += 1
            continue
        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            flush_paragraph()
            close_list()
            begin_section()
            output.append(f"<blockquote>{_inline(quote.group(1), source_targets)}</blockquote>")
            index += 1
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
        else:
            begin_section()
            paragraph.append(line.strip())
        index += 1

    if fence:
        output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph()
    close_list()
    if section_open:
        output.append("</section>")
    return "\n".join(output), headings


def build_report_html(
    markdown: str,
    *,
    question: str = "",
    source_targets: dict[str, str] | None = None,
    exported_at: datetime | None = None,
) -> str:
    body, headings = render_markdown(markdown, source_targets)
    exported_at = exported_at or datetime.now()
    title = "完整对比报告"
    question_label = question.strip() or "完整观点、对比与原文"
    toc = "".join(
        f'<li class="level-{min(level, 3)}"><a href="#{anchor}">{html.escape(label)}</a></li>'
        for level, label, anchor in headings
        if level <= 3
    )
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light">
<title>{html.escape(title + " · " + question_label)}</title>
<style>
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#f3efe8;color:#302a24;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
line-height:1.72;-webkit-font-smoothing:antialiased}}.hero{{color:#fff;background:linear-gradient(135deg,#663e31,#9c563c 58%,#c57c58);
padding:34px max(20px,env(safe-area-inset-left)) 30px}}.wrap{{width:min(860px,calc(100% - 28px));margin:auto}}
.kicker{{font-size:12px;letter-spacing:2px;opacity:.78}}.hero h1{{margin:7px 0 6px;font-size:clamp(25px,5vw,38px);line-height:1.25}}
.question{{max-width:760px;font-size:15px;opacity:.94;white-space:pre-wrap}}.meta{{margin-top:12px;font-size:12px;opacity:.72}}
main{{padding:18px 0 6px}}.toc,.card,.notice{{background:#fff;border:1px solid #e8ded2;border-radius:16px;
box-shadow:0 5px 22px rgba(86,58,45,.07)}}.toc{{padding:16px 18px;margin-bottom:18px}}.toc h2{{margin:0 0 8px;font-size:14px;color:#9c563c}}
.toc ol{{list-style:none;margin:0;padding:0}}.toc li{{margin:5px 0}}.toc .level-2{{padding-left:16px}}.toc .level-3{{padding-left:32px;font-size:14px}}
a{{color:#9c563c;text-decoration-thickness:1px;text-underline-offset:3px}}.toc a{{color:#56473e;text-decoration:none}}
.card{{padding:9px 22px 23px;margin-bottom:18px;overflow:hidden}}h1{{font-size:24px;margin:19px 0 10px}}h2{{font-size:20px;color:#7c4533;
margin:28px 0 10px;padding-top:7px;border-top:1px solid #eee3d8}}h3{{font-size:17px;color:#58453b;margin:22px 0 7px}}h4,h5,h6{{color:#66544a;margin:18px 0 6px}}
p{{margin:10px 0}}strong{{color:#512f25}}ul,ol{{padding-left:23px}}li{{margin:5px 0}}blockquote{{margin:12px 0;padding:10px 14px;
border-left:4px solid #c57c58;background:#fff6ed;border-radius:0 9px 9px 0;color:#654a3b}}
code{{font-family:"Cascadia Mono",Consolas,monospace;background:#f0ebe5;padding:2px 5px;border-radius:5px;font-size:.9em}}
pre{{background:#292421;color:#f5eee7;padding:15px;border-radius:12px;overflow:auto;line-height:1.55}}pre code{{background:transparent;padding:0;color:inherit}}
.table-wrap{{overflow-x:auto;margin:13px -4px}}table{{width:100%;border-collapse:collapse;font-size:14px;min-width:460px}}th,td{{border:1px solid #e5d9cd;
padding:8px 10px;text-align:left;vertical-align:top}}th{{background:#f8f1ea;color:#704432}}hr{{border:0;border-top:1px solid #eadfd4;margin:23px 0}}
.citation-link{{text-decoration:none}}.citation{{display:inline-block;background:#f3e1d6;color:#78432f;border-radius:999px;padding:0 6px;font-size:12px;font-weight:650}}
.notice{{padding:12px 15px;margin:2px 0 18px;background:#fff9eb;border-color:#eeddb8;color:#74581d;font-size:13px}}
footer{{text-align:center;color:#998b82;font-size:12px;padding:8px 0 28px}}
@media(max-width:560px){{.wrap{{width:min(100% - 20px,860px)}}.hero{{padding-top:26px;padding-bottom:24px}}.card{{padding:6px 15px 18px}}
h1{{font-size:21px}}h2{{font-size:18px}}body{{font-size:15px}}.toc .level-3{{display:none}}}}
@media print{{body{{background:#fff}}.hero{{background:#fff;color:#302a24;padding:10mm 0}}.wrap{{width:100%;max-width:none}}.toc{{display:none}}.card,.notice{{box-shadow:none;break-inside:auto}}
a{{color:inherit;text-decoration:none}}}}
</style>
</head>
<body>
<header class="hero"><div class="wrap"><div class="kicker">FOUR AI CONSULT · 可分享报告</div><h1>{title}</h1>
<div class="question">{html.escape(question_label)}</div><div class="meta">导出时间 {exported_at:%Y-%m-%d %H:%M}</div></div></header>
<div class="wrap"><main><nav class="toc" aria-label="报告目录"><h2>快速目录</h2><ol>{toc}</ol></nav>{body}</main>
<aside class="notice">本报告汇总模型生成内容。关键事实、数字和高风险建议请回到原文并进一步核验。</aside></div>
<footer>四模型会诊 · 本地导出 · 不依赖网络样式</footer>
</body></html>'''


def source_targets_for_record(record) -> dict[str, str]:
    """Map citation ids to the corresponding exported original-answer heading."""
    return {source.id: f"原文 · {source.site_name}" for source in record.sources}


def normalize_export_path(path: str, selected_filter: str) -> tuple[Path, str]:
    target = Path(path)
    suffix = target.suffix.lower()
    if selected_filter:
        kind = "md" if "Markdown" in selected_filter else "html"
    else:
        kind = "md" if suffix in {".md", ".markdown"} else "html"
    wanted = ".md" if kind == "md" else ".html"
    if (kind == "md" and suffix not in {".md", ".markdown"}) or (kind == "html" and suffix not in {".html", ".htm"}):
        target = target.with_suffix(wanted)
    return target, kind


def write_report_export(
    path: str,
    selected_filter: str,
    markdown: str,
    *,
    question: str = "",
    source_targets: dict[str, str] | None = None,
) -> Path:
    target, kind = normalize_export_path(path, selected_filter)
    payload = build_report_html(markdown, question=question, source_targets=source_targets) if kind == "html" else markdown
    target.write_text(payload, encoding="utf-8")
    return target
