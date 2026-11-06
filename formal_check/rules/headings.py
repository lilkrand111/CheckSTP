"""Shared heading checks from the rectorate clarification supplied on 21.10.2026."""
import re

from .common import indent
from ..constraints import BLANK_LINE_SPACING
from ..formatting import blank_line_spacing_values, paragraph_edge_spacing_values
from ..messages import alignment, number
from ..model import Diagnostic
from ..xmlutil import xp

SPECIAL_TITLES = {"toc_heading", "abstract_heading", "bibliography_heading", "appendices_heading",
                  "appendix_heading", "appendix_title", "appendix_meta"}


def titles(model):
    return [p for p in model.body if p.kind in SPECIAL_TITLES or
            (p.kind == "heading" and p.heading_level in (1, 2))]


def unnumbered(p):
    return p.kind in SPECIAL_TITLES or (p.kind == "heading" and p.heading_level == 1
                                       and not p.heading_number)


def title_runs(p):
    """Status rows of a combined appendix heading need not be bold."""
    omitted = set()
    if p.kind in ("appendix_heading", "appendix_meta"):
        omitted = {i for i, line in enumerate(p.text.splitlines())
                   if re.fullmatch(r"\s*\([^()]+\)\s*", line)}
    line = 0
    selected = []
    for r in p.runs:
        if r.props.get("hidden"):
            continue
        parts = r.text.split("\n")
        if any(part.strip() and line + i not in omitted for i, part in enumerate(parts)):
            selected.append(r)
        line += len(parts) - 1
    return selected


def physical_alignment(p):
    value = p.props["alignment"]
    if value in ("start", "end"):
        return "left" if (value == "start") != p.props.get("bidi", False) else "right"
    return value


ALIGNMENT_TARGETS = {"left": "по левому краю", "both": "по ширине", "center": "по центру"}


def check_alignment(p, report, target, subject, prop):
    """Subject is a nominative noun phrase, e.g. «Текст» or «Заголовок раздела»."""
    value = physical_alignment(p)
    if value != target:
        name = alignment(value)
        fact = (f"{subject} выровнен {name}." if name != "нестандартное выравнивание"
                else f"{subject}: нестандартное выравнивание.")
        obj = "абзац" if subject == "Текст" else "заголовок"
        report.add(p, fact, f"Выровняйте {obj} {ALIGNMENT_TARGETS[target]}.", prop=prop,
                   diagnostic=(Diagnostic("paragraph_alignment", p, prop, value, target)
                               if report.rule == "F06" else None))


def check_zero_indent(p, report):
    # A centered title can still be shifted by a whole-paragraph side indent.
    # Check those separately from firstLine/hanging so Word's controls map to
    # distinct, actionable findings.
    for side, alias, chars, label in (
            ("left", "start", ("leftChars", "startChars"), "слева"),
            ("right", "end", ("rightChars", "endChars"), "справа")):
        prop = f"heading_{side}_indent"
        if any(key in p.props for key in chars):
            report.add(p, f"У заголовка без номера отступ {label} задан в знаках, а не в сантиметрах.",
                       f"В окне «Абзац» установите для заголовка отступ {label} 0 см.",
                       "review", prop=prop)
            continue
        size = p.props.get(alias, p.props.get(side, 0))
        if size != 0:
            cm = size * 2.54 / 1440
            display = number(cm)
            if display == "0":
                display = number(cm, precision=3)
            report.add(p, f"У заголовка без номера отступ {label} {display} см.",
                       "В окне «Абзац» установите отступы слева и справа 0 см "
                       "и уберите отступ первой строки.", prop=prop,
                       diagnostic=(Diagnostic("paragraph_side_indent", p, prop, size, 0)
                                   if report.rule == "F06" else None))
    value = indent(p)
    if value is None:
        report.add(p, "У заголовка без номера отступ первой строки задан в знаках.",
                   "Уберите у заголовка отступ первой строки.",
                   "review", prop="heading_first_line_indent")
    elif value != 0:
        fact = "отступ" if value > 0 else "выступ"
        report.add(p, f"У заголовка без номера {fact} первой строки "
                   f"{number(abs(value) * 2.54 / 1440)} см.",
                   "Уберите у заголовка отступ первой строки.", prop="heading_first_line_indent",
                   diagnostic=(Diagnostic("paragraph_first_line_indent", p, "heading_first_line_indent", value, 0)
                               if report.rule == "F06" else None))


GAP_EXPECTED = "После заголовка оставьте одну пустую строку: один пустой абзац или интервал такой же высоты."
GAP_FIX = ("Для пустой строки после заголовка выберите одинарный интервал, «Перед» и «После» 0 пт. "
           "У заголовка и следующего абзаца уберите интервалы.")


def line_spacing(line, rule):
    if rule == "auto":
        return None if line == 240 else f"межстрочный интервал {number(line / 240)}"
    name = {"exact": "Точно", "atLeast": "Минимум"}.get(rule)
    return f"межстрочный интервал {'«' + name + '» ' if name else ''}{number(line / 20)} пт"


def edge_details(points, lines, auto, owner, side):
    """owner: «у заголовка» or «у следующего абзаца»; side: «перед» or «после»."""
    details = []
    if points:
        details.append(f"{owner} интервал {side} {number(points / 20)} пт")
    if lines:
        details.append(f"{owner} интервал {side} в строках {number(lines / 100)}")
    if auto:
        details.append(f"{owner} автоматический интервал {side}")
    return details


