import re

from . import rule
from .common import APP_ALPHABET, LOG, cap_errors, explicit_page_break, page_section, prose, size_range
from .headings import check_heading_gap, check_zero_indent, physical_alignment, titles
from ..classify import heading_parts, normalized
from ..xmlutil import attr, child, integer, parse, q, val, xp
from ..messages import alignment, page_number_format


@rule("F07")
def heading_spacing(model, report):
    for p in titles(model):
        if p.kind == "appendix_heading" and len(p.text.strip().splitlines()) < 3:
            continue  # Designation/status/title rows are adjacent, not separated by blank lines.
        check_heading_gap(model, p, report)


@rule("F08")
def contents(model, report):
    toc_headers = [p for p in model.body if p.kind == "toc_heading"]
    if not toc_headers and not model.toc_entries:
        if model.headings:
            report.add(model.headings[0], "В документе нет содержания.", "Разместите содержание непосредственно после задания.")
        else:
            LOG.info("F08: структура полного документа не распознана; наличие содержания не оценено.")
        return
    if toc_headers:
        toc = toc_headers[0]
        prior = [p for p in model.body if p.index < toc.index and p.text.strip()]
        task = [p for p in prior if re.match(r"^\s*задание\b", p.text, re.I)]
        if task and any(p.kind == "heading" for p in prior if p.index > task[-1].index):
            report.add(toc, "Между заданием и содержанием стоят разделы основной части.",
                       "Разместите содержание непосредственно после задания.")
        elif not task:
            LOG.info("F08: граница задания не распознана; непосредственное следование содержания не проверено.")
    if not model.toc_entries:
        LOG.warning("F08: содержание обнаружено, но его записи не распознаны; сопоставление пропущено.")
        return
    entries = {}
    for p in model.toc_entries:
        text = re.sub(r"(?:\t+|\.{2,})\s*\S+\s*$", "", p.numbered_text).strip()
        number, title = heading_parts(text)
        entries.setdefault(number, []).append((p, normalized(title)))
    for p in model.headings:
        if p.region != "main" or (p.heading_level > 2 and not p.heading_title):
            continue
        desired = normalized(p.heading_title)
        candidates = entries.get(p.heading_number, [])
        if any(title == desired for _, title in candidates):
            continue
        if p.heading_number and candidates:
            report.add([p, candidates[0][0]], "Заголовки с одинаковым номером в содержании и тексте различаются.",
                       "Согласуйте номер и заголовок содержания с основным текстом.")
        else:
            report.add(p, "Этого заголовка нет в содержании.",
                       "Включите номер и заголовок в содержание.", "review" if not p.heading_number else "error")
    main_numbers = {p.heading_number for p in model.headings if p.region == "main" and p.heading_number}
    for number, items in entries.items():
        if number and number not in main_numbers:
            report.add(items[0][0], "В тексте нет заголовка с этим номером из содержания.",
                       "Сравните запись с заголовком в основном тексте и обновите содержание.", "review")


@rule("F09")
def contents_style(model, report):
    for p in model.body:
        if p.kind != "toc_heading":
            continue
        if cap_errors(p):
            report.add(p, "Слово «СОДЕРЖАНИЕ» написано не только прописными буквами.",
                       "Напишите «СОДЕРЖАНИЕ» прописными буквами.")
        if any(not 14 <= s <= 16 for s in size_range(p)):
            report.add(p, "Размер шрифта заголовка «СОДЕРЖАНИЕ» вне диапазона 14–16 пт.",
                       "Задайте размер шрифта 14–16 пт.")
        if p.props["alignment"] != "center":
            report.add(p, f"Заголовок содержания выровнен {alignment(physical_alignment(p))}.",
                       "Выровняйте заголовок содержания по центру.", prop="unnumbered_heading_alignment")
        check_zero_indent(p, report)
    for p in model.toc_entries:
        if not re.search(r"(?:\t|\.{2,})\s*\d+\s*$", p.text):
            continue
        dot_tabs = any(align in ("right", "end") and leader == "dot"
                       for align, leader in p.props["tabs"].values())
        if not dot_tabs and not re.search(r"\.{2,}", p.text):
            report.add(p, "В строке содержания нет точек перед номером страницы.",
                       "Добавьте точки между названием и номером страницы. "
                       "Номер страницы выровняйте по правому краю.", "review")


