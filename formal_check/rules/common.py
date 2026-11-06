import logging
import re

from ..xmlutil import xp
from ..constraints import FIRST_LINE_MM, mm_to_twips
from ..messages import number as format_number
from ..model import Diagnostic

LOG = logging.getLogger("formal_check")
APP_ALPHABET = "АБВГДЕЖИКЛМНПРСТУФХЦШЩЭЮЯ"
FIRST_LINE_ACTION = ("В окне «Абзац» для первой строки выберите «Отступ» на 1,25 или 1,27 см. "
                     "Пробелы и табуляция не подходят.")


def page_section(model, index, *details):
    """Word calls sectPr a «раздел»; omit the number for a single-section document."""
    parts = ["Параметры страницы"]
    if len(model.document.sections) > 1:
        parts.append(f"раздел Word {index}")
    return ", ".join(parts + list(details))


def ordinary(model):
    return [p for p in model.body if p.region in ("main", "appendix", "abstract") and
            p.kind == "text" and p.text.strip() and not p.uncertain]


def prose(model):
    return [p for p in model.paragraphs if p.region in ("main", "appendix") and
            p.kind in ("text", "list", "note", "explanation") and not p.uncertain]


def visible_runs(p):
    return [r for r in p.runs if r.text.strip() and not r.props.get("hidden")]


def indent(p):
    if "firstLineChars" in p.props or "hangingChars" in p.props:
        return None
    return -p.props["hanging"] if "hanging" in p.props else p.props.get("firstLine", 0)


def good_indent(value):
    return value is not None and any(abs(value - mm * 1440 / 25.4) <= 1 for mm in FIRST_LINE_MM)


def first_line_target(model):
    """The allowed first-line indent this document already uses most often.

    Both 12,5 and 12,7 mm are allowed. Correcting to the prevailing one keeps
    the document uniform; with no evidence or a tie, 12,5 mm is used.
    """
    if model.first_line_target is None:
        counts = dict.fromkeys(FIRST_LINE_MM, 0)
        for p in ordinary(model):
            value = indent(p)
            for mm in FIRST_LINE_MM:
                if value is not None and abs(value - mm * 1440 / 25.4) <= 1:
                    counts[mm] += 1
        best = max(FIRST_LINE_MM, key=lambda mm: (counts[mm], mm == FIRST_LINE_MM[0]))
        model.first_line_target = mm_to_twips(best)
    return model.first_line_target


def check_indent(model, p, report):
    actual = indent(p)
    if actual is None:
        LOG.info("%s: отступ задан в знаках; перевод в миллиметры требует раскладки.", report.rule)
    elif not good_indent(actual):
        if actual == 0:
            fact = "У абзаца нет отступа первой строки."
        elif actual > 0:
            fact = f"Отступ первой строки: {format_number(actual * 2.54 / 1440)} см."
        else:
            fact = f"Вместо отступа первой строки задан выступ {format_number(-actual * 2.54 / 1440)} см."
        report.add(p, fact, FIRST_LINE_ACTION,
                   prop="first_line_indent",
                   diagnostic=(Diagnostic("paragraph_first_line_indent", p, "first_line_indent",
                                          actual, first_line_target(model))
                               if report.rule in ("F04", "F05") else None))


def cap_errors(p, title=None):
    text = p.display_text.strip() or p.text.strip()
    if title is not None:
        text = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", text)
    return bool(any(c.isalpha() for c in text) and text != text.upper())


def explicit_page_break(model, p):
    if p.props.get("page_break_before") or xp(p.xml, './w:r/w:br[@w:type="page"]'):
        return True
    previous = [x for x in model.body if x.index < p.index]
    for prev in reversed(previous):
        if xp(prev.xml, './/w:br[@w:type="page"] | ./w:pPr/w:sectPr'):
            return True
        if prev.text.strip():
            break
    return False


def has_gap(model, p, after=True):
    others = [x for x in model.body if (x.index > p.index if after else x.index < p.index)]
    if not others:
        return None
    adjacent = others[0] if after else others[-1]
    if not adjacent.text.strip() and not adjacent.images and not adjacent.math:
        return True
    key = "after" if after else "before"
    other_key = "before" if after else "after"
    if p.props.get(key + "Lines", 0) >= 100 or adjacent.props.get(other_key + "Lines", 0) >= 100:
        return True
    if p.props.get(key, 0) > 0 or adjacent.props.get(other_key, 0) > 0:
        return None  # A positive spacing may or may not equal a full blank line.
    return False


def size_range(p):
    return sorted({r.props["size"] for r in visible_runs(p) if "size" in r.props})


def numbering_check(items, report, label, allow_continuations=False, gap_status="error"):
    """Items: (paragraph, number string, continuation bool). Supports two schemes."""
    seen, counters, modes = {}, {}, {}
    plural = {"формулы": "формул", "рисунка": "рисунков", "таблицы": "таблиц"}[label]
    nominative, accusative = {"формулы": ("формула", "формулы"), "рисунка": ("рисунок", "рисунки"),
                              "таблицы": ("таблица", "таблицы")}[label]

    def shown(value):
        return f"({value})" if label == "формулы" else value
    for p, number, continuation in items:
        if continuation and allow_continuations:
            if (p.appendix, number) not in seen:
                report.add(p, f"Для продолжения {label} {number} не найдена первая часть.",
                           "Проверьте, есть ли перед продолжением первая часть с тем же номером.", "review")
            continue
        key = (p.appendix, number)
        if key in seen:
            report.add([seen[key], p], f"Номер {label} {shown(number)} встречается дважды.",
                       f"Перенумеруйте {accusative} по порядку.")
            continue
        seen[key] = p
        parts = number.split(".")
        if p.appendix:
            if len(parts) != 2 or parts[0] != p.appendix or not parts[1].isdigit():
                report.add(p, f"Номер {label}: {number}.",
                           f"В приложении {p.appendix} указывайте букву приложения перед номером, например {p.appendix}.1.")
                continue
        elif not all(s.isdigit() for s in parts) or len(parts) not in (1, 2):
            report.add(p, f"Номер {label} «{number}» записан в неподходящем формате.",
                       "Используйте арабские цифры: например, 1 при сквозной нумерации или 2.1 при нумерации по разделам.")
            continue
        scope = tuple(parts[:-1])
        counter_key = (p.appendix, scope)
        n = int(parts[-1])
        expected = counters.get(counter_key, 0) + 1
        if n != expected:
            wanted = shown(".".join(parts[:-1] + [str(expected)]))
            if counter_key in counters:
                previous = shown(".".join(parts[:-1] + [str(counters[counter_key])]))
                fact = f"После {label} {previous} идёт {nominative} {shown(number)}."
            else:
                fact = f"Нумерация {plural} начинается с номера {shown(number)}."
            report.add(p, fact,
                       (f"Проверьте нумерацию: следующей должна быть формула {wanted}. "
                        "Если формулы с пропущенными номерами вставлены картинками, сверьте их номера вручную."
                        if gap_status == "review" else f"Исправьте номер на {wanted}."),
                       gap_status)
        counters[counter_key] = n
        modes.setdefault(p.appendix, set()).add(len(parts))
    for app, mode in modes.items():
        if len(mode) > 1:
            targets = [p for p, _, c in items if p.appendix == app and not c]
            report.add(targets, f"Одновременно используются сквозная нумерация {plural} и нумерация по разделам.",
                       f"Проверьте выбранную систему и приведите номера {plural} к одному варианту.", "review")
