"""F22: the customer-requested blank line between a figure and its caption."""
from .common import LOG
from .headings import blank_details, edge_details, empty_paragraphs, sentence
from ..constraints import BLANK_LINE_SPACING
from ..formatting import blank_line_spacing_values, paragraph_edge_spacing_values
from ..model import Diagnostic
from ..reader import paragraph_runs
from ..xmlutil import attr, q, xp


EXPECTED = (
    "Между рисунком и подписью оставьте одну пустую строку: "
    "один пустой абзац или интервал такой же высоты."
)
GAP_FIX = ("Для пустой строки между рисунком и подписью выберите одинарный интервал, "
           "«Перед» и «После» 0 пт. У рисунка и подписи уберите интервалы.")
# Embedded objects that the reader does not count as pictures (OLE, VML,
# compatibility wrappers). Such a paragraph may hold the figure itself.
OBJECTS = ".//w:object | .//w:pict | .//w:drawing | .//mc:AlternateContent | .//w:txbxContent"


def pagination_marker(node):
    return (node.tag == q("w:lastRenderedPageBreak") or
            (node.tag == q("w:br") and attr(node, "type") in ("page", "column")))


def break_before_caption_text(model, caption):
    """Markers after the caption has started do not describe its leading gap."""
    for run in paragraph_runs(caption.xml):
        if model.resolver.run(caption.xml, run).get("hidden"):
            continue
        for node in run:
            if pagination_marker(node):
                return True
            if node.tag == q("w:t") and (node.text or "").strip():
                return False
    return False


def after_picture(model, p):
    """Visible run content after the last picture, preserving explicit breaks."""
    tail = None
    before = ""
    gap_break = False
    for run in paragraph_runs(p.xml):
        if model.resolver.run(p.xml, run).get("hidden"):
            continue
        for node in run:
            if node.tag in (q("w:drawing"), q("w:pict")):
                before += tail or ""
                tail = ""
                # A marker before the last picture concerns the picture's
                # preceding page, not the gap following that picture.
                gap_break = False
            elif pagination_marker(node):
                if tail is not None and not tail.strip():
                    gap_break = True
            elif node.tag in (q("w:t"), q("w:tab"), q("w:br"), q("w:cr")):
                if node.tag == q("w:t"):
                    text = node.text or ""
                elif node.tag == q("w:tab"):
                    text = "\t"
                else:
                    text = "" if attr(node, "type") in ("page", "column") else "\n"
                if tail is None:
                    before += text
                else:
                    tail += text
    return tail, before, gap_break