@rule("F10")
def page_numbering(model, report):
    references = {}
    page_found = False
    for i, section in enumerate(model.document.sections, 1):
        element = section._sectPr
        numbering = child(element, "w:pgNumType")
        fmt, start = attr(numbering, "fmt", "decimal"), integer(attr(numbering, "start"))
        location = {"section": page_section(model, i), "property": "page_numbering"}
        if fmt != "decimal":
            report.add(location, f"Формат нумерации страниц: {page_number_format(fmt)}.",
                       "Выберите обычные арабские цифры: 1, 2, 3 и так далее.")
        if i > 1 and start is not None:
            report.add(location, f"В этом разделе Word нумерация страниц начинается заново с {start}.",
                       "Проверьте, что нумерация страниц сквозная, включая приложения.", "review")
        for kind in ("header", "footer"):
            for ref in xp(element, "./w:" + kind + "Reference"):
                references[(kind, attr(ref, "type", "default"))] = ref.get(q("r:id"))
        for (kind, variant), rel_id in sorted(references.items()):
            if variant == "first" and child(element, "w:titlePg") is None:
                continue
            if variant == "even" and child(model.parts.get("word/settings.xml"), "w:evenAndOddHeaders") is None:
                continue
            rel = model.document.part.rels.get(rel_id)
            if rel is None:
                LOG.warning("F10: отсутствующая связь колонтитула %s.", rel_id)
                continue
            root = parse(rel.target_part.blob)
            for p in xp(root, ".//w:p"):
                fields = " ".join(xp(p, ".//w:instrText/text()") + xp(p, ".//w:fldSimple/@w:instr"))
                if not re.search(r"\bPAGE\b", fields, re.I):
                    continue
                page_found = True
                kind_name = "верхний колонтитул" if kind == "header" else "нижний колонтитул"
                variant_name = {"first": "первая страница", "even": "чётные страницы",
                                "default": "обычные страницы"}.get(variant, "особые страницы")
                loc = {"section": page_section(model, i, kind_name, variant_name), "property": "PAGE"}
                if kind == "header":
                    report.add(loc, "Номер страницы стоит вверху страницы.",
                               "Перенесите номер страницы вниз, в нижний колонтитул.")
                if re.search(r"\\\*\s+(?:ROMAN|roman|ALPHABETIC|alphabetic)\b", fields):
                    report.add(loc, "Номер страницы записан римскими цифрами или буквами.",
                               "Выберите для номера страницы обычные арабские цифры: 1, 2, 3 и так далее.")
                props = model.resolver.paragraph(p)
                if kind == "footer" and props["alignment"] not in ("right", "end"):
                    right_tab = any(a in ("right", "end") for a, _ in props["tabs"].values())
                    if not right_tab:
                        report.add(loc, "Номер страницы не выровнен по правому краю.",
                                   "Номер страницы должен стоять справа внизу. "
                                   "Выровняйте его по правому краю.", "review")
    if not page_found and model.headings:
        # Without any header/footer the document certainly has no page numbers;
        # an existing header/footer may still hold a number typed by hand.
        status = "review" if references else "error"
        report.add({"section": "Нумерация страниц", "property": "PAGE"},
                   "В документе нет автоматических номеров страниц.",
                   "Добавьте номера: «Вставка» → «Номер страницы» → «Внизу страницы», справа. "
                   "Номера должны идти подряд.", status)


