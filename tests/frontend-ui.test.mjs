// State transitions used by the UI; all documents and network responses are synthetic.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DocumentStore, isBusy, permanentUploadError } from '../frontend/assets/documents.mjs';
import { DocumentAPI } from '../frontend/assets/api.mjs';
import { reportSelection } from '../frontend/assets/views.mjs';

const findings = [
  { rule_id: 'F03', status: 'error', actual: '1,5', expected: '1,0', standard_reference: 'Уточнение', locations: [{ fragment_start: 'Первый абзац' }] },
  { rule_id: 'F02', status: 'review', actual: 'Шрифт', expected: 'Проверить', standard_reference: '2.1', locations: [] },
];
const data = (id = 'one', version = 'original') => ({ document_id: id, filename: `${id}.docx`, state: version === 'original' ? 'checked' : 'fixed', current_version: version, checker_version: '1', ruleset_version: '1', report: { standard: 'СТП 01–2017', findings: structuredClone(findings) }, warnings: [], limitations: [], corrected: version === 'corrected' ? new Blob(['copy']) : null });
const file = () => new File(['synthetic'], 'test.docx');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function setup(overrides = {}, storage) {
  const memory = new Map();
  const api = { session: { max_upload_bytes: 1024, max_concurrent_jobs: 2 }, async connect() { return this.session; }, async upload() { return data(); }, async get(id) { return data(id); }, async fix(id) { return data(id, 'corrected'); }, ...overrides };
  return new DocumentStore({ api, storage: storage || { getItem: key => memory.get(key), setItem: (key, value) => memory.set(key, value) } });
}

test('parallel uploads respect admission; completion does not replace another document', async () => {
  const first = deferred(), second = deferred(); let calls = 0;
  const store = setup({ upload: () => (++calls === 1 ? first : second).promise });
  const a = store.upload(file()), b = store.upload(file());
  assert.equal(store.activeCount, 2);
  assert.throws(() => store.upload(file()), /обработчики заняты/);
  second.resolve(data('two')); await b.done;
  assert.equal(b.data.document_id, 'two'); assert.equal(isBusy(a), true);
  first.resolve(data('one')); await a.done;
  assert.equal(store.documents.size, 2); assert.equal(a.data.document_id, 'one');
  assert.equal(store.activeCount, 0); store.dispose();
});

test('429/503 pause all new mutations, allow reads, and never replay POST after time expires', async () => {
  for (const status of [429, 503]) {
    let uploads = 0, fixes = 0, reads = 0;
    const store = setup({ async upload() { uploads++; throw Object.assign(new Error('Ограничение сервера'), { status, retryAfter: 3600 }); }, async get(id) { reads++; return data(id); }, async fix() { fixes++; return data('one', 'corrected'); } });
    try {
      const checked = store.entry('one', 'one.docx'); await store.adopt(checked, data());
      const failed = store.upload(file()); await failed.done;
      assert.equal(store.paused, true); assert.match(failed.error, /3600/);
      assert.throws(() => store.upload(file()), /приостановлены/);
      await store.fix(checked); assert.equal(fixes, 0);
      assert.ok(checked.data.report); assert.equal(reads, 0);
      store.pausedUntil = Date.now() - 1;
      assert.equal(store.admissionError(), ''); assert.equal(uploads, 1); assert.equal(fixes, 0);
      await store.fix(checked); assert.equal(fixes, 1);
    } finally { store.dispose(); }
  }
});

test('manual marks survive the same report and failed fix, but reset for the corrected report', async () => {
  let shouldFail = true;
  const store = setup({ async fix() { if (shouldFail) throw new TypeError('offline'); return data('one', 'corrected'); } });
  const entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  store.mark(entry, 0, true); entry.filters.query = 'Первый'; entry.selectedFinding = 0;
  await store.fix(entry);
  assert.deepEqual([...entry.marks], [0]); assert.equal(entry.filters.query, 'Первый'); assert.match(entry.error, /Соединение/);
  shouldFail = false; await store.fix(entry);
  assert.equal(entry.data.current_version, 'corrected'); assert.equal(entry.marks.size, 0); assert.equal(entry.filters.query, '');
  assert.ok(entry.data.corrected instanceof Blob); store.dispose();
});

