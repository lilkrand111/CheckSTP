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
    body.append('file', file, 'document.docx');
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
  async fix(file) {
    const body = new FormData();
    body.append('file', file, 'document.docx');
    const response = await fetch('/api/documents/fix', {
      method: 'POST', body, credentials: 'same-origin',
      headers: { 'X-CSRF-Token': this.session.csrf_token },
    });
    if (!response.ok) await readResponse(response);
    const parts = await response.formData();
    const result = JSON.parse(parts.get('result'));
    const corrected = parts.get('file');
    if (!result?.report || result.current_version !== 'corrected' || !(corrected instanceof Blob) || !corrected.size) {
      throw new Error('Не удалось получить проверенную копию. Повторите исправление.');
    }
    return { ...result, corrected };
  }
}