@rule("F12")
def list_references(model, report):
    numbers = {".".join(map(str, p.heading_number)) for p in model.headings if p.heading_number}
    list_labels = set()
    for p in model.paragraphs:
        if p.kind != "list":
            continue
        if p.label:
            list_labels.add(p.label.rstrip(".)").casefold())
        manual = re.match(r"^\s*([а-яё]|\d+(?:\.\d+)*)[.)](?:\s|$)", p.numbered_text, re.I)
        if manual:
            list_labels.add(manual[1].casefold())
    for p in prose(model):
        for match in re.finditer(
                r"\b(?:пункт(?:е|а|у|ом)?|подпункт(?:е|а|у|ом)?)\s+"
                r"(?P<quote>[«\"']?)(?P<label>\d+(?:\.\d+)*|[а-яё])(?!\w|\.\d)"
                r"(?P<close>[»\"']?)(?P<paren>\)?)", p.text, re.I):
            label = match['label'].casefold()
            # A bare preposition/conjunction followed by another word is not
            # evidence of a lettered reference: «пункт в начальном меню».
            if (label in {'а', 'в', 'и', 'к', 'о', 'с', 'у'} and
                    not (match['quote'] or match['close'] or match['paren']) and
                    re.match(r"\s+\w", p.text[match.end():])):
                continue
            if match['paren']:
                report.add(p, f"Ссылка «{match[0]}» содержит закрывающую скобку.",
                       "Уберите закрывающую скобку после номера или буквы пункта в ссылке.")
            if label not in numbers | list_labels:
                report.add(p, f"Ссылка «{match[0]}» ведёт на пункт, которого нет в документе.",
                           "Проверьте номер или букву пункта в ссылке.", "review")


@rule("F29")
def appendix_numbers(model, report):
    known = {p.appendix: p for p in model.appendices}
    seen = set()
    for index, p in enumerate(model.appendices):
        expected = APP_ALPHABET[index] if index < len(APP_ALPHABET) else None
        if p.appendix in seen:
            report.add(p, f"Обозначение приложения {p.appendix} повторяется.",
                       "Исправьте обозначения: у каждого приложения должна быть своя буква.")
        elif p.appendix != expected:
            report.add(p, f"Приложение обозначено буквой «{p.appendix}», которая не соответствует порядку приложений.",
                       f"Обозначьте приложение русской прописной буквой {expected or 'из допустимой последовательности'}. "
                       "Обозначения начинаются с А; буквы Ё, З, Й, О, Ч, Ъ, Ы и Ь не используются.")
        seen.add(p.appendix)
    references = []
    for p in prose(model):
        if p.region != "main":
            continue
        for match in re.finditer(r"\bприложени[еяию]\s+([А-ЯЁA-Z])\b", p.text, re.I):
            letter = match[1].upper()
            if letter not in known:
                report.add(p, f"В тексте есть ссылка на приложение {letter}, но такое приложение не найдено.",
                           f"Добавьте приложение {letter} или исправьте букву в ссылке.",
                           "error" if model.main_complete else "review")
            elif letter not in references:
                references.append(letter)
    for letter, p in known.items():
        if letter not in references:
            report.add(p, f"В основном тексте нет ссылки на приложение {letter}.",
                       f"Добавьте ссылку в текст, например «в приложении {letter}».", "error" if model.main_complete else "review")
    actual_order = [p.appendix for p in model.appendices if p.appendix in references]
    if references != actual_order:
        report.add(model.appendices, "Порядок приложений отличается от порядка первых ссылок.",
                   "Расположите приложения в порядке первого упоминания.")


