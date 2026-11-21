import { DocumentStore, isBusy, isFixing, defaultFilters, permanentUploadError } from './documents.mjs';
import { $, element, notice, renderReport, countText, moveListFocus } from './views.mjs?v=20261111.1';

let storage;
try { storage = window.localStorage; } catch { storage = null; }
const store = new DocumentStore({ storage, changed: render });
let selectedKey = null;
let selectedFile = null;
let mode = 'landing';
let uploadError = '';
let navigationSequence = 0;
const mobile = window.matchMedia('(max-width: 650px)');
// Touch screens have no drag-and-drop from a file manager.
const coarse = window.matchMedia('(pointer: coarse)');
const baseTitle = document.title;
const current = () => store.documents.get(selectedKey);
const limitText = () => store.session ? `DOCX · до ${new Intl.NumberFormat('ru', { maximumFractionDigits: 1 }).format(store.session.max_upload_bytes / 1048576)} МБ` : 'DOCX';

function navigate(view, entry = null, push = true) {
  navigationSequence++;
  mode = view === 'check' ? 'landing' : view;
  selectedKey = entry?.key || null;
  const url = new URL(location.href); url.searchParams.delete('document'); url.hash = '';
  if (view === 'check') url.hash = 'check';
  if (view === 'report' && entry?.data?.document_id) url.searchParams.set('document', entry.data.document_id);
  else if (view === 'report' && entry) url.hash = `pending=${entry.key}`;
  if (url.href !== location.href) history[push ? 'pushState' : 'replaceState'](null, '', url);
  render();
  if (view === 'check') {
    $('check').scrollIntoView();
    $('check').focus({ preventScroll: true });
  } else {
    window.scrollTo({ top: 0, behavior: 'instant' });
    if (view === 'report') $('document-title').focus({ preventScroll: true });
  }
}
async function restoreRoute() {
  const sequence = ++navigationSequence;
  const url = new URL(location.href), id = url.searchParams.get('document');
  if (id) {
    mode = 'report';
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
      uploadError = 'В адресе указан некорректный идентификатор документа. Выберите DOCX заново.';
      navigate('check', null, false);
      return;
    }
    const entry = await store.open(id);
    if (sequence !== navigationSequence) return;
    selectedKey = entry.key; render();
    if (store.session) await store.refresh(entry);
    return;
  }
  const pendingRoute = url.hash.startsWith('#pending=');
  const pending = pendingRoute ? store.documents.get(url.hash.slice(9)) : null;
  if (pendingRoute && !pending) {
    navigate('check', null, false);
    return;
  }
  selectedKey = pending?.key || null;
  mode = pending ? 'report' : 'landing';
  render();
  if (mode === 'landing' && url.hash) document.getElementById(url.hash.slice(1))?.scrollIntoView();
}
function choose(file) {
  if (!file) return;
  uploadError = store.validate(file);
  selectedFile = uploadError ? null : file;
  render();
}
function start(file = selectedFile) {
  try {
    const entry = store.upload(file); selectedFile = null; uploadError = ''; navigate('report', entry);
  } catch (error) { uploadError = error.message; render(); }
}
function render() {
  const entry = current(), data = entry?.data;
  const reportShown = mode === 'report' && Boolean(data?.report);
  const landing = mode === 'landing';
  $('landing').hidden = !landing; $('application').hidden = landing;
  $('application').classList.toggle('report-visible', reportShown);
  $('application').classList.toggle('report-waiting', mode === 'report' && Boolean(entry) && !data?.report);
  $('application').classList.remove('report-empty', 'report-clean', 'report-fixing', 'no-findings');
  const name = mode === 'report' ? data?.filename || entry?.file?.name || '' : '';
  document.title = name ? `${name} · NormaCheck` : baseTitle;
  $('document-name').textContent = name; $('document-name').hidden = !reportShown || !name;
  $('workspace').hidden = landing;
  // The waiting screen shows the standard badge. A ready report offers a new upload.
  $('new-document').hidden = !reportShown; $('standard-badge').hidden = reportShown;
  const feedback = $('feedback'), feedbackTarget = $(landing ? 'check-messages' : 'report-messages');
  if (feedback.parentElement !== feedbackTarget) feedbackTarget.append(feedback);
  const fileError = selectedFile ? store.validate(selectedFile) : '';
  const selectionError = landing ? uploadError || fileError : '';
  notice('global-message', selectionError || store.sessionError, true);
  $('session-retry').hidden = !store.sessionError;
  notice('pause-message', store.paused ? 'Новые загрузки и автоисправление временно приостановлены по ограничению сервера. После паузы повторите действие вручную. Просмотр и скачивание доступны.' : '');
  notice('busy-message', store.session && store.activeCount >= store.session.max_concurrent_jobs && !store.paused ? 'Обработчики заняты документами этой вкладки. Новую проверку или исправление можно запустить после завершения одной из операций. Просмотр отчётов доступен.' : '');
  $('upload-limit').textContent = store.session ? limitText() : 'DOCX · Лимит будет доступен после подключения';
  $('upload-zone').classList.toggle('has-file', Boolean(selectedFile));
  $('selected-filename').hidden = !selectedFile; $('selected-filename').textContent = selectedFile?.name || '';
  $('upload-label').textContent = selectedFile ? 'Сменить' : 'Выбрать DOCX';
  $('upload-subtitle').textContent = coarse.matches ? 'Можно выбрать другой DOCX.' : selectedFile ? 'Выберите другой DOCX или перетащите его в это окно.' : 'или перетащите документ в это окно';
  $('upload-subtitle').hidden = coarse.matches && !selectedFile;
  $('start-check').disabled = !selectedFile || Boolean(fileError) || Boolean(store.admissionError());
  if (!entry || mode !== 'report') return;
  if (data?.document_id && new URL(location.href).searchParams.get('document') !== data.document_id) {
    const url = new URL(location.href); url.hash = ''; url.searchParams.set('document', data.document_id); history.replaceState(null, '', url);
  }
  const busy = isBusy(entry), fixing = isFixing(entry), corrected = data?.current_version === 'corrected', report = data?.report;
  // A file the server rejected needs another file, not a retry of the same one.
  const permanent = !busy && !report && permanentUploadError(entry);
  const uploading = entry.operation === 'checking' && Number.isInteger(entry.uploadPercent) && entry.uploadPercent < 100;
  $('document-title').textContent = fixing ? 'Исправляем и повторно проверяем' : report ? corrected ? 'Отчёт исправленной копии' : 'Отчёт исходного документа' : busy ? 'Проверяем документ' : permanent ? 'Нужен другой файл' : 'Отчёт пока недоступен';
  $('document-state').textContent = !report && busy ? 'Проверяем оформление по СТП 01–2017. Результат появится после завершения проверки.' : fixing ? 'Предыдущий отчёт сохранён. Результат появится после завершения всей операции.' : entry.refreshing ? 'Обновляем статус…' : corrected ? 'Поддерживаемые исправления применены. Копия повторно проверена.' : '';
  notice('document-message', entry.error || data?.last_error?.message || '', true);
  $('report').hidden = !report; $('report-counts').hidden = !report;
  $('waiting').hidden = Boolean(report); $('waiting-spinner').hidden = !busy;
  $('waiting-title').textContent = uploading ? `Передаём файл: ${entry.uploadPercent}\u00a0%` : busy ? 'Проверка выполняется' : permanent ? 'Проверка этого файла остановлена' : 'Проверка не дала доступного отчёта';
  $('waiting-filename').textContent = entry.filename;
  $('waiting-meta').textContent = `${limitText()}   /   СТП 01–2017`;
  $('waiting-button').hidden = !busy;
  $('waiting-note').textContent = busy ? 'Результат откроется на этой странице. Исходный файл не меняется.' : permanent ? 'Причина указана выше. Выберите другой DOCX или сохраните документ заново в Word.' : 'Используйте кнопки выше. Загрузка повторяется только вручную.';
  $('refresh').hidden = !data || (!entry.error && !busy && data.state !== 'failed');
  $('refresh').disabled = Boolean(entry.refreshing || entry.operation || !store.session);
  $('retry-upload').hidden = !entry.file || busy || permanent;
  $('retry-upload').disabled = Boolean(store.admissionError());
  $('choose-again').hidden = busy || Boolean(report);
  // The only remaining action becomes the main one.
  $('choose-again').classList.toggle('lime', $('retry-upload').hidden); $('choose-again').classList.toggle('glass', !$('retry-upload').hidden);
  $('recovery-actions').hidden = ['refresh', 'retry-upload', 'choose-again'].every(id => $(id).hidden);
  if (!report) return;
  $('application').classList.toggle('report-fixing', fixing);
  $('application').classList.toggle('no-findings', !report.findings.length);
  // Sizes of the two reports; the API gives no number of applied operations.
  const fixSummary = corrected && entry.fixSummary ? ` Было ${countText(entry.fixSummary.before, 'замечание', 'замечания', 'замечаний')}, осталось ${entry.fixSummary.after}.` : '';
  $('actions-title').textContent = fixing ? 'Исправляем и повторно проверяем' : corrected ? 'Исправленная копия' : entry.error ? 'Автоисправление не завершено' : report.findings.length ? 'Исправить оформление' : 'Получить проверенную копию';
  $('actions-description').textContent = corrected ? 'Поддерживаемые правки применены. Копия повторно проверена.' + fixSummary : fixing ? 'Применяем поддерживаемые правки к копии и проверяем её заново. Дождитесь завершения всей операции.' : 'Поддерживаемые правки применятся ко всему документу, не к карточке.';
  $('fix-spinner').hidden = !fixing;
  $('fix').disabled = busy || corrected || entry.refreshing || Boolean(store.admissionError());
  $('fix').textContent = fixing ? 'Операция выполняется…' : corrected ? 'Автоисправление применено' : !report.findings.length ? 'Создать проверенную копию' : 'Автоисправить документ';
  $('action-note').textContent = fixing ? 'Исходный отчёт и ваши отметки сохранены.' : corrected ? 'Оставшиеся замечания исправьте в Word.' : 'Исходный файл не меняется';
  $('download').disabled = !data.download_url || entry.downloading;
  $('download').classList.toggle('lime', Boolean(data.download_url)); $('download').classList.toggle('secondary', !data.download_url);
  $('download').textContent = entry.downloading ? 'Получаем файл…' : 'Скачать исправленную копию';
  $('download-note').textContent = data.download_url ? 'Копия готова к скачиванию' : fixing ? 'Доступно после повторной проверки.' : 'Скачивание после повторной проверки. Оставшиеся замечания не мешают.';
  $('download-status').textContent = entry.downloaded ? '✓ Скачивание началось' : '';
  const { visible } = renderReport(entry, { mobile: mobile.matches, onSelect: index => { entry.selectedFinding = index; render(); }, onMark: (index, checked) => store.mark(entry, index, checked), onMore: render });
  // Empty list states follow frames 06 and 07: the list and detail give way to one card.
  const empty = !visible.length, clean = !report.findings.length && !corrected && !busy;
  $('application').classList.toggle('report-empty', empty); $('application').classList.toggle('report-clean', empty && clean);
  $('document-actions').hidden = empty && clean;
  if (empty && !corrected && !busy && !entry.error) $('download-note').textContent = 'Доступно после повторной проверки';
}
async function download() {
  const entry = current();
  if (!entry?.data?.download_url || entry.downloading) return;
  entry.downloading = true; entry.downloaded = false; entry.error = ''; render();
  try {
    const { blob, name } = await store.api.download(entry.data), url = URL.createObjectURL(blob), link = element('a');
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000); entry.downloaded = true;
  } catch (error) { await store.error(entry, error); }
  finally { entry.downloading = false; render(); }
}
function resetFilters() { const entry = current(); if (entry) { entry.filters = defaultFilters(); render(); } }
for (const node of document.querySelectorAll('[data-check]')) node.addEventListener('click', event => { event.preventDefault(); uploadError = ''; navigate('check'); });
for (const node of document.querySelectorAll('[data-home]')) node.addEventListener('click', event => { event.preventDefault(); navigate('landing'); });
$('file-input').addEventListener('change', event => { choose(event.target.files[0]); event.target.value = ''; });
$('start-check').addEventListener('click', () => start());
$('retry-upload').addEventListener('click', () => start(current()?.file));
$('session-retry').addEventListener('click', async () => { if (await store.connect()) await restoreRoute(); });
$('fix').addEventListener('click', () => store.fix(current())); $('download').addEventListener('click', download);
$('refresh').addEventListener('click', () => store.refresh(current()));
$('search').addEventListener('input', event => { if (current()) { current().filters.query = event.target.value; render(); } });
$('clear-search').addEventListener('click', () => { if (current()) { current().filters.query = ''; render(); $('search').focus(); } });
$('category').addEventListener('change', event => { if (current()) { current().filters.category = event.target.value; render(); } });
$('status').addEventListener('change', event => { if (current()) { current().filters.status = event.target.value; render(); } });
$('hide-done').addEventListener('change', event => { if (current()) { current().filters.hideDone = event.target.checked; render(); } });
$('reset-filters').addEventListener('click', resetFilters);
$('empty-action').addEventListener('click', () => { if ($('empty-action').dataset.action === 'show' && current()) { current().filters.hideDone = false; render(); } else resetFilters(); $('search').focus(); });
const hero = $('hero'), dropTarget = $('check');
let dragDepth = 0;
const draggingFiles = event => event.dataTransfer?.types?.includes('Files');
hero.addEventListener('dragenter', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault(); dragDepth++; dropTarget.classList.add('dragging');
});
hero.addEventListener('dragleave', event => {
  if (!draggingFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropTarget.classList.remove('dragging');
});
hero.addEventListener('drop', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault(); dragDepth = 0; dropTarget.classList.remove('dragging');
  if (event.dataTransfer.files.length === 1) choose(event.dataTransfer.files[0]);
  else {
    selectedFile = null;
    uploadError = 'Перетащите один DOCX-файл. Следующий можно загрузить отдельно.';
    render();
  }
});
window.addEventListener('dragover', event => { if (draggingFiles(event)) event.preventDefault(); });
window.addEventListener('drop', event => { if (draggingFiles(event)) event.preventDefault(); });
window.addEventListener('popstate', restoreRoute); window.addEventListener('hashchange', restoreRoute);
mobile.addEventListener('change', render); coarse.addEventListener('change', render);
$('findings').addEventListener('keydown', moveListFocus);
window.addEventListener('beforeunload', event => {
  // Until the upload answers, the document has no address to come back to.
  if ([...store.documents.values()].some(entry => entry.operation === 'checking')) { event.preventDefault(); event.returnValue = ''; }
});
await restoreRoute();
if (await store.connect()) {
  const entry = current();
  if (mode === 'report' && entry?.data?.document_id) await store.refresh(entry);
}

$('example-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText('Система выполняет проверку документации…'); $('announcement').textContent = 'Фрагмент примера скопирован'; }
  catch { $('announcement').textContent = 'Копирование недоступно. Выделите текст примера вручную.'; }
});
