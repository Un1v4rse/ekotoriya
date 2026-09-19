# WORKLOG — журнал доработки проекта «Экотория»

> Правило: после КАЖДОЙ задачи из `PLAN.md` дописывай запись по шаблону ниже.
> Этот файл — единственный источник истины о прогрессе. Если контекст сессии закончился,
> следующая сессия читает этот файл сверху вниз и продолжает с последней незакрытой задачи.
> Подробность записи — такая, чтобы не пришлось заново читать весь код.

## Формат записи

```
## [YYYY-MM-DD HH:MM] Задача: [этап-N.M] — короткое название
- Статус: done | in_progress | blocked
- Сделано: <что именно сделано, ключевые решения>
- Изменённые файлы: <список>
- Проверки: <что запускал, результаты (pytest, ручная проверка страниц)>
- Бэкапы: <какие есть сейчас в backups/>
- Следующий шаг: <что делать дальше>
- Примечания: <риски, отклонения, решения по спорным моментам>
```

---

## Анализ (Этап 0, 2026-09-19 19:49)

### Карта проекта

**Стек:** Flask 3.1.3 (системный Python 3.14.7, `C:\Python314`), Jinja2-шаблоны, JSON-файлы в `data/` как хранилище. `.venv` в проекте пуст/сломан (flask не импортируется) — реально используется системный Python. Зависимости: Flask, Werkzeug, requests, beautifulsoup4 (PIL — опционально для заглушки noimage).

**Модули:**
- `app.py` (1290 строк) — всё приложение: админка (сессионная auth, CSRF, смена пароля), роутинг 32 страниц из `content.json` (`page_route` + fallback-редиректы на родителя), `/theme.css` (генерация из site.theme, валидация цветов/размеров), SEO (robots, sitemap, 301-редиректы, Schema.org Organization/Product), конструктор (`/admin/page/<slug>` — редактор прямо на странице, API page-blocks/page-save/preview/add-page/delete-page), загрузка файлов (`/admin/api/upload` — БЕЗ валидации расширений, перезапись разрешена), прокси картинок (`/proxy/image` — allowlist хостов: basket-/cdn/wildberries.ru/wb.ru), заглушки `/basket/`, `/personal/*` (`basket_stub`, `personal_stub`), публичные API `/api/register|order|like`, авто-синк WB (`_autosync_loop` — поток в процессе веб-сервера, сейчас запускается и из `app.py __main__`, и из `python -c "..."` при старте сервера).
- `wb_sync.py` (389 строк) — WB Seller API: карточки (Content API v2 cursor), цены (`/public/api/v1/prices` + резерв), остатки (v3 stocks по складам, best-effort), push скидки (удалить по PR-5), демо-режим (8 товаров, включается АВТОМАТИЧЕСКИ при отсутствии токена — по WB-3 надо сделать явным переключателем), хранение в `data/wb_products.json`, журнал `data/sync_log.json` (200 записей), `final_price()` = `round(price × (1 − min(wb_disc + admin_disc, 99)/100))` — формула БУДЕТ заменена на PR-2 в этапе 2 (база = discount_price иначе price; НЕ суммировать wb+admin; тип «фикс. сумма»).
- `customers.py` (157 строк) — клиенты/заявки/лайки в `data/customers.json` (6 клиентов, 7 заявок, лайки). Дедуп по телефону (нормализация: цифры и `+`). `get_popular_products()` — скоринг заказы + лайки×вес.
- `blocks.py` (510 строк) — рендер ~17 типов блоков конструктора в HTML (`render_block`, `render_blocks`, `render_blocks_marked`), эскейпинг, валидация цветов.
- `static/js/page-editor.js` (1106 строк) — редактор на странице: hover-плашки, структура, DnD, drawer настроек, галерея блоков, upload через `/admin/api/upload`.
- `templates/` — `admin.html` (1749 строк, одностраничная админка), `admin_login.html` (глазик), `pages/*.html` (33 шаблона, включая `_new_page_base.html`), `wb_catalog_block.html`, `recommendations_block.html`, `seo_head.html`, `site_menu.html` и др.
- `data/content.json` — 32 страницы (slug, url, title, h1, meta_description, template, blocks[], content_html), menu (6), socials, site (title/phone/email/address/logo/meta_description/theme/seo; theme — 14 полей; seo — robots_txt, шаблоны мета карточек, redirects). `wb_products.json` — 8 товаров. `customers.json`, `sync_log.json`, `admin_credentials.json`, `image_cache/`.
- Прочее: `build_template.py` (пересборка шаблонов из static_original), `migrate_sections.py`, `download_assets.py`, `pages_to_clone.txt`.

