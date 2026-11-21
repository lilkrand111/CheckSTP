import { readResponse } from './report.mjs';

export class DocumentAPI {
  session = null;
  async connect() {
    this.session = await this.request('/api/session');
    return this.session;
  }
  async request(path, init = {}) {
    const headers = new Headers(init.headers);
    if (init.method === 'POST') {
      if (!this.session) throw new Error('Подключитесь к серверу перед запуском проверки.');
      headers.set('X-CSRF-Token', this.session.csrf_token);
    }
    return readResponse(await fetch(path, { ...init, headers, credentials: 'same-origin' }));
  }
  upload(file, onProgress = () => {}) {
    const body = new FormData();
    body.append('file', file, file.name);
    if (typeof XMLHttpRequest !== 'function') return this.request('/api/documents', { method: 'POST', body });
    // fetch cannot report how much of the body has been sent; XMLHttpRequest can.
    return new Promise((resolve, reject) => {
      if (!this.session) throw new Error('Подключитесь к серверу перед запуском проверки.');
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/documents');
      xhr.setRequestHeader('X-CSRF-Token', this.session.csrf_token);
      xhr.upload.addEventListener('progress', event => { if (event.lengthComputable && event.total) onProgress(event.loaded / event.total); });
      xhr.upload.addEventListener('load', () => onProgress(1));
      xhr.addEventListener('load', () => readResponse({
        ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status,
        text: async () => xhr.responseText, headers: { get: name => xhr.getResponseHeader(name) },
      }).then(resolve, reject));
      // The same error type as a failed fetch: the caller treats it as an uncertain upload.
      for (const name of ['error', 'abort', 'timeout']) xhr.addEventListener(name, () => reject(new TypeError('Upload connection failed')));
      xhr.send(body);
    });
  }
  path(id) { return `/api/documents/${encodeURIComponent(id)}`; }
  get(id) { return this.request(this.path(id)); }
  fix(id) { return this.request(`${this.path(id)}/fix`, { method: 'POST' }); }
  async download(data) {
    if (data.download_url !== `${this.path(data.document_id)}/download`) throw new Error('Ссылка на проверенную копию недоступна. Обновите статус.');
    const response = await fetch(data.download_url, { credentials: 'same-origin' });
    if (!response.ok) await readResponse(response);
    const encoded = (response.headers.get('Content-Disposition') || '').match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    let name = 'исправлено.docx';
    try { if (encoded) name = decodeURIComponent(encoded); } catch { /* Safe fallback. */ }
    return { name, blob: await response.blob() };
  }
}
