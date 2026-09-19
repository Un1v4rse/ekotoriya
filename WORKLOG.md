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

## [2026-09-19 21:30] Задача: [этап-2.1…2.8 частично] — Ozon, фильтр выгрузки, скидки PR-2
- Статус: in_progress (осталось: админка «Интеграции» — субагент, финальный прогон/коммит)
- Сделано:
  - `pricing.py` (PR-2/4/5/6): база=discount_price или price; итог=база×(1−admin/100) или база−сумма; floor в пользу клиента; минимум 1 руб.; скидка МП в итог не входит; resolve по приоритету индивидуальная→category→source→all из таблицы discounts; enrich_product(s) добавляет final_price/site_discount_badge.
  - `wb_sync.final_price` — обёртка над pricing (старая формула удалена); `_persist_products` проставляет visible через export_filter.
  - `export_filter.py` (WB-5/OZ-4): режим all/selected/category из site.<source>_export; `reapply_visibility(source)` пересчитывает visible в БД без синка.
  - app.py: `_products_cached(include_hidden=False)` — единый каталог WB+Ozon (видимые; настройка site.hide_zero_stock CT-4); `invalidate_site_cache` везде; роуты: `/catalog/` (фильтры цена/категория/наличие/источник, сортировка popular/price/new, пагинация 24, поиск q — CT-1/CT-5), `/catalog/ozon/<id>/`, `_product_page(source, nm_id)` — единая карточка (CT-2…4); скидки: `/admin/api/product-discount` (percent|amount), `/admin/api/discount-bulk` (selected/category/source/all → Discount-правило), `/admin/api/discount-rules`, `/admin/api/discount-rule/delete`, `/admin/api/export-settings` GET/POST, `/admin/api/ozon-sync(-light)`; `_audit_log()` (PR-7); push-скидок в WB УДАЛЁН целиком (PR-5: `_maybe_push_discount`, pushed/push_message); `/admin/api/wb-discount(-bulk)` — обёртки совместимости; `/admin/api/wb-products` теперь отдаёт ВСЕ товары включая скрытые; журнал `/admin/api/wb-sync-log?source=wb|ozon` (OZ-6).
  - `templates/wb_catalog_block.html` переписан: три цены (база зачёркнута → цена МП → итог сайта), бейдж «скидка X% на сайте», бейдж источника WB/Ozon (OZ-7), внутренние ссылки на карточки, панель фильтров + пагинация при catalog_view, кнопки лайк/1-клик сохранены.
  - `ozon_sync.py` (OZ-1…6, субагент): креденшелы env/site, эндпоинты v3 product/list+info/list, v5 prices, v4 stocks, цены в сотых→рубли, маппинг в единую схему, merge admin-скидок, журнал source='ozon', без креденшелов → ошибка (демо НЕ нужен).
  - `jobs/autosync.py`: оба источника (wb+ozon), флаг --source, состояние site.mp_autosync_state.
  - Тесты: test_final_price.py переписан под PR-2 (floor, границы 0/99, фикс. сумма, приоритет правил, бейдж); test_export_filter.py (режимы, reapply, hide_zero_stock, цикл синк→каталог→заявка→клиент, фильтры/поиск); test_ozon_sync.py (маппинг, persist, merge скидок, журнал, sync_light) — субагент.
- Изменённые файлы: pricing.py, export_filter.py, ozon_sync.py, wb_sync.py, app.py, jobs/autosync.py, templates/wb_catalog_block.html, tests/{conftest,test_final_price,test_export_filter,test_ozon_sync}.py
- Проверки: `pytest tests/ -q` → **77 passed**; smoke test_client: `/`, `/catalog/` (+фильтры/поиск/пагинация), `/catalog/wb/<id>/`, `/theme.css` → 200; `jobs/autosync.py` — оба источника корректно пропускаются (выключены).
- Бэкапы: backup_2026-09-19_19-49, backup_2026-09-19_20-55
- Следующий шаг: админка «Интеграции» (субагент-3, фон) → финальная проверка «не ломать» → бэкап №3 → коммит [этап-2] → Этап 3.
- Примечания: карточка `/catalog/wb/<nm_id>/` сохранена (роутинг не сломан), добавлен `/catalog/ozon/<id>/`. Категория у WB-товаров пустая (WB API отдаёт предметы только через отдельный справочник) — фильтр по категории для WB сработает когда category появится.

## [2026-09-19 20:50] Задача: [этап-1.11] — Тесты TS-1 (baseline), pytest зелёный
- Статус: done
- Сделано: создан `tests/` (conftest с временной SQLite-БД и изоляцией JSON-фолбэков wb_sync во tmp): `test_blocks.py` (рендер всех 21 типа блоков + products-блок из БД), `test_customers.py` (нормализация, дедуп, заказы→клиент, лайки/события, популярное), `test_final_price.py` (baseline старой формулы round(base×(1−min(wb+admin,99)/100))), `test_storage.py` (roundtrip, удаление страниц, settings), `test_wb_sync.py` (demo-синк в БД, скидка переживает ресинк, журнал, light-sync, final_price на уровне app.get_products()).
- Изменённые файлы: tests/conftest.py, tests/test_blocks.py, tests/test_customers.py, tests/test_final_price.py, tests/test_storage.py, tests/test_wb_sync.py
- Проверки: `pytest tests/ -q` → **53 passed**. Заодно исправлено: `customers.normalize_phone` теперь приводит «8…»/«+7…»/10-значные к каноническому `+7XXXXXXXXXX` (иначе дедупликация по телефону не работала — CL-2).
- Бэкапы: backup_2026-09-19_19-49
- Следующий шаг: ручная проверка «не ломать», обновление PLAN.md, коммит [этап-1].

