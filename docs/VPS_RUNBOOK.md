# Запуск CheckSTP на новом VPS

Инструкция рассчитана на новый Linux VPS с cgroup v2. На сервер переносится
код из Git. Каталоги старых загрузок и резервные копии пользовательских файлов
для запуска не нужны.

## Подготовить хост

Проверьте swapon --show, параметры сбора аварийных дампов (включая
/proc/sys/kernel/core_pattern, apport, systemd-coredump и kdump) и настройки
хостинга. Используйте профиль без дискового swap и сохранения дампов памяти.
Проверьте настройки после перезагрузки. Не включайте сбор HTTP-запросов,
IP посетителей или тел запросов в firewall-логах, внешнем прокси, CDN, APM
и у провайдера. Снимки для резервирования не должны включать память VM.
Проверки контейнера не подтверждают эти настройки хоста и провайдера.

### Установить Docker, если его ещё нет

Проверка:

```bash
sudo docker version
sudo docker compose version
```

Для новой Ubuntu 24.04 без Docker:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
```

### Клонировать репозиторий

Сначала опубликуйте на GitHub текущий проект, включая Dockerfile, compose.yaml,
.env.example, deploy/, код и тесты. `storage/` и настоящие секреты в Git не входят.

На VPS:

```bash
sudo install -d -m 0755 -o "$(id -u)" -g "$(id -g)" /opt/normacheck
git clone https://github.com/lilkrand111/normacheck.git /opt/normacheck
cd /opt/normacheck
```

Если репозиторий закрытый, используйте SSH-доступ с отдельным read-only deploy key.
Не записывайте токен в URL репозитория. Репозиторий на сервере используем для
получения кода; локальные правки исходников делаем на компьютере и отправляем
через GitHub.

## Настроить и запустить

1. Направьте DNS-запись домена на VPS. Для автоматического TLS Caddy нужны
   входящие TCP 80 и 443. Убедитесь, что опубликованные Docker-порты
   соответствуют правилам сетевого экрана.
2. В корне проекта выполните команды ниже. В .env укажите DOMAIN и случайный
   APP_SECRET длиной не менее 32 символов (например, вывод openssl rand -hex 32).
   Оставьте COOKIE_SECURE=true. Храните файл конфигурации с правами 0600.
3. До публичного запуска выполните сквозную проверку на синтетических документах
   из [DEPLOYMENT.md](DEPLOYMENT.md). Проверьте память при одновременном
   исправлении двух документов типичной и максимальной сложности.

```bash
cd /opt/normacheck
install -m 600 .env.example .env
# Заполните .env перед продолжением.
sudo docker compose config --quiet
sudo docker compose up -d --build --wait
sudo docker compose ps
```

## Приёмка и обновление

Проверьте HTTPS и /health. Откройте страницу, загрузите синтетический DOCX,
исправьте его и откройте скачанную копию в Word. Обновление вкладки требует
повторного выбора файла. При проверке хоста выполните:

```bash
sudo docker compose exec -T app cat /sys/fs/cgroup/memory.swap.max
sudo docker compose exec -T caddy cat /sys/fs/cgroup/memory.swap.max
sudo docker stats --no-stream
```

Оба значения memory.swap.max должны быть 0. Документы не входят в резервные
копии. Операторские данные, которые нужны при переносе сервиса, включают
конфигурацию домена, секрет приложения и TLS-состояние Caddy.

Для обновления выполните git pull --ff-only и docker compose up -d --build --wait
с обычными для сервера правами Docker. В процессе перезапуска активный запрос
может прерваться. Пользователь может повторить его из открытой вкладки.
