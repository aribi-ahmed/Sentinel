"""Executive report rendering.

The graph stores each report as Markdown in the audit ledger. This module turns
that stored text into the two things a compliance officer actually needs to hand
on: the Markdown itself, and a typeset PDF.

The PDF is generated from the same stored string rather than re-running the
graph, so a report downloaded from the ledger months later is byte-identical in
substance to the one the officer approved.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ACCENT = colors.HexColor("#C2610A")
INK = colors.HexColor("#141518")
MUTED = colors.HexColor("#5C6672")
RULE = colors.HexColor("#D9DDE3")
WASH = colors.HexColor("#F5F6F8")

PAGE_MARGIN = 18 * mm

BAND_COLOURS = {
    "MINIMAL": colors.HexColor("#1B7A4B"),
    "LOW": colors.HexColor("#2E7D5B"),
    "MODERATE": colors.HexColor("#A8760A"),
    "ELEVATED": colors.HexColor("#C2610A"),
    "SEVERE": colors.HexColor("#B3301C"),
}


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    common = {"fontName": "Helvetica", "textColor": INK, "alignment": TA_LEFT}

    return {
        "body": ParagraphStyle("body", parent=base, fontSize=9.5, leading=14.5, spaceAfter=7, **common),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=21,
                             textColor=INK, spaceBefore=4, spaceAfter=9, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                             textColor=INK, spaceBefore=13, spaceAfter=6, keepWithNext=1),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                             textColor=ACCENT, spaceBefore=11, spaceAfter=5, keepWithNext=1),
        "bullet": ParagraphStyle("bullet", parent=base, fontSize=9.5, leading=14, textColor=INK,
                                 fontName="Helvetica", spaceAfter=3),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=12, textColor=INK, fontName="Helvetica"),
        "cellhead": ParagraphStyle("cellhead", parent=base, fontSize=7.5, leading=11, textColor=MUTED,
                                   fontName="Helvetica-Bold"),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8, leading=11.5, textColor=MUTED, fontName="Helvetica"),
        "metaval": ParagraphStyle("metaval", parent=base, fontSize=9.5, leading=13, textColor=INK,
                                  fontName="Helvetica-Bold"),
    }


# Characters the standard PDF fonts cannot encode, mapped to what they mean.
_TYPOGRAPHY = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "—",
    "\u2018": "'", "\u2019": "'", "\u201a": ",", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u200b": "",
    "\u2022": "-", "\u00ad": "",
}


def _sanitise(text: str) -> str:
    for source, target in _TYPOGRAPHY.items():
        text = text.replace(source, target)
    return text


def _escape(text: str) -> str:
    text = _sanitise(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    """Converts the inline Markdown the reports use into reportlab markup."""
    out = _escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", out)
    out = re.sub(r"`([^`]+)`", r'<font face="Courier" color="#C2610A">\1</font>', out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<link href="\2" color="#C2610A">\1</link>', out)
    return out


def _split_row(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:\-|]+\|?", line.strip())) and "-" in line


def _table(rows: List[List[str]], style: Dict[str, ParagraphStyle], width: float):
    header, *body = rows
    data = [[Paragraph(_inline(cell).upper(), style["cellhead"]) for cell in header]]
    data += [[Paragraph(_inline(cell), style["cell"]) for cell in row] for row in body]

    columns = max(len(row) for row in rows)
    # First column carries the label and gets the slack; the rest share evenly.
    first = width * 0.40 if columns > 2 else width * 0.5
    rest = (width - first) / max(1, columns - 1)

    table = Table(data, colWidths=[first] + [rest] * (columns - 1), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), WASH),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def markdown_to_flowables(markdown: str, style: Dict[str, ParagraphStyle], width: float) -> List[Any]:
    """Renders the Markdown subset the reports use into PDF flowables."""
    flowables: List[Any] = []
    lines = (markdown or "").replace("\r", "").split("\n")

    paragraph: List[str] = []
    bullets: List[str] = []
    table_rows: List[List[str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            flowables.append(Paragraph(_inline(" ".join(paragraph)), style["body"]))
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            flowables.append(ListFlowable(
                [ListItem(Paragraph(_inline(item), style["bullet"]), leftIndent=12) for item in bullets],
                bulletType="bullet",
                bulletColor=ACCENT,
                bulletFontSize=9,
                bulletOffsetY=-1,
                leftIndent=14,
                spaceAfter=8,
            ))
            bullets.clear()

    def flush_table() -> None:
        if table_rows:
            flowables.append(Spacer(1, 3))
            flowables.append(_table(list(table_rows), style, width))
            flowables.append(Spacer(1, 9))
            table_rows.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_bullets()
        flush_table()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_all()
            continue

        if stripped.startswith("|"):
            if _is_separator(stripped):
                continue
            flush_paragraph()
            flush_bullets()
            table_rows.append(_split_row(stripped))
            continue
        flush_table()

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_all()
            level = len(heading.group(1))
            key = "h1" if level <= 2 else ("h2" if level == 3 else "h3")
            flowables.append(Paragraph(_inline(heading.group(2)), style[key]))
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush_all()
            flowables.append(Spacer(1, 4))
            flowables.append(HRFlowable(width="100%", thickness=0.6, color=RULE, spaceAfter=8))
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet:
            flush_paragraph()
            bullets.append(bullet.group(1))
            continue

        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered:
            flush_paragraph()
            bullets.append(numbered.group(1))
            continue

        flush_bullets()
        paragraph.append(stripped)

    flush_all()
    return _bind_headings(flowables, style)


def _bind_headings(flowables: List[Any], style: Dict[str, ParagraphStyle]) -> List[Any]:
    """Keeps each heading on the same page as the block it introduces."""
    heading_styles = {style["h1"], style["h2"], style["h3"]}
    bound: List[Any] = []
    index = 0

    while index < len(flowables):
        current = flowables[index]
        is_heading = isinstance(current, Paragraph) and getattr(current, "style", None) in heading_styles
        if is_heading and index + 1 < len(flowables):
            bound.append(KeepTogether([current, flowables[index + 1]]))
            index += 2
            continue
        bound.append(current)
        index += 1

    return bound


def strip_front_matter(markdown: str) -> str:
    """Drops the report's title block, which the PDF cover page already states.

    Everything up to and including the first horizontal rule is the heading plus
    the entity/verdict/approval lines — exactly what the cover table shows.
    """
    text = (markdown or "").replace("\r", "")
    lines = text.split("\n")
    if not lines or not lines[0].lstrip().startswith("#"):
        return text.strip()

    for index, line in enumerate(lines[:12]):
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", line.strip()):
            return "\n".join(lines[index + 1:]).strip()
    return text.strip()


def _cover(meta: Dict[str, Any], style: Dict[str, ParagraphStyle], width: float) -> List[Any]:
    """The identifying block every page-one of a compliance file needs."""
    band = str(meta.get("risk_level") or "UNKNOWN").upper()
    band_colour = BAND_COLOURS.get(band, ACCENT)
    score = meta.get("score")
    verdict = f"{band}" + (f" · {score}/100" if score is not None else "")

    approved = meta.get("human_approved")
    decision = "Approved" if approved is True else ("Rejected" if approved is False else "Pending")

    cells = [
        ("Target entity", f"{meta.get('subject_name', 'Unknown')} ({meta.get('ticker') or 'N/A'})"),
        ("Risk verdict", verdict),
        ("Officer decision", decision),
        ("Investigation ID", str(meta.get("id", ""))),
        ("Opened", str(meta.get("created_at", ""))[:19].replace("T", " ")),
        ("Document generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
    ]

    data = [[Paragraph(label.upper(), style["meta"]), Paragraph(_escape(str(value)), style["metaval"])]
            for label, value in cells]

    table = Table(data, colWidths=[width * 0.30, width * 0.70], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
        ("TEXTCOLOR", (1, 1), (1, 1), band_colour),
    ]))

    return [
        Paragraph("EXECUTIVE COMPLIANCE REPORT", style["h1"]),
        HRFlowable(width="100%", thickness=1.6, color=ACCENT, spaceBefore=2, spaceAfter=12),
        table,
        Spacer(1, 16),
    ]


def _decorate(canvas, doc, meta: Dict[str, Any]) -> None:
    """Draws the running header and footer on every page."""
    canvas.saveState()
    width, height = A4

    canvas.setFillColor(ACCENT)
    canvas.rect(0, height - 9 * mm, width, 9 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(PAGE_MARGIN, height - 6.2 * mm, "SENTINEL // AI")
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(width - PAGE_MARGIN, height - 6.2 * mm, "CONTROLLED INTELLIGENCE SURFACE")

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(PAGE_MARGIN, 13 * mm, width - PAGE_MARGIN, 13 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(PAGE_MARGIN, 9 * mm, f"{meta.get('subject_name', '')} · investigation {str(meta.get('id', ''))[:8]}")
    canvas.drawRightString(width - PAGE_MARGIN, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(markdown: str, meta: Dict[str, Any]) -> bytes:
    """Typesets a stored Markdown report into a PDF document."""
    buffer = BytesIO()
    style = _styles()

    document = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Sentinel compliance report — {meta.get('subject_name', 'investigation')}",
        author="Sentinel AI",
        subject="Executive compliance report",
    )
    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="body",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    document.addPageTemplates([
        PageTemplate(id="report", frames=[frame], onPage=lambda canvas, doc: _decorate(canvas, doc, meta))
    ])

    story = _cover(meta, style, document.width)
    body = markdown_to_flowables(strip_front_matter(markdown), style, document.width)
    if body:
        story.extend(body)
    else:
        story.append(Paragraph("No report content was stored for this investigation.", style["body"]))

    document.build(story)
    return buffer.getvalue()


def extract_score(markdown: str) -> Any:
    """Reads the composite score back out of a stored report.

    Only `risk_level` is persisted as a column, so for a report pulled from the
    ledger the score is recovered from the document itself rather than lost.
    """
    match = re.search(r"Composite Risk:[\s*`]*([0-9]+(?:\.[0-9]+)?)\s*/\s*100", markdown or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def filename_for(meta: Dict[str, Any], extension: str) -> str:
    """Builds a stable, filesystem-safe download name."""
    subject = re.sub(r"[^A-Za-z0-9]+", "-", str(meta.get("subject_name") or "investigation")).strip("-").lower()
    ticker = re.sub(r"[^A-Za-z0-9]+", "", str(meta.get("ticker") or "")).upper()
    stamp = str(meta.get("created_at") or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [part for part in ("sentinel", subject or "investigation", ticker, stamp) if part]
    return f"{'-'.join(parts)}.{extension}"
