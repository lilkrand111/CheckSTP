import re

from . import rule
from .common import (FIRST_LINE_ACTION, LOG, cap_errors, check_indent, indent, ordinary, page_section,
                     size_range, visible_runs)
from .headings import check_alignment, check_zero_indent, title_runs, titles, unnumbered
from ..continuations import formula_continuations
from ..constraints import PAGE_SETUP, SECTION_INDENT_MM, SINGLE_LINE, mm_to_twips
from ..formatting import section_indentation_values
from ..messages import number as format_number, spacing
from ..model import Diagnostic
from .. import subsection_layout
from ..xmlutil import attr, child, integer


@rule("F01")
def page_setup(model, report):
    for i, section in enumerate(model.document.sections, 1):
        for name, tag, attribute, mm, minimum, label, target in PAGE_SETUP:
            node = child(section._sectPr, tag)
            twips = integer(attr(node, attribute))
            if twips is None:
                LOG.warning("F01: секция %s, %s не определено в DOCX.", i, name)
                continue
            expected = mm * 1440 / 25.4
            invalid = twips < expected - 1 if minimum else abs(twips - expected) > 1
            if invalid:
                report.add({"section": page_section(model, i), "property": name},
                           f"{label}: {format_number(twips * 25.4 / 1440)} мм.",
                           f"Установите {target} {'не менее ' if minimum else ''}{mm} мм.",
                           diagnostic=Diagnostic("section_property", section._sectPr, name,
                                                 twips, mm_to_twips(mm)))


@rule("F02")
def fonts(model, report):
    for p in ordinary(model):
        fonts, sizes, unknown = set(), set(), False
        for r in visible_runs(p):
            for char in r.text:
                if not char.isalnum():
                    continue
                script = "ascii" if ord(char) < 128 else "hAnsi"
                if 0x2E80 <= ord(char) <= 0x9FFF or 0xAC00 <= ord(char) <= 0xD7AF:
                    script = "eastAsia"
                if r.props.get("cs") or r.props.get("rtl") or 0x0590 <= ord(char) <= 0x08FF:
                    script = "cs"
                font = r.props.get("font_" + script)
                if font:
                    fonts.add(font)
                else:
                    unknown = True
            if "size" in r.props:
                sizes.add(r.props["size"])
            else:
                unknown = True
        bad_fonts = sorted(f for f in fonts if f.casefold() != "times new roman")
        bad_sizes = sorted(s for s in sizes if not 13 <= s <= 14)
        if bad_fonts or bad_sizes:
            facts = []
            if bad_fonts:
                facts.append("Шрифт обычного текста: " + ", ".join(bad_fonts))
            if bad_sizes:
                facts.append("Размер шрифта обычного текста: " + "; ".join(format_number(n) for n in bad_sizes) + " пт")
            actions = []
            if bad_fonts:
                actions.append("Выберите шрифт Times New Roman.")
            if bad_sizes:
                actions.append("Установите размер шрифта от 13 до 14 пт.")
            report.add(p, ". ".join(facts) + ".", " ".join(actions))
        if unknown:
            model.resolver.warn("F02: часть шрифтов/размеров не определена; эти свойства не проверены.")
    for p in model.headings:
        if p.heading_number and p.heading_level not in (1, 2):
            if p.num_id:
                bold = p.num_props.get("bold") if p.num_known else None
            else:
                number_len = len(re.match(r"^\s*\d+(?:\.\d+)*", p.text)[0]) if re.match(r"^\s*\d+", p.text) else 0
                selected, offset = [], 0
                for r in p.runs:
                    if offset < number_len and r.text.strip():
                        selected.append(r)
                    offset += len(r.text)
                bold = all(r.props.get("bold") for r in selected) if selected else None
            if bold is False:
                report.add(p, "Номер раздела или пункта не выделен полужирным.",
                           "Выделите номер полужирным начертанием.")
    for p in titles(model):
        runs = title_runs(p)
        number_not_bold = p.num_id and p.num_known and not p.num_props.get("bold")
        if any(not r.props.get("bold") for r in runs) or number_not_bold:
            report.add(p, "Заголовок или его номер не полностью выделены полужирным.",
                       "Выделите весь заголовок и его номер полужирным начертанием.", prop="heading_bold")
        if p.kind == "heading":
            bounds = (14, 16) if p.heading_level == 1 else (13, 14)
            sizes = size_range(p)
            if any(not bounds[0] <= s <= bounds[1] for s in sizes):
                report.add(p, "Размер шрифта заголовка: " + "; ".join(format_number(s) for s in sizes) + " пт.",
                           f"Рекомендуется установить размер шрифта заголовка от {bounds[0]} до {bounds[1]} пт.",
                           "recommendation")


