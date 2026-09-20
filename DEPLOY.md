# Деплой «Экотории» (Этап 6.4, AR-8)

Flask-приложение (`app:app`) в Docker за nginx (прокси + статика + gzip).
БД — SQLite в volume (по умолчанию); для PostgreSQL задайте `DATABASE_URL`.

## 1. Быстрый старт

```bash
cp .env.example .env   # или создайте .env вручную (см. список ниже)
docker compose up -d --build
```

Сайт: `http://<хост>/`, админка: `http://<хост>/admin/`.

При первом запуске без `ADMIN_USER`/`ADMIN_PASS` пароль админа сгенерируется
случайный и будет напечатан **один раз** в логах контейнера:

```bash
docker compose logs app | grep -A3 'Пароль'
```

## 2. Переменные окружения (.env)

| Переменная      | Назначение                                             |
|-----------------|--------------------------------------------------------|
| `ADMIN_USER`    | Логин главного админа (иначе `admin` + случайный пароль) |
| `ADMIN_PASS`    | Пароль главного админа (минимум 8 символов рекомендуется) |
| `SECRET_KEY`    | Секрет сессий (**обязательно** в проде, иначе сессии сбросятся при перезапуске) |
| `DATABASE_URL`  | `sqlite:////app/data/ekotoriya.db` (default) или `postgresql+psycopg://…` |
| `CACHE_TYPE`    | `SimpleCache` (default) / `RedisCache` (тогда Redis в compose) |
| `FORCE_HTTPS`   | `1` — редирект 308 на https + HSTS (включить за nginx с TLS) |
| `WB_API_TOKEN`  | Токен Seller API Wildberries (демо-каталог без токена) |
| `OZON_CLIENT_ID`, `OZON_API_KEY` | Ключи API Ozon Seller |

## 3. Тома

- `app-data` → `/app/data` — SQLite-файл, `image_cache`, JSON-фолбэки.
- `app-uploads` → `/app/static/uploads` — файлы, загруженные через админку.
- `./static` подключён в nginx read-only — статика отдаётся без участия Flask.

## 4. HTTPS

1. Положите сертификаты в `./certs` (`fullchain.pem`, `privkey.pem`),
   раскомментируйте блок `server { listen 443 ssl; … }` в `nginx.conf`
   и строку с томом `certs` в `docker-compose.yml`.
2. В `.env` выставьте `FORCE_HTTPS=1` (приложение добавит HSTS и доведёт
   редирект до 308).

## 5. Cron-задачи (внутри контейнера app или на хосте с `DATABASE_URL`)

```cron
# лёгкий синк цен/остатков и полный синк по расписанию из настроек админки
*/15 * * * *  cd /app && python jobs/autosync.py >> /var/log/ekotoriya-autosync.log 2>&1
# пересчёт персональных рекомендаций (RS-5), раз в сутки ночью
17 3 * * *    cd /app && python jobs/recompute_recommendations.py >> /var/log/ekotoriya-recs.log 2>&1
# ежедневный бэкап БД + контента, ротация 14 дней
23 2 * * *    cd /app && python jobs/backup_daily.py --dir /app/backups/daily >> /var/log/ekotoriya-backup.log 2>&1
```

> Чтобы cron работал внутри контейнера, добавьте в образ пакет `cron`
> (или `apt-get install -y cron`) и CMD-обёртку `cron && gunicorn …`.
> Проще — cron на хосте, примонтировав тот же volume `app-data`.

Бэкапы лежат в `backups/daily/YYYY-MM-DD.zip` (SQLite `ekotoriya.db` +
`content.json`); восстановление — через админку, раздел «Бэкап».

## 6. Обновление

```bash
git pull
docker compose up -d --build
```

## 7. Проверка

```bash
docker compose ps
docker compose logs -f app
curl -I http://127.0.0.1/           # 200 + CSP-заголовок
```
