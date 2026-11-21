import { DocumentAPI } from './api.mjs';
import { retryDelay, operationErrorMessage, PERMANENT_UPLOAD_ERRORS } from './report.mjs';

export const defaultFilters = () => ({ status: 'all', category: 'all', query: '', hideDone: false });
export const isBusy = entry => Boolean(entry?.operation || ['checking', 'fixing'].includes(entry?.data?.state));
export const isFixing = entry => entry?.operation === 'fixing' || entry?.data?.state === 'fixing';
// The server rejected the file itself: sending the same file again cannot help.
export const permanentUploadError = entry => PERMANENT_UPLOAD_ERRORS.includes(entry?.errorStatus);

export class DocumentStore {
  documents = new Map();
  pausedUntil = 0;
  sessionError = '';
  sequence = 0;
  constructor({ api = new DocumentAPI(), changed = () => {} } = {}) {
    this.api = api;
    this.changed = changed;
  }
  get session() { return this.api.session; }
  get paused() { return this.pausedUntil > Date.now(); }
  get activeCount() { return [...this.documents.values()].filter(isBusy).length; }
  async connect() {
    if (this.connecting) return this.connecting;
    this.connecting = (async () => {
      try { await this.api.connect(); this.sessionError = ''; return true; }
      catch { this.api.session = null; this.sessionError = 'Не удалось подключиться к серверу. Проверьте соединение и повторите подключение.'; return false; }
      finally { this.changed(); }
    })();
    try { return await this.connecting; } finally { this.connecting = null; }
  }
  validate(file) {
    if (!file) return 'Выберите документ для проверки.';
    if (!/\.docx$/i.test(file.name)) return 'Нужен файл DOCX. PDF, DOC и скан-копии не подходят.';
    if (!file.size) return 'Файл пуст. Выберите другой документ.';
    if (this.session && file.size > this.session.max_upload_bytes) return `Файл слишком большой. Допустимо до ${new Intl.NumberFormat('ru', { maximumFractionDigits: 1 }).format(this.session.max_upload_bytes / 1048576)} МБ.`;
    return '';
  }
  admissionError() {
    if (!this.session) return 'Нет подключения к серверу. Повторите подключение.';
    if (this.paused) return 'Новые операции временно приостановлены. Дождитесь окончания паузы и повторите действие вручную.';
    if (this.activeCount >= this.session.max_concurrent_jobs) return 'Доступные обработчики заняты вашими документами. Дождитесь завершения одной из операций.';
    return '';
  }
  entry(key, filename) {
    const entry = { key, filename, data: null, operation: null, error: '', marks: new Set(),
      revision: 0, filters: defaultFilters(), selectedFinding: null, expanded: new Set(),
      errorStatus: null, uploadPercent: null, fixSummary: null };
    this.documents.set(key, entry);
    return entry;
  }
  async adopt(entry, data) {
    entry.marks.clear(); entry.expanded.clear(); entry.selectedFinding = null;
    entry.filters = defaultFilters(); entry.revision++;
    entry.data = data; this.changed();
  }
  mark(entry, index, checked) {
    if (isFixing(entry) || !entry.data?.report || !Number.isInteger(index) || index < 0 || index >= entry.data.report.findings.length) return;
    if (checked) entry.marks.add(index); else entry.marks.delete(index);
    this.changed();
  }
  async error(entry, error) {
    entry.errorStatus = error.status ?? null;
    entry.error = operationErrorMessage(error);
    if (error instanceof TypeError) entry.error = 'Соединение прервалось. Повторите действие вручную.';
    if ([401, 403].includes(error.status)) {
      const connected = await this.connect();
      entry.error = connected ? 'Сессия обновлена. Повторите действие вручную.' : this.sessionError;
    }
    if ([429, 503].includes(error.status)) {
      const seconds = retryDelay(error);
      this.pausedUntil = Math.max(this.pausedUntil, Date.now() + seconds * 1000);
      entry.error += ` Повторите действие не раньше чем через ${seconds} сек.`;
      clearTimeout(this.pauseTimer);
      const wake = () => {
        this.changed();
        if (this.paused) this.pauseTimer = setTimeout(wake, Math.min(this.pausedUntil - Date.now() + 20, 2147483647));
      };
      this.pauseTimer = setTimeout(wake, Math.min(this.pausedUntil - Date.now() + 20, 2147483647));
    }
  }
  upload(file) {
    const problem = this.validate(file) || this.admissionError();
    if (problem) throw new Error(problem);
    const entry = this.entry(`upload-${++this.sequence}`, file.name);
    entry.file = file; entry.operation = 'checking';
    entry.done = (async () => {
      const progress = fraction => {
        const percent = Math.min(100, Math.floor(fraction * 100));
        if (percent !== entry.uploadPercent) { entry.uploadPercent = percent; this.changed(); }
      };
      try { await this.adopt(entry, await this.api.upload(file, progress)); }
      catch (error) { await this.error(entry, error); }
      finally { entry.operation = null; this.changed(); }
    })();
    this.changed();
    return entry;
  }
  async fix(entry) {
    if (!entry?.data?.report || isBusy(entry) || entry.data.current_version === 'corrected') return;
    const problem = this.admissionError();
    if (problem) { entry.error = problem; this.changed(); return; }
    entry.operation = 'fixing'; entry.error = ''; this.changed();
    try {
      const before = entry.data.report.findings.length;
      await this.adopt(entry, await this.api.fix(entry.file));
      // Counts of findings in the two reports, not a number of applied operations.
      if (Number.isInteger(before) && entry.data.current_version === 'corrected' && entry.data.report) entry.fixSummary = { before, after: entry.data.report.findings.length };
    } catch (error) {
      await this.error(entry, error);
    } finally { entry.operation = null; this.changed(); }
  }
  dispose() {
    clearTimeout(this.pauseTimer);
  }
}