@rule("F30")
def appendix_heading(model, report):
    for p in titles(model):
        if p.kind in ("appendix_heading", "appendix_title", "appendix_meta"):
            check_zero_indent(p, report)
    for app in model.appendices:
        designation = (app.display_text.strip() or app.text.strip()).splitlines()[0]
        if designation != designation.upper():
            report.add(app, "Обозначение приложения написано не только прописными буквами.",
                       f"Напишите «ПРИЛОЖЕНИЕ {app.appendix}» прописными.")
        if app.props["alignment"] != "center":
            report.add(app, "Обозначение приложения не выровнено по центру.", "Выровняйте обозначение по центру.",
                       prop="unnumbered_heading_alignment")
        rows = []
        following = [app] + [p for p in model.body if p.index > app.index and p.text.strip()]
        for p in following:
            if p is not app and (p.kind == "appendix_heading" or p.region != "appendix"):
                break
            lines = [line.strip() for line in p.text.splitlines() if line.strip()]
            displayed = [line.strip() for line in p.display_text.splitlines() if line.strip()]
            for index, line in enumerate(lines):
                if p is app and index == 0:
                    continue
                rows.append((p, line, displayed[index] if index < len(displayed) else line))
            if len(rows) >= 2:
                break
        status = rows[0][0] if rows else None
        status_text = rows[0][1] if rows else ""
        valid_status = re.fullmatch(r"\((?:обязательное|рекомендуемое|справочное)\)", status_text)
        if not valid_status:
            choices = "«(обязательное)», «(рекомендуемое)» или «(справочное)»"
            if re.fullmatch(r"\([^()]+\)", status_text):
                report.add(app, f"Статус приложения записан неверно: «{rows[0][2]}».",
                           f"Запишите статус строчными буквами в скобках: {choices}.")
            else:
                designation = (app.display_text.strip() or app.text.strip()).splitlines()[0].strip()
                report.add(app, f"Под обозначением «{designation}» нет строки статуса.",
                           f"Добавьте под обозначением строку {choices}.")
        else:
            if status is not app and status.props["alignment"] != "center":
                report.add(status, "Строка статуса не выровнена по центру.", "Выровняйте статус по центру.")
        title_index = 1 if re.fullmatch(r"\([^()]+\)", status_text) else 0
        title = rows[title_index][0] if len(rows) > title_index else None
        if title is None or title.kind not in ("appendix_heading", "appendix_title", "appendix_meta", "text"):
            report.add(app, "Под строкой статуса не найден заголовок приложения.",
                       "Проверьте заголовок приложения: он стоит отдельной строкой ниже статуса.", "review")
        else:
            first = next((c for c in rows[title_index][2] if c.isalpha()), "")
            if first.islower():
                report.add(title, "Заголовок приложения начинается со строчной буквы.", "Начните заголовок с прописной буквы.")
            if (title is not app and (title is not status or not valid_status) and
                    title.props["alignment"] != "center"):
                report.add(title, "Заголовок приложения не выровнен по центру.", "Выровняйте заголовок по центру.",
                           prop="unnumbered_heading_alignment")
        if not explicit_page_break(model, app):
            LOG.info("F30: %s — новая страница явно не задана, фактическое начало не проверено.", app.section)


@rule("F31")
def bibliography_heading(model, report):
    headers = [p for p in model.body if p.kind == "bibliography_heading"]
    for p in headers:
        check_zero_indent(p, report)
        if cap_errors(p):
            report.add(p, "Заголовок списка источников написан не только прописными буквами.",
                       "Напишите «СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ» прописными.")
        if p.props["alignment"] != "center":
            report.add(p, "Заголовок списка источников не выровнен по центру.", "Выровняйте заголовок по центру.",
                       prop="unnumbered_heading_alignment")
        if any(a.index < p.index for a in model.appendices):
            report.add(p, "Список источников расположен после начала приложений.",
                       "Расположите список в конце записки перед приложениями.")
        if any(h.index > p.index and h.region == "main" for h in model.headings):
            report.add(p, "После списка источников есть раздел основной части.", "Расположите список после основной части.")
        if not explicit_page_break(model, p):
            LOG.info("F31: новая страница библиографии явно не задана; требуется просмотр.")