def blank_details(line, rule, before, after, before_lines, after_lines, before_auto, after_auto):
    details = [text for text in (line_spacing(line, rule),) if text]
    if before and before == after:
        details.append(f"сверху и снизу по {number(before / 20)} пт")
    else:
        if before:
            details.append(f"сверху {number(before / 20)} пт")
        if after:
            details.append(f"снизу {number(after / 20)} пт")
    if before_lines or after_lines:
        details.append(f"интервалы в строках: перед {number(before_lines / 100)}, после {number(after_lines / 100)}")
    if before_auto or after_auto:
        details.append("автоматический интервал")
    return details


def empty_paragraphs(count):
    """«21 пустой абзац», «2 пустых абзаца», «5 пустых абзацев»."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} пустой абзац"
    few = count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14)
    return f"{count} пустых {'абзаца' if few else 'абзацев'}"


def sentence(head, details):
    return head + (": " + ", ".join(details) if details else "") + "."


def check_heading_gap(model, p, report):
    following = [x for x in model.paragraphs if x.index > p.index]
    blanks = []
    next_p = None
    for item in following:
        if item.table is None and item.kind == "blank":
            blanks.append(item)
        else:
            next_p = item
            break
    if next_p is None:
        return
    if any(xp(x.xml, './/w:br[@w:type="page"] | ./w:pPr/w:sectPr') for x in blanks):
        report.add(p, "Между заголовком и текстом стоит разрыв страницы или раздела.",
                   GAP_EXPECTED, "review", prop="heading_gap")
        return
    spaced = p.props.get("after", 0) > 0 or next_p.props.get("before", 0) > 0
    automatic = any(x.props.get(key) in ("1", "true", "on") for x, key in
                    ((p, "afterAutospacing"), (next_p, "beforeAutospacing")))
    lines = max(p.props.get("afterLines", 0), next_p.props.get("beforeLines", 0))
    # Limit automatic normalization to one adjacent, genuinely empty paragraph.
    # Never cross a table/content control or reinterpret fields and line breaks.
    plain_blank = (len(blanks) == 1 and
                   not blanks[0].props.get("page_break_before") and
                   not any(r.text.strip() for r in blanks[0].runs) and
                   not xp(blanks[0].xml, ".//w:br | .//w:cr | .//w:fldChar | "
                          ".//w:fldSimple | .//w:instrText"))
    # A correct blank may precede a TOC content control. Accept its settings,
    # but do not automatically edit across such structural boundaries.
    if (plain_blank and not spaced and not lines and not automatic and
            blank_line_spacing_values(blanks[0].props) == BLANK_LINE_SPACING):
        return
    simple_blank = (plain_blank and blanks[0].xml.getprevious() is p.xml and
                    next_p.xml.getprevious() is blanks[0].xml)
    gap_boundaries = any(
        xp(x.xml, './/w:br[@w:type="page" or @w:type="column"] | ./w:pPr/w:sectPr')
        for x in (p, next_p)) or next_p.props.get("page_break_before")
    if simple_blank and not gap_boundaries:
        blank = blanks[0]
        actual = (paragraph_edge_spacing_values(p.props, "after"),
                  blank_line_spacing_values(blank.props),
                  paragraph_edge_spacing_values(next_p.props, "before"))
        required = ((0, 0, False), BLANK_LINE_SPACING, (0, 0, False))
        if actual == required:
            return
        details = (blank_details(*actual[1]) +
                   edge_details(*actual[0], "у заголовка", "после") +
                   edge_details(*actual[2], "у следующего абзаца", "перед"))
        report.add(p, sentence("Промежуток после заголовка не равен одной строке", details),
                   GAP_FIX, prop="heading_gap",
                   diagnostic=Diagnostic("blank_gap_spacing", blank, "heading_gap", actual, required))
        return
    if (not blanks and lines == 100 and not spaced and not automatic and
            not p.props.get("contextual_spacing") and not next_p.props.get("contextual_spacing")):
        return
    if not blanks and not lines and not spaced and not automatic:
        report.add(p, "После заголовка нет пустой строки.", GAP_EXPECTED, prop="heading_gap")
    else:
        edges = (edge_details(p.props.get("after", 0), p.props.get("afterLines", 0),
                              p.props.get("afterAutospacing") in ("1", "true", "on"), "у заголовка", "после") +
                 edge_details(next_p.props.get("before", 0), next_p.props.get("beforeLines", 0),
                              next_p.props.get("beforeAutospacing") in ("1", "true", "on"),
                              "у следующего абзаца", "перед"))
        if len(blanks) >= 2 and not edges:
            report.add(p, f"После заголовка {empty_paragraphs(len(blanks))}.", "Оставьте один пустой абзац.",
                       "review", prop="heading_gap")
            return
        details = [f"пустых абзацев {len(blanks)}"] if blanks else []
        details += edge_details(p.props.get("after", 0), p.props.get("afterLines", 0),
                                 p.props.get("afterAutospacing") in ("1", "true", "on"),
                                 "у заголовка", "после")
        details += edge_details(next_p.props.get("before", 0), next_p.props.get("beforeLines", 0),
                                 next_p.props.get("beforeAutospacing") in ("1", "true", "on"),
                                 "у следующего абзаца", "перед")
        if len(blanks) == 1:
            blank_props = blanks[0].props
            details += [f"у пустой строки {text}" for text in blank_details(
                blank_props["line"], blank_props["lineRule"], blank_props.get("before", 0),
                blank_props.get("after", 0), 0, 0, False, False)]
        report.add(p, sentence("Промежуток после заголовка задан нестандартно", details),
                   GAP_EXPECTED + " Проверьте промежуток в Word.", "review", prop="heading_gap")
