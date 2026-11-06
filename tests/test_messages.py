"""User-facing finding texts: plain wording, no em dashes, no zero-value noise."""
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm

from formal_check import check_document
from formal_check.rules.bibliography import bibliography_issues


def build_document(path):
    document = Document()
    document.sections[0].bottom_margin = Mm(20)
    document.add_heading("ВВЕДЕНИЕ", 1)
    blank = document.add_paragraph()
    blank.paragraph_format.line_spacing = 1.5
    blank.paragraph_format.space_after = 0
    text = document.add_paragraph("Работа описывает численные методы и порядок проверки программы.")
    text.paragraph_format.line_spacing = 1.5
    text.alignment = WD_ALIGN_PARAGRAPH.LEFT
    heading = document.add_heading("1 ОБЗОР МЕТОДОВ", 1)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph("Метод трапеций показан на рисунке 7 и описан в приложении В.")
    document.add_paragraph("Рисунок 1 – Схема алгоритма")
    document.add_heading("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 1)
    document.add_paragraph("[1] Петров, И. И. Численные методы: Курс лекций / И.И. Петров. – Мн.: БГУИР, 2020.")
    document.save(path)


def test_finding_texts_have_no_em_dash_or_zero_noise(tmp_path):
    path = tmp_path / "messages.docx"
    build_document(path)
    findings = check_document(path)["findings"]
    rules = {item["rule_id"] for item in findings}
    assert {"F01", "F03", "F06", "F07", "F33"} <= rules
    for item in findings:
        texts = [item["actual"], item["expected"]] + [
            value for location in item["locations"] for key, value in location.items()
            if key != "fragment_start"]
        for text in texts:
            assert "—" not in text, (item["rule_id"], text)
            assert "распознан" not in text, (item["rule_id"], text)
            assert "секция" not in text.lower(), (item["rule_id"], text)
    gap = next(item for item in findings if item["rule_id"] == "F07" and item["status"] == "error")
    assert gap["actual"] == "Промежуток после заголовка не равен одной строке: межстрочный интервал 1,5."
    assert "«Перед»" in gap["expected"] and "«До»" not in gap["expected"]
    margin = next(item for item in findings if item["rule_id"] == "F01"
                  and item["locations"][0]["property"] == "bottom_margin")
    assert margin["actual"] == "Нижнее поле: 20 мм."
    assert margin["locations"][0]["section"] == "Параметры страницы"


def test_bibliography_issue_wording():
    issues = bibliography_issues("Сидоров А. А. Методы: учебник / А.А. Сидоров. – Мн.: БГУИР, 2020.")
    actual = [a for a, _ in issues]
    expected = [e for _, e in issues]
    assert "После фамилии автора нужна запятая: «Сидоров А. А. Методы:»." in actual
    assert any(a.startswith("Перед двоеточием нужен пробел: «") for a in actual)
    assert any(a.startswith("Между инициалами нужен пробел: «") for a in actual)
    assert any(a.startswith("Название города пишется полностью: «") for a in actual)
    assert all(e.startswith("Исправьте на «") and e.endswith("».") for e in expected)