## [2026-09-19 20:40] Задача: [этап-1.10] — Авто-синк WB в cron-скрипт (AR-4, WB-2)
- Статус: done
- Сделано: создан `jobs/autosync.py` — расписание из БД (`site.wb_autosync`), состояние последних запусков в `site.wb_autosync_state`, флаги `--force-catalog`/`--force-light`. Из app.py удалены `_autosync_loop` и фоновый поток; `start_autosync()` оставлен как stub с подсказкой (обратная совместимость команд запуска). `__main__` теперь просто `app.run(...)`.
- Изменённые файлы: jobs/autosync.py, app.py
- Проверки: `python jobs/autosync.py --force-catalog` — демо-синк 8 товаров ОК; `import app` + test_client `/` → 200.
- Бэкапы: backup_2026-09-19_19-49
- Следующий шаг: 1.11 тесты.
- Примечания: для продакшена cron-строку: `*/15 * * * * cd <проект> && .venv/bin/python jobs/autosync.py` (Windows — планировщик задач). Сервер сессии перезапущен: TaskStop bash-agggxcpx → новый фоновый `bash-vffyu5s4` (`.venv/Scripts/python.exe -X utf8 app.py`, порт 5000, curl `/`, `/admin/login`, `/api/wb-products` → 200).

## [2026-09-19 20:20] Задача: [этап-1.9] — Flask-Caching (AR-3)
- Статус: done
- Сделано: подключён `Cache` (SimpleCache по умолчанию, env `CACHE_TYPE`); `_products_cached()` (5 мин) + `_popular_cached()` через `get_products()`; `invalidate_site_cache()` (cache.clear + storage.invalidate_cache) вызывается после всех save_content в админке (page-save, save, add-page, delete-page), после `admin_wb_sync`, `api_order`, `api_like`. `admin_wb_products`, `api_wb_products`, `wb_product_page` переведены на `get_products()` (в wb_product_page убрана ручная досчёт final_price — теперь из кэша).
- Изменённые файлы: app.py
- Проверки: test_client `/`, `/catalog/namatrasniki/`, `/proizvodstvo/`, `/theme.css`, `/robots.txt`, `/sitemap.xml`, `/catalog/wb/890129778/` → все 200.
- Бэкапы: backup_2026-09-19_19-49
- Следующий шаг: 1.10 autosync.

## [2026-09-19 20:05] Задача: [этап-1.1…1.8] — БД-фундамент: зависимости, модели, миграция данных
- Статус: done
- Сделано: requirements.txt + `.venv` (Python 3.14.7; всегда запускать `.venv/Scripts/python.exe -X utf8` — консоль Windows cp1251); `db.py` (Flask-SQLAlchemy, SQLite по умолчанию `data/ekotoriya.db`, env `DATABASE_URL`, WAL, `standalone_app()`); `models.py` (13 таблиц AR-1, у Page/Product — `to_content_dict()`/`to_product_dict()` под прежний JSON-формат); Alembic (`alembic/env.py` на database_url(), миграция `5da456f72137`, upgrade head выполнен); `migrate_json_to_db.py` (идемпотентен, сверка counts: 32 pages, 8 products source='wb', 6 customers, 7 orders, 8 likes→events, 2 sync_logs); `storage.py` (load/save на БД + JSON-фолбэк, module-level кэш, get/set_setting); `customers.py` полностью на БД (сигнатуры сохранены; добавлены normalize_phone/add_event/get_customer_likes/get_customer_orders/get_customer_stats); `wb_sync.py` на БД (app_context, журнал в sync_logs, скидки админа переживают ресинк через merge_with_existing, JSON-фолбэк сохраняется). `final_price()` — пока СТАРАЯ формула (baseline, новая PR-2 в этапе 2).
- Изменённые файлы: requirements.txt, db.py, models.py, storage.py, customers.py, wb_sync.py, migrate_json_to_db.py, alembic/*, app.py (импорты storage/db, init_app, обёртки load/save_content)
- Проверки: миграция идемпотентна (двойной прогон); test_client `/`, `/catalog/namatrasniki/`, `/proizvodstvo/`, `/theme.css`, `/robots.txt`, `/sitemap.xml` → 200; demo-синк 8 товаров, скидка 15% переживает ресинк.
- Бэкапы: backup_2026-09-19_19-49
- Следующий шаг: 1.9 кэширование.
- Примечания: бэкап №2 по плану перед этапом 1 не делался отдельно — baseline-бэкап №1 (сделанный при исправном состоянии) покрывает это требование.

## [2026-09-19 19:55] Задача: [этап-0] — Анализ, бэкап №1, baseline
- Статус: done
- Сделано: прочитаны все ключевые файлы; карта проекта и решения по миграции — в разделе «Анализ» выше; бэкап №1 `backups/backup_2026-09-19_19-49/` (102 МБ, полный); baseline-коммит `5ea17d6`; в `.gitignore` добавлен `backups/` (бэкап случайно попал в git — вынесен через `git rm --cached` + amend).
- Изменённые файлы: WORKLOG.md, PLAN.md, .gitignore
- Проверки: `/` → 200, `/admin` → 302; бэкап сверен побайтово (app.py, data/content.json).
- Бэкапы: backup_2026-09-19_19-49
- Следующий шаг: Этап 1.1 — зависимости (venv, requirements.txt).
- Примечания: `.venv` в проекте пуст — создаём заново; системный Python 3.14.7 с Flask 3.1.3 пока держит работающий сервер (фоновая задача bash-agggxcpx).
