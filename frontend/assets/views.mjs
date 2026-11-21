import { STATUSES, RULES, PROPERTIES, filteredFindings } from './report.mjs';
import { isFixing } from './documents.mjs';

export const $ = id => document.getElementById(id);
// The list is drawn in portions: a long report must not build thousands of rows at once.
export const LIST_STEP = 200;
export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
export function notice(id, text, error = false) {
  const node = $(id);
  node.textContent = text || ''; node.hidden = !text;
  node.classList.toggle('error-notice', error);
}
export function reportSelection(entry) {
  const findings = entry.data.report.findings;
  const matching = filteredFindings(findings, { ...entry.filters, hideDone: false }, entry.marks);
  const visible = entry.filters.hideDone ? matching.filter(item => !entry.marks.has(item.index)) : matching;
  if (!visible.some(item => item.index === entry.selectedFinding)) entry.selectedFinding = visible[0]?.index ?? null;
  return { findings, visible, matching, hiddenByMarks: matching.length > 0 && visible.length === 0 };
}
function checkbox(entry, index, onMark, label) {
  const input = element('input'); input.type = 'checkbox'; input.checked = entry.marks.has(index);
  input.disabled = isFixing(entry); input.dataset.focus = `mark-${label}-${index}`;
  input.setAttribute('aria-label', `Исправлено вручную: ${RULES[entry.data.report.findings[index].rule_id] || entry.data.report.findings[index].rule_id}, замечание ${index + 1}`);
  input.addEventListener('change', () => onMark(index, input.checked));
  return input;
}
// One line that tells similar rows apart: where the finding is in the document.
function placeText(finding) {
  const location = finding.locations[0];
  const text = (location?.fragment_start || location?.context || location?.section || '').replace(/\s+/g, ' ').trim();
  return text.length > 90 ? text.slice(0, 90) + '…' : text;
}
// Arrow keys move between rows; Tab then reaches the detail and the actions in a few steps.
export function moveListFocus(event) {
  const step = { ArrowDown: 1, ArrowUp: -1, Home: -Infinity, End: Infinity }[event.key];
  const control = event.target, row = control?.parentElement;
  if (!step || event.altKey || event.ctrlKey || event.metaKey || !row?.classList?.contains('finding-row')) return;
  const rows = [...$('findings').children].filter(node => node.classList.contains('finding-row'));
  const target = rows[Math.max(0, Math.min(rows.length - 1, rows.indexOf(row) + step))];
  event.preventDefault();
  if (target && target !== row) target.querySelector(control.matches('input') ? 'input' : '.finding-select')?.focus();
}
function locationBlock(location) {
  const block = element('div', 'location');
  block.append(element('p', 'location-heading', [location.section || 'В документе', PROPERTIES[location.property] || location.property].filter(Boolean).join(' · ')));
  if (location.fragment_start) {
    block.append(element('p', 'fragment', location.fragment_start));
    const copy = element('button', 'copy', '⧉ Скопировать для поиска в Word'); copy.type = 'button';
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(location.fragment_start); copy.textContent = '✓ Скопировано · Скопировать снова'; $('announcement').textContent = 'Фрагмент скопирован'; }
      catch { copy.textContent = 'Выделите и скопируйте текст вручную'; $('announcement').textContent = 'Копирование недоступно. Выделите текст фрагмента вручную.'; }
    });
    block.append(copy);
  }
  if (location.context) block.append(element('p', 'context', location.context));
  if (!location.fragment_start) block.append(element('p', 'context', 'Для этого места текстовый фрагмент недоступен. Найдите указанный раздел или параметр в Word.'));
  return block;
}
function fillDetail(entry, finding, index, onMark) {
  const detail = $('finding-detail'); detail.replaceChildren();
  const kicker = isFixing(entry) ? `Предыдущий отчёт · ${finding.rule_id}` : `${finding.rule_id} / ${STATUSES[finding.status] || finding.status}`;
  detail.append(element('p', `eyebrow status-${finding.status}`, kicker), element('h2', '', RULES[finding.rule_id] || 'Замечание по оформлению'));
  const comparison = element('div', 'comparison');
  // Frames 10 and 11 put ❌ and ✅ before the labels of an error; other statuses keep plain labels.
  const marks = finding.status === 'error' ? ['❌ ', '✅ '] : ['', ''];
  for (const [label, value, mark] of [['Обнаружено', finding.actual, marks[0]], ['Требуется', finding.expected, marks[1]]]) {
    const column = element('div'); column.append(element('small', '', mark + label), element('p', '', value)); comparison.append(column);
  }
  detail.append(comparison, element('h3', '', `Места в документе · ${finding.locations.length}`));
  if (finding.locations.length) detail.append(locationBlock(finding.locations[0]));
  else detail.append(element('p', 'context', 'Замечание относится к документу в целом.'));
  if (finding.locations.length > 1) {
    const more = element('details', 'more-locations'); more.open = entry.expanded.has(index);
    const summary = element('summary', '', `Остальные места · ${finding.locations.length - 1}`);
    more.append(summary, ...finding.locations.slice(1).map(locationBlock));
    more.addEventListener('toggle', () => { if (more.open) entry.expanded.add(index); else entry.expanded.delete(index); });
    detail.append(more);
  }
  detail.append(element('p', 'reference', `Основание: ${finding.standard_reference}`));
}
export function countText(count, one, few, many) {
  const tens = count % 100, units = count % 10;
  return `${count} ${units === 1 && tens !== 11 ? one : units >= 2 && units <= 4 && (tens < 12 || tens > 14) ? few : many}`;
}
function statusCounts(findings) {
  const count = status => findings.filter(f => f.status === status).length;
  return [countText(count('error'), 'ошибка', 'ошибки', 'ошибок'), countText(count('review'), 'ручная проверка', 'ручные проверки', 'ручных проверок'),
    countText(count('recommendation'), 'рекомендация', 'рекомендации', 'рекомендаций')].join(' · ');
}
// Describes the active conditions, e.g. «Поиск «таблица» и статус «Ошибка» применены вместе.».
function conditionsText({ query, category, status }) {
  const parts = [];
  if (query.trim()) parts.push(['поиск «' + query.trim() + '»', 'применён']);
  if (category !== 'all') parts.push([`категория «${RULES[category] || category}»`, 'применена']);
  if (status !== 'all') parts.push([`статус «${STATUSES[status] || status}»`, 'применён']);
  if (!parts.length) return 'Измените условия или сбросьте их.';
  const capitalize = text => text[0].toLocaleUpperCase('ru') + text.slice(1);
  if (parts.length === 1) return `${capitalize(parts[0][0])} ${parts[0][1]}. Измените условие или сбросьте его.`;
  const names = parts.map(([name]) => name);
  return `${capitalize(names.slice(0, -1).join(', '))} и ${names.at(-1)} применены вместе. Измените условия или сбросьте их.`;
}
export function renderReport(entry, { onSelect, onMark, onMore, mobile }) {
  const { findings, visible, hiddenByMarks } = reportSelection(entry);
  const listKey = JSON.stringify([entry.revision, entry.filters]);
  if (entry.listKey !== listKey || !entry.listLimit) { entry.listKey = listKey; entry.listLimit = LIST_STEP; }
  const selectedPosition = visible.findIndex(item => item.index === entry.selectedFinding);
  if (selectedPosition >= entry.listLimit) entry.listLimit = (Math.floor(selectedPosition / LIST_STEP) + 1) * LIST_STEP;
  const shown = visible.slice(0, entry.listLimit);
  $('count-all').textContent = findings.length;
  for (const status of Object.keys(STATUSES)) $('count-' + status).textContent = findings.filter(f => f.status === status).length;
  const rules = [...new Set(findings.map(f => f.rule_id))].sort();
  if (!rules.includes(entry.filters.category)) entry.filters.category = 'all';
  const options = [element('option', '', 'Категория: Все категории'), ...rules.map(rule => element('option', '', `${rule} · ${RULES[rule] || 'Другое правило'}`))];
  options[0].value = 'all'; rules.forEach((rule, i) => { options[i + 1].value = rule; });
  $('category').replaceChildren(...options); $('category').value = entry.filters.category;
  $('status').value = entry.filters.status; $('search').value = entry.filters.query; $('hide-done').checked = entry.filters.hideDone;
  $('clear-search').hidden = !entry.filters.query;
  $('marked-count').textContent = `${entry.marks.size} из ${findings.length} отмечено вами`;
  $('visible-count').textContent = `Показано ${visible.length} из ${findings.length}`;
  $('list-heading').textContent = `${isFixing(entry) ? 'Предыдущий отчёт' : 'Замечания'} / ${visible.length}`;
  $('storage-warning').hidden = !entry.storageUnavailable;
  $('findings-column').hidden = !visible.length; $('details-slot').hidden = !visible.length;
  $('empty').hidden = Boolean(visible.length);
  $('empty').classList.toggle('empty-clean', !findings.length);
  if (!visible.length) {
    $('empty-kicker').textContent = !findings.length ? 'Проверка завершена' : hiddenByMarks ? 'Подходящие замечания скрыты' : `0 видимых · ${findings.length} найдено в документе`;
    $('empty-title').textContent = !findings.length ? 'В пределах выполненных проверок замечаний не найдено ✅' : hiddenByMarks ? 'Все подходящие замечания отмечены и скрыты' : 'Ничего не найдено по условиям';
    $('empty-text').textContent = !findings.length ? '' : hiddenByMarks ? 'Снимите скрытие, чтобы снова увидеть отмеченные пункты. Ваши отметки и исходные результаты сохранятся.' : conditionsText(entry.filters);
    $('empty-text').hidden = !findings.length;
    $('empty-action').hidden = !findings.length; $('empty-action').textContent = hiddenByMarks ? 'Показать отмеченные' : 'Сбросить поиск и фильтры';
    $('empty-action').dataset.action = hiddenByMarks ? 'show' : 'reset';
    const counts = statusCounts(findings);
    $('empty-summary').textContent = !findings.length ? counts : `${entry.data.current_version === 'corrected' ? 'Отчёт исправленной копии' : 'Исходный отчёт'}: ${countText(findings.length, 'замечание', 'замечания', 'замечаний')} · ${counts}`;
  }
  const signature = JSON.stringify([entry.key, entry.revision, entry.selectedFinding, entry.filters, [...entry.marks], isFixing(entry), mobile, entry.listLimit]);
  const selection = { findings, visible, hiddenByMarks };
  if ($('findings')._signature === signature) return selection;
  const focusKey = document.activeElement?.dataset?.focus;
  // A new report or new search conditions start from the top of the list.
  const scroll = $('findings')._listKey === entry.key + listKey ? $('findings').scrollTop : 0;
  $('findings')._listKey = entry.key + listKey;
  const detail = $('finding-detail');
  const detailSelection = `${entry.key}:${entry.selectedFinding}`;
  const detailScroll = detail._selection === detailSelection ? detail.scrollTop : 0;
  // Keep the single detail panel alive when moving between desktop and inline mobile layout.
  $('details-slot').append($('finding-detail'));
  $('findings').replaceChildren();
  for (const { finding, index } of shown) {
    const current = index === entry.selectedFinding;
    const row = element('div', 'finding-row'); row.classList.toggle('active', current);
    const button = element('button', 'finding-select'); button.type = 'button'; button.dataset.focus = `select-${index}`;
    button.setAttribute('aria-current', current ? 'true' : 'false');
    button.setAttribute('aria-controls', 'finding-detail');
    if (mobile) button.setAttribute('aria-expanded', String(index === entry.selectedFinding));
    button.append(element('strong', '', RULES[finding.rule_id] || finding.rule_id), element('small', `status-${finding.status}`, `${finding.rule_id} · ${STATUSES[finding.status] || finding.status}`));
    const place = placeText(finding);
    if (place) button.append(element('span', 'finding-place', place));
    button.addEventListener('click', () => onSelect(index));
    const mark = checkbox(entry, index, onMark, 'list');
    // Only the selected row is a Tab stop; the other rows are reached with the arrow keys.
    mark.tabIndex = button.tabIndex = current ? 0 : -1;
    row.append(mark, button); $('findings').append(row);
    if (entry.selectedFinding === index) {
      fillDetail(entry, finding, index, onMark);
      if (mobile) row.append($('finding-detail'));
    }
  }
  if (shown.length < visible.length) {
    const rest = visible.length - shown.length;
    const more = element('button', 'more-findings', `Показать ещё ${Math.min(LIST_STEP, rest)} из ${rest}`); more.type = 'button';
    // After the redraw the focus lands on the first of the added rows.
    more.dataset.focus = `select-${visible[shown.length].index}`;
    more.addEventListener('click', () => { entry.listLimit += LIST_STEP; onMore(); });
    $('findings').append(more);
  }
  if (!visible.length) $('finding-detail').replaceChildren();
  $('findings').scrollTop = scroll;
  detail.scrollTop = detailScroll;
  detail._selection = detailSelection;
  $('findings')._signature = signature;
  if (focusKey) {
    const target = [...document.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === focusKey);
    if (target) target.focus({ preventScroll: true });
    else $('hide-done').focus({ preventScroll: true });
  }
  return selection;
}
