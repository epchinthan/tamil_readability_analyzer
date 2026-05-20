"""PDF report rendering.

The shaped renderer uses MuPDF's HTML engine because ReportLab does not shape
Tamil combining marks correctly in normal Platypus text tables.
"""

import html
import os
import tempfile
import uuid


def generate_shaped_tamil_report_pdf(row, results, distribution, proper_nouns, sent_data, meaning=None):
    """Render the PDF report through MuPDF HTML so Tamil glyphs are shaped correctly."""
    import fitz

    book_name = row['book_name']
    tss = sent_data.get('target', {})
    total_stems = row['unique_stems'] or row['unique_words']
    first_readable = next((r for r in results if r['comprehension_pct'] >= 80), None)
    generated = row["analyzed_at"][:19]

    def esc(value):
        return html.escape(str(value if value is not None else ''))

    def word_html(value):
        text = str(value if value is not None else '')
        clusters = []
        for ch in text:
            if clusters and ('\u0BBE' <= ch <= '\u0BCD' or ch in {'\u0B82', '\u0B83', '\u0BD7'}):
                clusters[-1] += ch
            else:
                clusters.append(ch)
        chunks = [''.join(clusters[i:i+6]) for i in range(0, len(clusters), 6)]
        return '<br>'.join(html.escape(chunk) for chunk in chunks)

    def show_pct(value):
        return esc(value if value not in (None, '') else '-')

    def word_grid(words, cols=4, limit=None):
        shown = list(words or [])
        extra = 0
        if limit and len(shown) > limit:
            extra = len(shown) - limit
            shown = shown[:limit]
        cells = []
        for i, word in enumerate(shown):
            if i % cols == 0:
                cells.append('<tr>')
            cells.append(f'<td class="word">{word_html(word)}</td>')
            if i % cols == cols - 1:
                cells.append('</tr>')
        if shown and len(shown) % cols:
            for _ in range(cols - (len(shown) % cols)):
                cells.append('<td class="word empty"></td>')
            cells.append('</tr>')
        if not shown:
            return ''
        note = f'<p class="note">... and {extra:,} more (see Excel export for full list)</p>' if extra else ''
        return f'<table class="word-grid">{"".join(cells)}</table>{note}'

    overview_rows = [
        ('Total words in book', f"{row['total_words']:,}"),
        ('Unique Tamil stems (after morphological analysis)', f"{total_stems:,}"),
        ('Proper nouns (counted as known)', str(len(proper_nouns))),
        ('First readable from', f"Standard {first_readable['grade']}" if first_readable else 'Beyond Std 12'),
        ('Target book - average words per sentence', tss.get('avg', '-')),
        ('Target book - max words in one sentence', tss.get('max', '-')),
        ('Target book - total sentences analysed', f"{tss.get('total_sentences', 0):,}"),
    ]
    overview_html = ''.join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k, v in overview_rows)

    readability_rows = []
    for r in results:
        known_pct = r['known_pct']
        verdict = 'Easy' if known_pct >= 90 else 'Readable' if known_pct >= 80 else 'Challenging' if known_pct >= 60 else 'Very Hard'
        verdict_class = 'good' if r['comprehension_pct'] >= 80 else 'mid' if r['comprehension_pct'] >= 60 else 'hard'
        readability_rows.append(
            '<tr>'
            f'<td>Std 1-{esc(r["grade"])}</td>'
            f'<td>{int(r["total_unique_book_words"]):,}</td>'
            f'<td>{int(r["known_words"]):,}</td>'
            f'<td class="{verdict_class}">{show_pct(r["known_pct"])}%</td>'
            f'<td>{int(r["new_words"]):,}</td>'
            f'<td>{show_pct(r["new_pct"])}%</td>'
            f'<td class="{verdict_class}">{verdict}</td>'
            f'<td>{esc(r.get("grade_sent_max", "-"))}</td>'
            f'<td>{esc(r.get("target_sentences_over_max", "-"))}</td>'
            '</tr>'
        )

    distribution_rows = []
    for drow in distribution:
        label = drow.get('label') or f"Std {drow.get('grade')}"
        distribution_rows.append(
            '<tr>'
            f'<td>{esc(label)}</td>'
            f'<td>{int(drow.get("word_count", 0)):,}</td>'
            f'<td>{show_pct(drow.get("word_pct", 0))}%</td>'
            '</tr>'
        )

    sentence_rows = []
    target_avg = tss.get('avg', 0)
    for r in results:
        gmax = r.get('grade_sent_max', 0)
        over_pct = r.get('target_pct_over_max', 0)
        difficulty = ''
        if gmax > 0:
            difficulty = 'High' if over_pct > 50 else 'Medium' if over_pct > 20 else 'Low'
        sentence_rows.append(
            '<tr>'
            f'<td>Std {esc(r["grade"])}</td>'
            f'<td>{esc(gmax if gmax else "-")}</td>'
            f'<td>{esc(r.get("grade_sent_avg", "-"))}</td>'
            f'<td>{esc(target_avg)}</td>'
            f'<td>{esc(r.get("target_sentences_over_max", 0))}</td>'
            f'<td>{show_pct(over_pct)}% {esc(f"[{difficulty}]" if difficulty else "")}</td>'
            '</tr>'
        )

    section3 = []
    for r in results:
        nw = r.get('new_at_grade_list', r.get('new_word_list', []))
        if not nw:
            continue
        pct_value = r.get('new_at_grade_pct', r.get('new_words_pct', 0))
        section3.append(
            f'<h2>Standard 1-{esc(r["grade"])} - {len(nw):,} words introduced at Std {esc(r["grade"])} '
            f'({show_pct(pct_value)}% of book vocabulary)</h2>{word_grid(nw)}'
        )

    section4 = []
    for r in results:
        uw = r.get('new_word_list', r.get('unknown_word_list', []))
        heading = (
            f'<h2>Standard 1-{esc(r["grade"])} - '
            f'{int(r.get("new_words", r.get("unknown_words", 0))):,} new words for student '
            f'({show_pct(r.get("new_pct", round(100-r.get("known_pct", r.get("comprehension_pct", 100)), 1)))}% of book vocabulary)</h2>'
        )
        body = word_grid(uw, limit=300) if uw else '<p class="note">No new words - this student already knows every word in this book.</p>'
        section4.append(heading + body)

    proper_nouns_html = ''
    if proper_nouns:
        proper_nouns_html = (
            '<section class="page-break"><h1>Section 5 - Proper Nouns (Counted as Known)</h1>'
            '<p class="note">These words were identified as names, places, deities, or foreign proper nouns. '
            'They are counted as known at all grade levels since students can learn them from context.</p>'
            f'{word_grid(proper_nouns, cols=5)}</section>'
        )

    doc_html = f"""<!doctype html>
<html lang="ta">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: "Noto Sans Tamil", "Noto Serif Tamil", "Lohit Tamil", sans-serif; color:#1a1814; font-size:10pt; line-height:1.55; }}
  h1 {{ color:#1D4E89; font-size:17pt; line-height:1.25; margin:18pt 0 6pt; page-break-after:avoid; }}
  h2 {{ color:#2e6da4; font-size:13pt; line-height:1.35; margin:14pt 0 5pt; page-break-after:avoid; }}
  .cover {{ text-align:center; margin-top:34pt; border-bottom:1px solid #1D4E89; padding-bottom:18pt; }}
  .cover h1 {{ font-size:21pt; margin-bottom:4pt; }}
  .subtitle {{ color:#5c574f; font-size:11pt; }}
  .note {{ color:#666; font-size:8.5pt; margin:2pt 0 8pt; }}
  table {{ width:100%; border-collapse:collapse; margin:5pt 0 12pt; page-break-inside:auto; }}
  th, td {{ border:0.35pt solid #bbbbbb; padding:4pt 5pt; vertical-align:top; }}
  th {{ background:#1D4E89; color:white; font-weight:bold; }}
  .overview th {{ width:65%; background:#D6EAF8; color:#1a1814; text-align:left; }}
  .overview td {{ width:35%; }}
  tr:nth-child(even) td, .overview tr:nth-child(even) th {{ background:#F0EDE8; }}
  .numeric td:not(:first-child), .numeric th:not(:first-child) {{ text-align:center; }}
  .good {{ background:#D4EDDA; }}
  .mid {{ background:#FFF3CD; }}
  .hard {{ background:#F8D7DA; }}
  .word-grid {{ table-layout:fixed; font-size:9pt; line-height:1.45; }}
  .word-grid td {{ min-height:18pt; overflow-wrap:anywhere; word-break:break-word; background:white !important; }}
  .word-grid tr:nth-child(even) td {{ background:#F8D7DA !important; }}
  .word-grid .empty {{ background:white !important; }}
  .page-break {{ page-break-before:always; }}
</style>
</head>
<body>
  <section class="cover">
    <h1>Tamil Book Readability Report</h1>
    <div class="subtitle">{esc(book_name)}</div>
    <div class="subtitle">Generated: {esc(generated)}</div>
  </section>

  <h1>Overview</h1>
  <table class="overview">{overview_html}</table>

  <h1>Section 1 - Readability by Standard</h1>
  <p class="note">Each row is cumulative. Known words are words from the book the student already knows; new words are words in the book the student has not yet learned.</p>
  <table class="numeric">
    <tr><th>Standard</th><th>Total unique<br>words</th><th>Known words</th><th>% known</th><th>New words</th><th>% new</th><th>Verdict</th><th>Grade max<br>sentence</th><th>Sentences<br>over max</th></tr>
    {''.join(readability_rows)}
  </table>

  <h1>Word Distribution by Class</h1>
  <p class="note">Each unique book word is assigned to the first class where it appears in the loaded textbook database. The final row lists words not found in any class.</p>
  <table class="numeric"><tr><th>Class</th><th>Words</th><th>% of book vocabulary</th></tr>{''.join(distribution_rows)}</table>

  <section class="page-break">
    <h1>Section 2 - Sentence Complexity Analysis</h1>
    <p class="note">Compares sentence length distribution of the target book against each school standard's textbook.</p>
    <table class="numeric"><tr><th>Standard</th><th>Grade book<br>max sentence</th><th>Grade book<br>avg sentence</th><th>Target book<br>avg sentence</th><th>Sentences in<br>target &gt; grade max</th><th>% sentences<br>over grade max</th></tr>{''.join(sentence_rows)}</table>
    <p class="note">Target book sentence stats - Average: {esc(tss.get("avg","-"))} words/sentence | Max: {esc(tss.get("max","-"))} words | Median: {esc(tss.get("median","-"))} words | Total sentences: {int(tss.get("total_sentences",0)):,}</p>
  </section>

  <section class="page-break">
    <h1>Section 3 - New Words Introduced per Standard</h1>
    <p class="note">For each standard, the words in this book that become newly known after completing that standard.</p>
    {''.join(section3)}
  </section>

  <section class="page-break">
    <h1>Section 4 - New Words for Student per Standard</h1>
    <p class="note">Words in this book that are new to a student at each level - not yet encountered in their studies up to that standard.</p>
    {''.join(section4)}
  </section>

  {proper_nouns_html}
</body>
</html>"""

    page = fitz.Rect(0, 0, 595, 842)
    body = fitz.Rect(50, 50, 545, 792)
    out_path = os.path.join(tempfile.gettempdir(), f'tamil_report_{uuid.uuid4().hex}.pdf')
    writer = fitz.DocumentWriter(out_path)
    try:
        story = fitz.Story(doc_html)

        def rectfn(_rect_num, _filled):
            return page, body, fitz.Matrix(1, 1)

        story.write(writer, rectfn)
        writer.close()
        with open(out_path, 'rb') as fh:
            return fh.read()
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            os.remove(out_path)
        except OSError:
            pass
