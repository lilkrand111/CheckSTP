# Контейнеры NormaCheck

Compose запускает FastAPI и Caddy. Наружу опубликованы только порты Caddy.
Uvicorn доверяет заголовкам прокси только от адреса PROXY_IP в выделенной Docker-сети.
При конфликте подсети поменяйте DOCKER_SUBNET, APP_IP и PROXY_IP вместе.
Приложение запускается от UID 10001 с файловой системой только для чтения.
Данные TLS и конфигурация Caddy находятся в отдельных томах.

## Профиль памяти и журналов

| Параметр | Начальное значение |
| --- | --- |
| RAM приложения | 640 МиБ |
| RAM прокси | 96 МиБ |
| Число обработчиков | 2 |
| Максимальный исходник | 20 МиБ |
| Тайм-аут обработки | 60 с |

У обоих контейнеров предел RAM + swap равен пределу RAM, core limit равен нулю,
драйвер Docker logging установлен в none. Access-log отключён на обоих уровнях.
Состояние проверяйте через healthcheck и docker stats. Документы и отчёты
проходят через память приложения, прокси и сетевые соединения. Приложению не
нужны тома данных или временный том. Заполнять .env следует по [.env.example](../.env.example).

## Локальная проверка контейнеров

В окружении с Docker Engine и Compose:

```bash
docker build --target test -t normacheck:test .
docker run --rm normacheck:test
docker build --target frontend-test -t normacheck:ui-test .
docker run --rm normacheck:ui-test
docker build -t normacheck:check .
.venv/bin/python deploy/check_compose.py --image normacheck:check
```

Скрипт поднимает отдельный проект на loopback с тестовым секретом и проверяет
HTTP через Caddy, оба POST, повторную проверку, параллельную обработку файлов
вблизи лимита, cgroup v2, ограничение запросов и пересоздание приложения.
При нехватке памяти на целевом VPS увеличьте RAM и лимит приложения либо
уменьшите размер разрешённого файла с учётом MAX_REQUEST_BODY_BYTES. Размер
файла сам по себе не определяет расход памяти после распаковки XML.

Порядок запуска на новом сервере приведён в [VPS_RUNBOOK.md](VPS_RUNBOOK.md).
