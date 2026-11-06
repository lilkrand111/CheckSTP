"""Regression tests for F22 figure gaps and their fix, F10 page numbers and the F04 indent target."""
import base64
from io import BytesIO

from docx import Document
from docx.oxml import parse_xml
from docx.shared import Mm, Pt

from formal_check import check_document, inspect_document
from formal_fix import fix_document

# 1×1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def base_document():
    document = Document()
    document.add_heading("1 ОСНОВНАЯ ЧАСТЬ", 1)
    document.add_paragraph("Схема показана на рисунке 1, график на рисунке 2, объект на рисунке 3.")
    return document


def blank(document, line_spacing=1.0):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.line_spacing = line_spacing
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    return paragraph


def picture(document):
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(BytesIO(PNG), width=Mm(20))
    paragraph.paragraph_format.space_after = Pt(0)
    return paragraph


def caption(document, text):
    paragraph = document.add_paragraph(text)
    paragraph.paragraph_format.space_before = Pt(0)
    return paragraph


def figure_findings(tmp_path, document):
    path = tmp_path / "figures.docx"
    document.save(path)
    return [f for f in check_document(path)["findings"]
            if f["rule_id"] == "F22" and f["locations"][0].get("property") == "figure_caption_gap"]


def test_figure_gap_requires_single_blank_and_reports_missing_picture(tmp_path):
    document = base_document()
    picture(document)
    blank(document, 1.0)
    caption(document, "Рисунок 1 – Схема")
    picture(document)
    blank(document, 1.5)
    caption(document, "Рисунок 2 – График")
    document.add_paragraph("Текст после графика продолжает описание работы программы.")
    blank(document)
    caption(document, "Рисунок 3 – Пропущенный рисунок")
    findings = figure_findings(tmp_path, document)
    by_caption = {f["locations"][0]["fragment_start"]: f for f in findings}
    assert "Рисунок 1 – Схема" not in by_caption
    gap = by_caption["Рисунок 2 – График"]
    assert gap["status"] == "error"
    assert gap["actual"] == "Промежуток между рисунком и подписью не равен одной строке: межстрочный интервал 1,5."
    missing = by_caption["Рисунок 3 – Пропущенный рисунок"]
    assert (missing["status"], missing["actual"]) == ("review", "Над подписью нет рисунка.")


def test_fix_corrects_blank_between_picture_and_caption_without_touching_picture(tmp_path):
    document = base_document()
    picture(document)
    blank(document, 1.5)
    caption(document, "Рисунок 1 – Схема")
    spaced = picture(document)
    spaced.paragraph_format.space_after = Pt(6)
    blank(document, 1.5)
    caption(document, "Рисунок 2 – График")
    source, fixed = tmp_path / "source.docx", tmp_path / "fixed.docx"
    document.save(source)
    fix_document(source, fixed)
    remaining = {f["locations"][0]["fragment_start"]: f["actual"] for f in check_document(fixed)["findings"]
                 if f["rule_id"] == "F22" and f["locations"][0].get("property") == "figure_caption_gap"}
    assert "Рисунок 1 – Схема" not in remaining
    # A picture paragraph that would itself need editing is left alone, with the finding kept.
    assert remaining["Рисунок 2 – График"] == (
        "Промежуток между рисунком и подписью не равен одной строке: "
        "межстрочный интервал 1,5, у рисунка интервал после 6 пт.")
    before = [p._p.xml for p in Document(source).paragraphs if p._p.xpath(".//w:drawing")]
    after = [p._p.xml for p in Document(fixed).paragraphs if p._p.xpath(".//w:drawing")]
    assert before == after


def test_unrecognized_object_is_not_reported_as_missing_picture(tmp_path):
    document = base_document()
    holder = blank(document)
    holder._p.append(parse_xml(f'<w:r {W}><w:object/></w:r>'))
    caption(document, "Рисунок 1 – Схема Visio")
    assert figure_findings(tmp_path, document) == []


def page_number_findings(tmp_path, document):
    path = tmp_path / "pages.docx"
    document.save(path)
    return [f for f in check_document(path)["findings"] if f["rule_id"] == "F10"]


def test_missing_page_numbers_is_error_only_without_headers_and_footers(tmp_path):
    without = page_number_findings(tmp_path, base_document())
    assert [(f["status"], f["actual"]) for f in without] == [
        ("error", "В документе нет автоматических номеров страниц.")]
    document = base_document()
    document.sections[0].footer.paragraphs[0].text = "Страница"
    assert [f["status"] for f in page_number_findings(tmp_path, document)] == ["review"]


def test_first_line_fix_uses_prevailing_allowed_indent(tmp_path):
    document = base_document()
    for text in ("Первый абзац основной части описывает задачу.",
                 "Второй абзац основной части описывает метод.",
                 "Третий абзац основной части описывает результат."):
        document.add_paragraph(text).paragraph_format.first_line_indent = Mm(12.7)
    document.add_paragraph("Абзац без отступа первой строки нужно исправить.")
    path = tmp_path / "indent.docx"
    document.save(path)
    targets = {d.expected for d in inspect_document(path).diagnostics
               if d.rule_id == "F04" and d.kind == "paragraph_first_line_indent"}
    assert targets == {720}
