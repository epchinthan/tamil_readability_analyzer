"""One-page summary card PDF rendering."""

import io
import json
import os
from pathlib import Path


def generate_summary_card_pdf(row, results, analytics):
    """Build the A5 reading-level summary card and return PDF bytes plus headers."""
    from reportlab.lib.pagesizes import A5
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    book_name = row['book_name']
    best_grade = next(
        (r['grade'] for r in results if r.get('known_pct', r.get('comprehension_pct', 0)) >= 80),
        None,
    )
    best_pct = (
        next((r.get('known_pct', r.get('comprehension_pct', 0)) for r in results if r['grade'] == best_grade), 0)
        if best_grade else 0
    )

    package_root = Path(__file__).resolve().parents[1]
    tamil_font = 'Helvetica'
    tamil_bold = 'Helvetica-Bold'
    for fp in [
        package_root / 'fonts' / 'FreeSerif.ttf',
        Path('/usr/share/fonts/truetype/freefont/FreeSerif.ttf'),
    ]:
        if fp.exists():
            try:
                pdfmetrics.registerFont(TTFont('CardTamil', str(fp)))
                tamil_font = 'CardTamil'
                break
            except Exception:
                pass
    for fp in [
        package_root / 'fonts' / 'FreeSerifBold.ttf',
        Path('/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf'),
    ]:
        if fp.exists():
            try:
                pdfmetrics.registerFont(TTFont('CardTamilBold', str(fp)))
                tamil_bold = 'CardTamilBold'
                break
            except Exception:
                pass

    buf = io.BytesIO()
    width, _height = A5
    doc = SimpleDocTemplate(
        buf, pagesize=A5,
        leftMargin=1.2 * cm, rightMargin=1.2 * cm,
        topMargin=1.2 * cm, bottomMargin=1.2 * cm,
    )

    teal = colors.HexColor('#1D9E75')
    amber = colors.HexColor('#EF9F27')
    red = colors.HexColor('#E24B4A')
    grey = colors.HexColor('#5c574f')
    light_grey = colors.HexColor('#f5f5f3')
    white = colors.white
    black = colors.HexColor('#1a1a18')
    grade_color = teal if best_grade and best_grade <= 5 else amber if best_grade else red

    def style(name, **kw):
        return ParagraphStyle(name, **kw)

    title_style = style(
        'ct', fontName=tamil_bold, fontSize=13, leading=17,
        alignment=TA_CENTER, textColor=black, spaceAfter=2,
    )
    sub_style = style(
        'cs', fontName=tamil_font, fontSize=8.5, leading=12,
        alignment=TA_CENTER, textColor=grey, spaceAfter=8,
    )
    label_style = style(
        'cl', fontName='Helvetica-Bold', fontSize=7,
        textColor=grey, spaceBefore=6, spaceAfter=1,
    )
    note_style = style('cn', fontName=tamil_font, fontSize=7.5, leading=11, textColor=grey, spaceAfter=4)

    story = []
    content_width = width - 2.4 * cm

    story.append(Table(
        [[Paragraph('<b>READING LEVEL CARD</b>', style(
            'bh', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER, textColor=white,
        ))]],
        colWidths=[content_width],
        style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), teal),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]),
    ))
    story.append(Spacer(1, 6))

    display_name = os.path.splitext(book_name)[0].replace('_', ' ')
    story.append(Paragraph(display_name, title_style))
    story.append(Paragraph(
        f"Analyzed on {row['analyzed_at'][:10]} - "
        f"{row['total_words']:,} words - {row.get('unique_stems', 0):,} unique stems",
        sub_style,
    ))
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceAfter=8))

    grade_label = f'Standard {best_grade}' if best_grade else 'Beyond Std 12'
    pct_label = f'{best_pct:.0f}% comprehension' if best_grade else 'Needs advanced vocabulary'
    story.append(Table(
        [[
            Paragraph(f'<b>{grade_label}</b>', style(
                'bg', fontName=tamil_bold, fontSize=22, alignment=TA_CENTER, textColor=white,
            )),
            Paragraph(pct_label, style(
                'bp', fontName=tamil_font, fontSize=9, alignment=TA_CENTER, textColor=white,
            )),
        ]],
        colWidths=[content_width * 0.55, content_width * 0.45],
        style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), grade_color),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]),
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph('COMPREHENSION BY STANDARD', label_style))
    bar_rows = [[
        Paragraph(f'<b>Std {r["grade"]}</b>', style(
            f'g{r["grade"]}', fontName='Helvetica-Bold', fontSize=7, textColor=black,
        )),
        Table(
            [['']],
            colWidths=[(content_width * 0.55) * min(r.get('known_pct', r.get('comprehension_pct', 0)), 100) / 100],
            rowHeights=[10],
            style=TableStyle([
                (
                    'BACKGROUND', (0, 0), (-1, -1),
                    teal if r.get('known_pct', r.get('comprehension_pct', 0)) >= 80
                    else amber if r.get('known_pct', r.get('comprehension_pct', 0)) >= 60
                    else red,
                ),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ]),
        ) if r.get('known_pct', r.get('comprehension_pct', 0)) > 0 else Paragraph('', note_style),
        Paragraph(f'{r.get("known_pct", r.get("comprehension_pct", 0)):.0f}%', style(
            f'p{r["grade"]}', fontName='Helvetica', fontSize=7, textColor=grey,
        )),
    ] for r in results]
    if bar_rows:
        story.append(Table(
            bar_rows,
            colWidths=[1.1 * cm, content_width * 0.56, 0.9 * cm],
            style=TableStyle([
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]),
        ))
    story.append(Spacer(1, 8))

    lex = analytics.get('lexical', {})
    dial = analytics.get('dialogue', {})
    content_flags = analytics.get('content_flags', {})
    readability_score = analytics.get('readability_score', {}) or {}
    sent_data = json.loads(row.get('sentence_json') or '{}')
    tss = sent_data.get('target', {})
    stats = [
        ['Avg sentence', f"{tss.get('avg', 0):.1f} words"],
        ['Vocab diversity', f"TTR {lex.get('ttr', 0):.0f}%"],
        ['Dialogue', f"{dial.get('dialogue_pct', 0):.0f}%"],
        ['Difficulty score', f"{readability_score.get('score', '-')}"],
    ]

    story.append(Paragraph('QUICK STATS', label_style))
    story.append(Table(
        [[
            Paragraph(f'<b>{k}</b>', style('sk', fontName='Helvetica-Bold', fontSize=7, textColor=grey)),
            Paragraph(v, style('sv', fontName='Helvetica', fontSize=8, textColor=black)),
        ] for k, v in stats],
        colWidths=[content_width * 0.45, content_width * 0.55],
        style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), light_grey),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [light_grey, white]),
        ]),
    ))
    story.append(Spacer(1, 8))

    flags = content_flags.get('flags', [])
    if flags:
        story.append(Paragraph('CONTENT FLAGS', label_style))
        flag_items = []
        for flag in flags[:4]:
            severity_color = red if flag['severity'] == 'warning' else amber
            flag_items.append([
                Paragraph(f"- {flag['category']}", style(
                    'fi', fontName=tamil_font, fontSize=7.5, textColor=severity_color,
                )),
                Paragraph(f"Age {flag['min_age']}+", style(
                    'fa', fontName='Helvetica', fontSize=7, textColor=grey,
                )),
            ])
        if flag_items:
            story.append(Table(
                flag_items,
                colWidths=[content_width * 0.75, content_width * 0.25],
                style=TableStyle([
                    ('TOPPADDING', (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ]),
            ))
        story.append(Spacer(1, 4))

    story.append(HRFlowable(
        width='100%', thickness=0.5, color=colors.HexColor('#cccccc'), spaceBefore=4, spaceAfter=4,
    ))
    story.append(Paragraph(
        'Generated by Tamil Book Readability Analyzer (v28) - All analysis local and offline',
        style('ft', fontName='Helvetica', fontSize=6.5, alignment=TA_CENTER, textColor=colors.HexColor('#aaaaaa')),
    ))

    doc.build(story)
    buf.seek(0)
    safe_name = os.path.splitext(book_name)[0][:40]
    return buf.getvalue(), {
        'Content-Type': 'application/pdf',
        'Content-Disposition': f'attachment; filename="card_{safe_name}.pdf"',
    }