### Точки, которые НЕЛЬЗЯ ломать (проверять после каждого этапа)
1. Роутинг 32 страниц + fallback-редиректы (`page_route`, bitrix/upload 301).
2. Конструктор: `/admin/page/<slug>`, DnD, drawer, предпросмотр, API страниц.
3. `/theme.css` из site.theme.
4. SEO: robots.txt, sitemap.xml, 301, Schema.org, шаблоны мета карточек.
5. WB-синк: sync/sync_light/демо/журнал/авто-синк (до переноса в cron).
6. Админка: auth, CSRF, смена пароля, upload, API страниц.
7. Кэш-заголовки, proxy_image, noimage.
8. Публичные API register/order/like.

### Найденные проблемы (подтверждены аудитом ТЗ)
- Хранилище — JSON-файлы (AR-1 → миграция на БД: SQLite минимум, схема из AR-1).
- Авто-синк в потоке веб-процесса (AR-4/WB-2 → cron-скрипт `jobs/autosync.py`).
- Нет кэширования (AR-3 → Flask-Caching).
- Демо-режим WB автоматический (WB-3 → явный переключатель в админке).
- Нет фильтра выгрузки WB-5/OZ-4 (флаг visible), нет Ozon вообще (OZ-1…OZ-7).
- Формула цены: суммирование скидок (PR-2 → новая формула, фикс. сумма, аудит-лог PR-7).
- Нет events/рекомендаций RS-1…RS-7 (только популярное), нет сегментов CL-5, экспорта CSV CL-4, согласия ПДн CL-6.
- Корзина/ЛК — заглушки (LC-1…LC-5), нет ролей (AD-2), дашборда (AD-3), аудит-лога (AD-4), бэкапа из админки (AD-5).
- Upload без валидации (AR-5); push скидок в WB (PR-5 — вывести).
- Нет pytest вообще; нет CI (TS-5); нет Docker (AR-8).

### Решения по миграции (зафиксировано до старта)
- **БД: SQLite по умолчанию** (`DATABASE_URL` для PostgreSQL позже) — ТЗ допускает SQLite как минимум; Alembic + SQLAlchemy (ORM). Flask-SQLAlchemy — да (план 1.1).
- **Стратегия content.json:** таблица `pages` (одна строка = страница, blocks — JSON-колонка) + таблица `site_settings` (key→JSON: site/theme/seo/menu/socials/интеграции). `load_content()` собирает прежний dict из БД → все шаблоны/админка/редактор НЕ меняются. `save_content()` раскладывает dict обратно в БД (транзакционно).
- **Продукты:** таблица `products` (единая схема WB+Ozon: id/nm_id, source, name, brand, price, discount_price, wb_discount_percent→mp_discount, admin_discount_percent, admin_discount_type, admin_discount_amount, photo, url, stock, visible, category, vendor_code, updated_at). `wb_sync.load_products()` читает из БД; JSON-файл — фолбэк на период миграции.
- **Клиенты/заявки/лайки/события:** таблицы customers/orders/events по AR-1; `customers.py` переписывается на БД с СОХРАНЕНИЕМ публичных сигнатур.
- **Журнал синка:** sync_logs таблица; обратная совместимость `get_sync_log()`.
- **Пароль админа** пока остаётся в `admin_credentials.json`; таблица admin_users вводится в этапе 4 (роли AD-2).
- **data/*.json в git не коммитятся** (там ПДн; .gitignore уже так настроен) — миграционный скрипт восстанавливает БД из JSON при деплое.
- Текущий `final_price()` покрывается тестами как baseline (задача 1.11); новая формула PR-2 — в этапе 2.

### Риски
- `load_content()` вызывается на КАЖДЫЙ запрос (32 страницы с тяжёлым content_html) → после перевода на БД добавить кэш (1.9) иначе регресс по скорости.
- Редактор сохраняет страницы целиком (`page-save`) — гонки двух админов; CN-5 (предупреждение о конфликте) — этап 5.
- content_html дублирует blocks (производное поле) — при миграции пересчитываем через render_blocks.
- Server start: сейчас `start_autosync()` вызывается и в `__main__`, и внешним способом запуска (`python -c "from app import app, start_autosync; ..."`) — при переходе на cron (1.10) поток убрать из процесса веб-сервера, но команда запуска в фоновой задаче сессии должна быть обновлена.

---

## Журнал

(новые записи — сверху, под этой строкой; самая свежая запись всегда первая)