test('fix remembers report sizes; a rejected file is not offered for a retry', async () => {
  const store = setup({ async fix(id) { return { ...data(id, 'corrected'), report: { standard: 'СТП 01–2017', findings: [findings[1]] } }; } });
  const entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  assert.equal(entry.fixSummary, null);
  await store.fix(entry);
  assert.deepEqual(entry.fixSummary, { before: 2, after: 1 });
  for (const [status, permanent] of [[413, true], [415, true], [422, true], [503, false], [undefined, false]]) {
    const rejecting = setup({ async upload() { throw Object.assign(status ? new Error('Отказ') : new TypeError('offline'), { status }); } });
    const failed = rejecting.upload(file()); await failed.done;
    assert.equal(permanentUploadError(failed), permanent); assert.ok(failed.file); rejecting.dispose();
  }
  const percents = [];
  const progress = setup({ async upload(_, report) { for (const part of [0, 0.004, 0.5, 0.509, 1]) report(part); return data(); } });
  progress.changed = () => percents.push([...progress.documents.values()][0]?.uploadPercent);
  await progress.upload(file()).done;
  // The screen is redrawn once per whole percent, not for every network event.
  assert.deepEqual(percents.filter((value, index) => value !== percents[index - 1]), [0, 50, 100]);
  store.dispose(); progress.dispose();
});

test('fix resubmits the original File and ignores duplicate clicks', async () => {
  const original = file(), fixed = deferred(); let calls = 0;
  const store = setup({ fix(value) { assert.equal(value, original); calls++; return fixed.promise; } });
  const entry = store.upload(original); await entry.done;
  const first = store.fix(entry); await store.fix(entry);
  fixed.resolve(data('one', 'corrected')); await first; await store.fix(entry);
  assert.equal(calls, 1); store.dispose();
});

test('uncertain upload is retained for explicit retry and is never repeated automatically', async () => {
  let calls = 0;
  const store = setup({ async upload() { calls++; throw new TypeError('offline'); } });
  const entry = store.upload(file()); await entry.done;
  assert.match(entry.error, /Повторите действие вручную/);
  assert.ok(entry.file); assert.equal(calls, 1); assert.equal(isBusy(entry), false); store.dispose();
});

test('empty report, no search matches, and hidden matches remain distinct; selection follows visibility', async () => {
  const store = setup(), entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  assert.equal(reportSelection(entry).visible.length, 2); assert.equal(entry.selectedFinding, 0);
  store.mark(entry, 0, true); entry.filters.hideDone = true;
  assert.equal(reportSelection(entry).visible.length, 1); assert.equal(entry.selectedFinding, 1);
  entry.filters.category = 'F03'; assert.equal(reportSelection(entry).hiddenByMarks, true); assert.equal(entry.selectedFinding, null);
  entry.filters.query = 'missing'; assert.equal(reportSelection(entry).hiddenByMarks, false);
  await store.adopt(entry, { ...data(), report: { standard: 'СТП', findings: [] } });
  assert.equal(reportSelection(entry).findings.length, 0); store.dispose();
});

test('check and fix send a neutral filename and return the corrected bytes to the tab', async () => {
  const saved = globalThis.fetch, calls = [];
  globalThis.fetch = async (path, init = {}) => {
    calls.push({ path, init });
    if (path.endsWith('/fix')) {
      const parts = new FormData();
      parts.append('result', JSON.stringify(data('one', 'corrected')));
      parts.append('file', new Blob(['synthetic-copy']), 'corrected.docx');
      return new Response(parts);
    }
    return Response.json(path === '/api/session' ? { csrf_token: 'token' } : data());
  };
  try {
    const api = new DocumentAPI(); await api.connect(); await api.upload(file());
    const result = await api.fix(file());
    assert.equal(await result.corrected.text(), 'synthetic-copy');
    assert.deepEqual(calls.map(call => call.path), ['/api/session', '/api/documents', '/api/documents/fix']);
    for (const { init } of calls.slice(1)) {
      assert.equal(init.body.get('file').name, 'document.docx');
      assert.equal(init.credentials, 'same-origin');
      assert.equal(new Headers(init.headers).get('X-CSRF-Token'), 'token');
    }
  } finally { globalThis.fetch = saved; }
});