@rule("F03")
def paragraph_spacing(model, report):
    for p in ordinary(model):
        value, mode = p.props["line"], p.props["lineRule"]
        if (value, mode) != SINGLE_LINE:
            report.add(p, spacing(value, mode),
                       "В окне «Абзац» выберите междустрочный интервал «Одинарный».",
                       prop="line_spacing",
                       diagnostic=Diagnostic("paragraph_line_spacing", p, "line_spacing",
                                             (value, mode), SINGLE_LINE))


@rule("F04")
def first_line(model, report):
    continuations = formula_continuations(model)
    for p in ordinary(model):
        if indent(p) == 0:
            continuation = continuations.get(p.index)
            if continuation == "continuation":
                continue
            if continuation == "review":
                report.add(p, "Абзац после формулы начинается без отступа первой строки.",
                           "Если абзац продолжает предложение с формулой, отступ не нужен. "
                           "Если это новый абзац, " + FIRST_LINE_ACTION[0].lower() + FIRST_LINE_ACTION[1:],
                           "review", prop="first_line_indent")
                continue
        check_indent(model, p, report)


@rule("F05")
def heading_numbers(model, report):
    last = {}
    seen = {}
    for p in model.headings:
        number = p.heading_number
        if not number:
            continue
        if p.heading_level == 1:
            check_section_indentation(p, report, model.resolver.heading_paragraph(p.xml))
        elif p.heading_level == 2:
            check_subsection_layout(model, p, report)
        if p.num_id and not p.num_known:
            continue
        scope = (p.appendix, number[:-1])
        expected = last.get(scope, 0) + 1
        if number[-1] != expected:
            report.add(p, f"Номер раздела или пункта {'.'.join(map(str, number))} нарушает последовательность.",
                       f"Исправьте номер: следующим по порядку должен быть {'.'.join(map(str, number[:-1] + (expected,)))}.")
        if len(number) > 1 and (p.appendix, number[:-1]) not in seen:
            report.add(p, f"Для пункта {'.'.join(map(str, number))} не найден вышестоящий раздел "
                       f"{'.'.join(map(str, number[:-1]))}.",
                       "Проверьте, есть ли вышестоящий раздел и правильно ли указан номер пункта.", "review")
        last[scope] = number[-1]
        seen[(p.appendix, number)] = p
        if len(number) == 1 and re.match(r"^\s*\d+\.\s", p.numbered_text):
            report.add(p, "После номера раздела стоит точка.", "Уберите точку после номера раздела.")
        if p.heading_level not in (1, 2):
            check_indent(model, p, report)


def check_subsection_layout(model, p, report):
    desired = subsection_layout.target(model, p)
    if desired is None:
        report.add(p, "Отступы и знак после номера подраздела заданы нестандартно.",
                   "Номер должен начинаться с 1,25 см, а вторая строка заголовка под началом его текста. "
                   "Между номером и названием поставьте табуляцию. Не выравнивайте строки пробелами.",
                   "review", prop="subsection_indentation")
        return
    actual = subsection_layout.current_value(model, p)
    if actual == desired:
        return
    hanging_cm = desired[0][2] * 2.54 / 1440
    column_cm = (desired[0][0] + desired[0][2]) * 2.54 / 1440
    column = format_number(column_cm)
    expected = (f"Номер начинается с 1,25 см, текст заголовка с {column} см. "
                f"Установите выступ {format_number(hanging_cm)} см и позицию табуляции {column} см, "
                "справа 0 см. Между номером и названием поставьте табуляцию вместо пробела.")
    report.add(p, "Отступы заголовка подраздела не настроены: длинный заголовок перенесётся неровно.",
               expected, prop="subsection_indentation",
               diagnostic=Diagnostic("subsection_layout", p, "subsection_indentation", actual, desired))


