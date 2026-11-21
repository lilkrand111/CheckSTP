# Бэкенд CheckSTP

Локальный запуск из корня после установки Python 3.12 и зависимостей из `requirements.lock`:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --log-level critical
```

На новом VPS используйте профиль Compose и проверки хоста из [инструкции](docs/VPS_RUNBOOK.md).

## API

| Запрос | Вход | Успешный ответ |
| --- | --- | --- |
| GET /health | Без параметров | JSON со статусом ok |
| GET /api/session | Cookie, если она уже есть | Подписанная HttpOnly-cookie и JSON с csrf_token, max_upload_bytes, max_concurrent_jobs |
| POST /api/documents | multipart/form-data с одним полем file | 200, JSON исходного отчёта |
| POST /api/documents/fix | Тот же исходный DOCX в поле file | 200, multipart/form-data с текстовым полем result (JSON нового отчёта) и файлом file (исправленный DOCX) |

Оба POST требуют cookie и заголовок X-CSRF-Token. Интерфейс передаёт имя document.docx. Ответ содержит state, current_version, checker_version, ruleset_version, report, warnings и limitations. У report только standard и findings. После полной перепроверки копии браузер разбирает ответ через Response.formData() и предлагает скачать полученный Blob. Старые URL с идентификатором документа больше не обслуживаются. API использует Cache-Control: no-store.

Пример локальной проверки на синтетическом файле (понадобится jq):

```bash
base=http://127.0.0.1:8000
cookies=$(mktemp)
token=$(curl -fsS -c "$cookies" "$base/api/session" | jq -r .csrf_token)
curl -fsS -b "$cookies" -H "X-CSRF-Token: $token" -F 'file=@example.docx;filename=document.docx' "$base/api/documents"
rm "$cookies"
```

## Обработка и ограничения

FastAPI ограничивает запрос до разбора multipart, читает файл в память и передаёт байты дочернему процессу через stdin. Worker проверяет структуру ZIP, обрабатывает DOCX и возвращает результат через stdout. Вывод ошибок worker отключён. Исходник и исправленная копия существуют в отдельных буферах. Обёртки CLI по-прежнему сохраняют файлы по пути, явно указанному оператором.

Все настройки и значения Compose приведены в [.env.example](.env.example). По умолчанию размер файла ограничен 20 МиБ, время приёма 120 с, пауза при загрузке 30 с, обработка 60 с, число одновременно работающих процессов 2. Лимиты приёма и обработки независимы. Предел сериализованного результата одной операции составляет 64 МиБ. Подписанная cookie и CSRF не требуют таблицы сессий. Адрес клиента используется ограничителем запросов только в памяти процесса.

Ошибки возвращают JSON с error.code и error.message. Формат файла проверяется при каждой операции. При ошибке повторной проверки клиент получает только ошибку. Коды 401/403 относятся к сессии, 408 к загрузке, 413 к размеру, 415/422 к файлу, 429 к ограничению частоты, 503 к занятым обработчикам, 504 к тайм-ауту обработки. При 429/503 учитывайте Retry-After.

## Проверка

```bash
.venv/bin/python -m pytest tests -q
node --test tests/frontend*.test.mjs
```