def check_figure_gap(model, caption, report):
    blanks = []
    image = caption if caption.images else None
    preceding_text = None
    if image is None and not xp(caption.xml, OBJECTS):
        for p in reversed([p for p in model.paragraphs if p.index < caption.index]):
            if (p.table is not None or p.region != caption.region or p.section != caption.section or
                    p.section_index != caption.section_index or p.appendix != caption.appendix):
                break
            if xp(p.xml, OBJECTS) and not p.images:
                break  # An unrecognized object may be the figure: do not guess.
            if p.kind == "blank":
                blanks.append(p)
                continue
            if p.images:
                image = p
            elif p.text.strip() and not p.math and p.kind not in ("caption", "heading"):
                preceding_text = p
            break
    if image is None and preceding_text is not None:
        report.add(caption, "Над подписью нет рисунка.",
                   "Вставьте рисунок над подписью. Если рисунок стоит в другом месте, "
                   "перенесите подпись под него.", "review", prop="figure_caption_gap")
        return
    if image is None:
        LOG.info("F22: перед подписью «%s» не удалось связать рисунок с подписью; промежуток не оценён.",
                 caption.text.strip()[:80])
        return

    def review(actual):
        report.add(caption, actual, "Проверьте промежуток в Word. " + EXPECTED,
                   "review", prop="figure_caption_gap")

    if (xp(image.xml, ".//wp:anchor | .//w:pict") or
            not xp(image.xml, ".//wp:inline")):
        review("Рисунок размещён с обтеканием текстом, а не в строке текста.")
        return
    tail, before, gap_break = after_picture(model, image)
    # A break before the image or after the caption is outside the interval
    # being checked. An image and caption sharing a paragraph also share its
    # pageBreakBefore/sectPr, which do not separate their inline contents.
    if image is not caption:
        gap_break = gap_break or caption.props.get("page_break_before") or break_before_caption_text(model, caption)
        gap_break = gap_break or bool(xp(image.xml, "./w:pPr/w:sectPr")) or any(
            p.props.get("page_break_before") or
            xp(p.xml, './/w:br[@w:type="page" or @w:type="column"] | ./w:pPr/w:sectPr | '
               './/w:lastRenderedPageBreak') for p in blanks)
    if gap_break:
        review("Между рисунком и подписью стоит разрыв страницы, колонки или раздела.")
        return
    if tail is None or before.strip() or (image is not caption and tail.strip()):
        review("В одном абзаце с рисунком есть текст.")
        return

    if image is caption:
        # Two line breaks after a picture leave one blank line before the text.
        blank_lines = max(0, tail[:len(tail) - len(tail.lstrip())].count("\n") - 1)
        spaced = automatic = False  # Paragraph spacing does not separate its own contents.
        lines = 0
        line_sources = [image]
    else:
        leading = caption.text[:len(caption.text) - len(caption.text.lstrip())]
        blank_lines = sum(1 + p.text.count("\n") for p in blanks) + tail.count("\n") + leading.count("\n")
        spaced = image.props.get("after", 0) != 0 or caption.props.get("before", 0) != 0
        lines = max(image.props.get("afterLines", 0), caption.props.get("beforeLines", 0))
        automatic = any(p.props.get(key) in ("1", "true", "on") for p, key in (
            (image, "afterAutospacing"), (caption, "beforeAutospacing")))
        line_sources = blanks + ([image] if "\n" in tail else []) + ([caption] if "\n" in leading else [])

    if (image is not caption and len(blanks) == 1 and blank_lines == 1 and
            blanks[0].xml.getprevious() is image.xml and caption.xml.getprevious() is blanks[0].xml):
        # One adjacent empty paragraph: the same requirement as after a heading (F07).
        actual = (paragraph_edge_spacing_values(image.props, "after"),
                  blank_line_spacing_values(blanks[0].props),
                  paragraph_edge_spacing_values(caption.props, "before"))
        required = ((0, 0, False), BLANK_LINE_SPACING, (0, 0, False))
        if actual == required:
            return
        details = (blank_details(*actual[1]) + edge_details(*actual[0], "у рисунка", "после") +
                   edge_details(*actual[2], "у подписи", "перед"))
        report.add(caption, sentence("Промежуток между рисунком и подписью не равен одной строке", details),
                   GAP_FIX, prop="figure_caption_gap",
                   diagnostic=Diagnostic("blank_gap_spacing", blanks[0], "figure_caption_gap", actual, required))
        return
    extra = any(p.props.get(key, 0) != 0 for p in blanks
                for key in ("before", "after", "beforeLines", "afterLines"))
    extra = extra or any(p.props.get(key) in ("1", "true", "on") for p in blanks
                         for key in ("beforeAutospacing", "afterAutospacing"))
    line_units_known = all(p.props["lineRule"] == "auto" and p.props["line"] > 0
                           for p in line_sources)
    if blank_lines == 1 and line_units_known and not (spaced or lines or automatic or extra):
        return
    if (blank_lines == 0 and lines == 100 and not (spaced or automatic) and
            not image.props.get("contextual_spacing") and not caption.props.get("contextual_spacing")):
        return
    if not blank_lines and not (spaced or lines or automatic):
        report.add(caption, "Между рисунком и подписью нет пустой строки.",
                   EXPECTED, prop="figure_caption_gap")
    elif (blank_lines >= 2 and len(blanks) == blank_lines and line_units_known and
          not (spaced or lines or automatic or extra)):
        report.add(caption, f"Между рисунком и подписью {empty_paragraphs(blank_lines)}.",
                   "Оставьте один пустой абзац.", "review", prop="figure_caption_gap")
    else:
        review(f"Промежуток между рисунком и подписью задан нестандартно: пустых строк {blank_lines}.")