def check_section_indentation(p, report, props):
    """Customer-approved Word settings for numbered section headings."""
    expected = "В окне «Абзац» установите отступ слева 1,25 см, справа 0 см и выступ 0,5 см."
    values = section_indentation_values(props)
    if values is None:
        report.add(p, "Отступы заголовка раздела заданы в знаках, а не в сантиметрах.",
                   "В окне «Абзац» задайте отступы в сантиметрах: слева 1,25 см, справа 0 см, выступ 0,5 см.",
                   "review", prop="heading_indentation")
        return
    # Translate the OOXML coordinates to the supplied Word for Mac
    # dialog settings. This is a representation conversion, not another norm.
    checked_left, right, hanging, _ = values
    left_mm, right_mm, hanging_mm = SECTION_INDENT_MM
    if (abs(checked_left - left_mm * 1440 / 25.4) <= 1 and
            abs(right - right_mm * 1440 / 25.4) <= 1 and
            abs(hanging - hanging_mm * 1440 / 25.4) <= 1):
        return

    def cm(value, target=None):
        amount = value * 2.54 / 1440
        text = format_number(amount)
        # Do not display identical actual/target values for a small deviation
        # outside the Word rounding tolerance.
        if (target is not None and abs(value - target * 1440 / 2.54) > 1 and
                text == format_number(target)):
            text = format_number(amount, precision=3)
        return text

    if hanging:
        special = f"выступ {cm(hanging, 0.5)} см"
    elif props.get("firstLine", 0):
        special = f"отступ первой строки {cm(props['firstLine'])} см вместо выступа"
    else:
        special = "выступа нет"
    report.add(p, f"Сейчас: слева {cm(checked_left, 1.25)} см, справа {cm(right, 0)} см, {special}.",
               expected, prop="heading_indentation",
               diagnostic=Diagnostic("section_heading_indentation", p, "heading_indentation",
                                     values, tuple(mm_to_twips(mm) for mm in SECTION_INDENT_MM) + (0,)))


@rule("F06")
def heading_text(model, report):
    for p in ordinary(model):
        check_alignment(p, report, "both", "Текст", "text_alignment")
    for p in titles(model):
        if p.kind == "heading" or p.kind in ("abstract_heading", "appendices_heading"):
            if unnumbered(p):
                check_alignment(p, report, "center", "Заголовок без номера", "unnumbered_heading_alignment")
                check_zero_indent(p, report)
            else:
                subject = "Заголовок раздела" if p.heading_level == 1 else "Заголовок подраздела"
                check_alignment(p, report, "left", subject, "heading_alignment")
        lines = [line for line in p.text.splitlines() if line.strip()]
        if p.kind == "appendix_heading":
            lines = lines[2:] if len(lines) > 1 and lines[1].strip().startswith("(") else lines[1:]
        elif p.kind == "appendix_meta":
            lines = lines[1:]
        if len(lines) > 1:
            report.add(p, "В заголовок вставлен ручной перенос строки.",
                       "Проверьте вторую строку заголовка: она должна начинаться под текстом первой строки. "
                       "Не выравнивайте её номером или пробелами.",
                       "review", prop="heading_continuation")
    for p in model.headings:
        if p.heading_level not in (1, 2):
            continue
        title = p.heading_title
        if not title:
            report.add(p, "Заголовок отсутствует.", "Укажите заголовок раздела или подраздела.")
            continue
        if p.heading_level == 1 and cap_errors(p, title):
            report.add(p, "Заголовок раздела написан не только прописными буквами.",
                       "Напишите заголовок раздела прописными буквами.")
        if p.heading_level == 2:
            display_title = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", p.display_text).strip()
            first = next((c for c in display_title if c.isalpha()), "")
            if first and first.islower():
                report.add(p, "Заголовок подраздела начинается со строчной буквы.",
                           "Напишите первую букву заголовка прописной.")
            words = re.findall(r"[А-Яа-яЁёA-Za-z]+", display_title)
            if len(words) > 1 and all(w.isupper() for w in words):
                report.add(p, "Все слова заголовка подраздела прописные.",
                           "Проверьте строчное написание после первой прописной, учитывая аббревиатуры и имена.", "review")
        if title.endswith("."):
            report.add(p, "Заголовок заканчивается точкой.", "Уберите точку в конце заголовка.")
        if any(r.props.get("underline") for r in visible_runs(p)):
            report.add(p, "В заголовке есть подчёркивание.", "Уберите подчёркивание заголовка.")
        if "\u00ad" in title or re.search(r"-\n[а-яa-z]", title):
            report.add(p, "В заголовке слово перенесено вручную.", "Уберите перенос слова в заголовке.")