test('actual app separates file choice from POST, preserves the selected document and restores URLs', async () => {
  const { readFile } = await import('node:fs/promises');
  const { setImmediate } = await import('node:timers/promises');
  const saved = new Map(['document', 'window', 'location', 'history', 'fetch', 'setTimeout', 'clearTimeout'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const elements = new Map();
  class Element {
    constructor() { this.children = []; this.listeners = new Map(); this.dataset = {}; this.value = ''; this.hidden = false; this.scrollTop = 0; this.classList = { toggle() {}, add() {}, remove() {} }; }
    setAttribute() {}
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    get parentElement() { return this.parent; }
    append(...nodes) { for (const node of nodes) { node.remove?.(); node.parent = this; this.children.push(node); } }
    replaceChildren(...nodes) { for (const child of this.children) child.parent = null; this.children = []; this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); this.parent = null; }
    focus() { document.activeElement = this; }
    scrollIntoView() {}
    click() {}
    querySelector() { return new Element(); }
    async fire(name, event = {}) { await this.listeners.get(name)?.(event); for (let i = 0; i < 15; i++) await setImmediate(); }
  }
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const location = { href: 'http://localhost/' }, events = new Map(), pending = deferred();
  const requests = [], uploaded = data('00000000-0000-4000-8000-000000000001'); let timer = 0;
  const long = { ...data('00000000-0000-4000-8000-000000000002'), report: { standard: 'СТП 01–2017', findings: Array.from({ length: 450 }, (_, i) => ({ ...findings[0], locations: [{ section: 'Раздел', fragment_start: `Абзац\n${i}` }] })) } };
  try {
    Object.assign(globalThis, {
      document: { body: new Element(), activeElement: null, getElementById: get, createElement: () => new Element(),
        querySelectorAll: selector => selector === '[data-check]' ? [get('new-document')] : [] },
      window: { localStorage: { getItem: () => null, setItem() {} }, matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener: (name, fn) => events.set(name, fn), scrollTo() {} },
      location, history: { pushState(_, __, url) { location.href = String(url); }, replaceState(_, __, url) { location.href = String(url); } },
      setTimeout() { return ++timer; }, clearTimeout() {},
      async fetch(path, init = {}) {
        requests.push({ path, method: init.method || 'GET' });
        if (path === '/api/session') return new Response(JSON.stringify({ csrf_token: 'token', max_upload_bytes: 1024, max_concurrent_jobs: 2 }));
        if (path === '/api/documents') return new Response(JSON.stringify(requests.filter(r => r.method === 'POST').length === 1 ? await pending.promise : long), { status: 201 });
        throw new Error('Unexpected request ' + path);
      },
    });
    let source = await readFile(new URL('../frontend/assets/app.js', import.meta.url), 'utf8');
    for (const file of ['documents.mjs', 'views.mjs']) source = source.replace(new RegExp(`'\\./${file}(?:\\?[^']*)?'`), JSON.stringify(new URL(`../frontend/assets/${file}`, import.meta.url).href));
    await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    assert.equal(get('landing').hidden, false);
    assert.equal(get('start-check').disabled, true);
    await get('file-input').fire('change', { target: { files: [file()], value: '' } });
    assert.equal(get('start-check').disabled, false); assert.equal(requests.filter(r => r.method === 'POST').length, 0);
    assert.equal(get('landing').hidden, false);
    assert.equal(location.href, 'http://localhost/');
    const replacement = new File(['replacement'], 'другая курсовая.docx');
    await get('hero').fire('drop', { preventDefault() {}, dataTransfer: { types: ['Files'], files: [replacement] } });
    assert.equal(get('selected-filename').textContent, replacement.name);
    assert.equal(requests.filter(r => r.method === 'POST').length, 0);
    location.href = 'http://localhost/#check'; await events.get('hashchange')();
    assert.equal(get('landing').hidden, false);
    assert.equal(get('feedback').parentElement, get('check-messages'));
    await get('start-check').fire('click');
    assert.equal(requests.filter(r => r.method === 'POST').length, 1); assert.equal(get('report').hidden, true);
    assert.match(get('document-title').textContent, /Проверяем/);
    // Navigate back while the request runs: its response must not reopen the report.
    location.href = 'http://localhost/'; await events.get('popstate')();
    pending.resolve(uploaded); for (let i = 0; i < 15; i++) await setImmediate();
    assert.equal(get('landing').hidden, false); assert.equal(location.href, 'http://localhost/');
    location.href = 'http://localhost/#report=upload-1'; await events.get('popstate')(); for (let i = 0; i < 15; i++) await setImmediate();
    assert.equal(get('report').hidden, false); assert.equal(get('count-all').textContent, 2);
    assert.equal(get('document-name').textContent, replacement.name); assert.equal(document.title, `${replacement.name} · NormaCheck`);
    // Each row names its place, so equal rules stay distinguishable.
    assert.equal(get('findings').children[0].children[1].children[2].textContent, 'Первый абзац');
    assert.equal(get('findings').children[1].children[1].children.length, 2);
    assert.equal(new URL(location.href).searchParams.size, 0);
    get('finding-detail').scrollTop = 120;
    const firstMark = get('findings').children[0].children[0];
    firstMark.checked = true; await firstMark.fire('change');
    assert.equal(get('finding-detail').scrollTop, 120);
    await get('findings').children[1].children[1].fire('click');
    assert.equal(get('finding-detail').scrollTop, 0);
    await get('search').fire('input', { target: { value: 'неизвестный фрагмент' } });
    assert.equal(get('empty-title').textContent, 'Ничего не найдено по условиям');
    assert.equal(get('empty-text').textContent, 'Поиск «неизвестный фрагмент» применён. Измените условие или сбросьте его.');
    assert.equal(get('empty-summary').textContent, 'Исходный отчёт: 2 замечания · 1 ошибка · 1 ручная проверка · 0 рекомендаций');
    await get('reset-filters').fire('click'); assert.equal(get('empty').hidden, true);
    location.href = 'http://localhost/#report=upload-1'; await events.get('popstate')();
    assert.equal(requests.at(-1).path, "/api/documents");
    // A long report is drawn in portions of 200 rows.
    await get('new-document').fire('click', { preventDefault() {} });
    await get('file-input').fire('change', { target: { files: [file()], value: '' } });
    await get('start-check').fire('click');
    assert.equal(get('count-all').textContent, 450); assert.equal(get('findings').children.length, 201);
    assert.equal(get('findings').children[0].children[1].children[2].textContent, 'Абзац 0');
    assert.equal(get('findings').children.at(-1).textContent, 'Показать ещё 200 из 250');
    await get('findings').children.at(-1).fire('click');
    assert.equal(get('findings').children.length, 401); assert.equal(get('findings').children.at(-1).textContent, 'Показать ещё 50 из 50');
    await get('findings').children.at(-1).fire('click');
    assert.equal(get('findings').children.length, 450);
    await get('search').fire('input', { target: { value: 'Абзац' } });
    assert.equal(get('findings').children.length, 201);
    await get('new-document').fire('click', { preventDefault() {} });
    assert.equal(location.href, 'http://localhost/#check');
    assert.equal(get('landing').hidden, false);
    assert.equal(document.activeElement, get('check'));
    location.href = 'http://localhost/?document=invalid'; await events.get('popstate')();
    assert.equal(location.href, 'http://localhost/#check');
    assert.match(get('global-message').textContent, /Выберите DOCX заново/);
    assert.equal(requests.filter(r => r.method === 'POST').length, 2);
  } finally {
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});
