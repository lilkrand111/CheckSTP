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


def test_numbering_order_status_and_blank_texts(tmp_path):
    document = Document()
    document.add_heading("1 ОСНОВНАЯ ЧАСТЬ", 1)
    document.add_paragraph()
    document.add_paragraph()
    document.add_paragraph("Сначала упомянут источник [2], затем источник [1]. "
                           "Схемы показаны на рисунках 1.1, 1.3 и 1.3.")
    for text in ("Рисунок 1.1 – Первая схема", "Рисунок 1.3 – Вторая схема", "Рисунок 1.3 – Третья схема"):
        document.add_paragraph(text)
    document.add_heading("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 1)
    document.add_paragraph("[1] Петров, И. И. Численные методы / И. И. Петров. – Минск : БГУИР, 2020. – 10 с.")
    document.add_paragraph("[2] Сидоров, А. А. Алгоритмы / А. А. Сидоров. – Минск : БГУИР, 2021. – 20 с.")
    document.add_heading("ПРИЛОЖЕНИЕ А", 1)
    document.add_paragraph("Программный код")
    path = tmp_path / "numbering.docx"
    document.save(path)
    texts = {(f["rule_id"], f["actual"]): f["expected"] for f in check_document(path)["findings"]}
    assert texts[("F22", "После рисунка 1.1 идёт рисунок 1.3.")] == "Исправьте номер на 1.2."
    assert texts[("F22", "Номер рисунка 1.3 встречается дважды.")] == "Перенумеруйте рисунки по порядку."
    assert texts[("F32", "Источник [2] упоминается в тексте раньше источника [1].")].endswith(
        "Поставьте [2] в списке перед [1] и исправьте номера ссылок.")
    assert texts[("F30", "Под обозначением «ПРИЛОЖЕНИЕ А» нет строки статуса.")].startswith(
        "Добавьте под обозначением строку «(обязательное)»")
    assert texts[("F07", "После заголовка 2 пустых абзаца.")] == "Оставьте один пустой абзац."



def test_caption_hyphen_is_accepted(tmp_path):
    """Customer decision 06.11.2026: «Рисунок 1 - Наименование» is not a finding."""
    document = Document()
    document.add_heading("1 ОСНОВНАЯ ЧАСТЬ", 1)
    document.add_paragraph("Схема показана на рисунке 1.")
    document.add_paragraph("Рисунок 1 - Схема алгоритма")
    path = tmp_path / "hyphen.docx"
    document.save(path)
    captions = [f for f in check_document(path)["findings"] if f["rule_id"] == "F22"
                and f["locations"][0].get("property") != "figure_caption_gap"]
    assert captions == []
