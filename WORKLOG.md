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

## Анализ (заполняется на Этапе 0)

- Статус: done (2026-09-21)
- **Карта проекта** (цикл 1, подтверждён аудитом, не перепроверять):
  Flask + SQLite (data/ekotoriya.db, WAL). `app.py` (~3000 строк) — все роуты публичной части
  и админки. `models.py` — SQLAlchemy-таблицы (pages, site_settings, products, discounts,
  customers, orders, events, recommendations, sync_logs, admin_users, audit_log).
  `storage.py` — load_content/save_content/get_setting/set_setting (кэш в памяти).
  `blocks.py` — рендер блоков конструктора (render_block: 20 типов; render_blocks_marked —
  с маркерами `<!--blk:N-->` для редактора). WB — `wb_sync.py` (API WB, демо-режим),
  Ozon — `ozon_sync.py`; цены — `pricing.py` (PR-2); рекомендации — `recommendations.py`.
- **Конструктор (переделывается в цикле 2):** `/admin/page/<slug>` рендерит страницу
  в реальном шаблоне с маркированными блоками + инжектит `static/js/page-editor.js`
  и `static/css/page-editor.css`. Сохранено из цикла 1 и ВСТРАИВАЕТСЯ в новый UI:
  undo/redo, автосохранение черновика (site_settings.page_drafts), конфликт-детект 409
  (expected_updated_at), панель «Структура страницы», пресеты темы (/admin/api/theme-presets),
  дублирование страницы, блоки timer/html (html — только суперадмин, санитайзер в blocks.py).
- **API конструктора (расширять, не ломать):** /admin/api/page-blocks (GET), page-draft
  (GET/POST), preview-marked (POST), page-save (POST, meta+blocks→content_html),
  preview (POST), theme-presets (GET/POST), upload (POST, валидация файлов), page-html,
  full-content, save (общий).
- **Миграции БД:** alembic не используется. Новые колонки/таблицы — скриптом миграции
  по образцу migrate_json_to_db.py (проверка `PRAGMA user_version` или CREATE TABLE IF NOT EXISTS).
- **Нельзя ломать:** рендер blocks.py как источник истины вывода; 32 страницы/роутинг;
  content_html пересобирается из blocks при сохранении; существующие блоки страниц
  обязаны открываться в новом редакторе без потери данных; page-save/preview API.
- **Риски:** большой объём фронтенда (новый редактор ~2000+ строк JS); обратная
  совместимость блоков; производительность при 30+ блоках (CN-T11) — решать точечными
  обновлениями DOM, без полного перерендера.

---

## Журнал

(новые записи — сверху, под этой строкой; самая свежая запись всегда первая)

## [2026-09-21 23:20] Задача: [цикл2-CN-T10+CN-T12+CN-T4+CN-T8] — Серверная часть конструктора и рендер (2 агента, параллельно)
- Статус: done
- Сделано:
  - **CN-T10/12 (коммит `ce28fe3`, агент-7):** миграция `migrate_cycle2.py` (pages.published_blocks + pages.settings JSON, таблицы page_versions/block_templates, идемпотентна, авто-при старте; БД уже мигрирована, backfill 36/36). Черновик в колонке Page.draft_blocks (fallback на старый site_settings.page_drafts); `POST /admin/api/page-publish` (draft→published, пересборка content_html, автоснапшот версии). Версии: page-version-save (лимит 10), GET page-versions, page-version-restore (в черновик, без авто-публикации). «Мои блоки»: block-templates GET/POST/DELETE. Шаблоны страниц: `page_templates.py` (landing/showcase/contacts), GET page-templates, POST page-templates/create (add-page отрефакторен в `_create_page`, поведение прежнее). Зоны: `zones.py` (default_zones из content.menu/logo/socials, render_zone с полным экранированием, validate_zone), GET/POST /admin/api/global-zones; `_new_page_base.html` переписан в чистый шаблон с зонами (32 legacy-страницы не тронуты); дефолт use_global=False. Настройки страницы: GET/POST /admin/api/page-settings (мета в колонки, og/favicon/bg/link_color/custom_css/js в Page.settings; custom_css/js — только суперадмин; вывод og/favicon/стилей в seo_head.html). Тема: `fonts.py` (35 шрифтов с кириллицей), GET /admin/api/fonts?q=, THEME_DEFAULTS расширен (heading/body_font_google, h1–h6, интерлиньяж, button_shadow/variant), GET/POST /admin/api/theme с историей (≤10, restore_history), /theme.css с новыми переменными и @import только валидных шрифтов.
  - **CN-T4/8 рендер (коммит `51d44e8`, агент-8):** `_block_wrapper` для всех типов (фон color/image/gradient/video+parallax+opacity, отступы, min_height, container full, content_align, effects: anim/sticky/decor/hide_on/hidden, css_class, custom_css только суперадмину с санитайзом, scoped по uid); `_typo` типографика элементов (font/google-fonts 33 семейства, size/line-height/tracking/weight/italic/caps/align); `_button_style` (variant/radius/shadow/hover/size) для button/banner/timer/form/zero; **Zero-блок** (элементы text/button/image/shape/video/icon в %, rotate, z, адаптив @media 1024/640 из responsive, RuTube); ANIMATIONS_ASSETS (IntersectionObserver + CSS, подключается только при наличии эффектов); Google Fonts link собирается один раз на страницу. html/section на wrapper НЕ мигрированы (сырой HTML/санитайз — осознанно).
  - **CN-T12.3 (коммит `fdba238`, я):** `GET /admin/api/media` — медиатека static/uploads (поиск ?q=, лимит, mtime desc). Эндпоинта recent-uploads не существовало (был только в списке прав).
- Изменённые файлы: app.py, models.py, blocks.py, storage.py (нет), zones.py, fonts.py, page_templates.py, migrate_cycle2.py, templates/pages/_new_page_base.html, templates/seo_head.html, tests/test_cycle2_server.py (27 тестов), tests/test_cycle2_blocks.py (35 тестов).
- Проверки: `pytest` — **214 passed** (152 прежних + 62 новых); сервер стартует (bash-1ltyohxj), `/`, `/catalog/`, `/basket/`, `/auth/`, `/theme.css`, `/sitemap.xml`, `/admin/login` → 200.
- Бэкапы: 02-55, 03-50, 21_20-45 (без изменений).
- Следующий шаг: **CN-T1–CN-T3 + CN-T11 — полная переработка фронтенда-редактора** (`static/js/page-editor.js` + `static/css/page-editor.css`): трёхзонный Tilda-layout (библиотека блоков слева / холст / правая панель настроек по вкладкам), верхняя панель (undo/redo, preview 1200/768/360, настройки страницы, черновик/опубликовано, сохранить/опубликовать), инлайн-редактирование с мини-тулбаром, hover-мост «+», панель «Контент страницы», горячие клавиши. API-контракт готов (см. коммиты выше).
- Примечания:
  - Агент-7: `published_at` в GET page-draft = updated_at при наличии published_blocks (отдельной колонки в ТЗ нет). У footer добавлен ключ use_global (по аналогии с header). override_* форсирует use_global при рендере. История темы не пушится при сохранении через старые роуты (/admin/api/save, theme-presets) — осознанно.
  - Агент-7: тест test_duplicate_page копирует текущий `_new_page_base.html` в templates/pages/akcii.html — после прогонов тестов этот файл «грязный», в коммиты не включать (восстанавливать `git checkout -- templates/pages/akcii.html`).
  - Агент-8: старая разметка блоков без новых полей визуально идентична (отличие — хвостовая `;` в style, тесты сравнивают нормализованно). `_sanitize_url` теперь отсекает URL с кавычками/пробелами/<> (усиление, существующие тесты не пострадали).

---

## [2026-09-21 20:55] Задача: [цикл2-этап-0] — Аудит и подготовка к циклу 2 (конструктор как Tilda)
- Статус: done
- Сделано:
  - Прочитаны `AUDIT_612a47b.md` (матрица статусов цикла 1 = весь цикл 1 done; вынесена в раздел «Аудит цикла 2» ниже), `PLAN_v2.md`, `blocks.py`, конструкторные API `app.py`, `models.py`, `db.py`, `storage.py`, структура `page-editor.js` (1251 строка, старая архитектура — под замену на трёхзонный layout).
  - Проверка работоспособности: сайт 200 (localhost:5000, задача bash-p4abz3i4), `pytest` — 152 passed.
  - **Бэкап №10**: `backups/backup_2026-09-21_20-45/` (полный, 70 МБ); удалён самый старый `backup_2026-09-19_23-05` (лимит 3). Актуальные: `backup_2026-09-20_02-55`, `backup_2026-09-20_03-50`, `backup_2026-09-21_20-45`.
  - WORKLOG.md был заменён пользователем на свежий шаблон цикла 2 — заполнил разделы «Анализ» и «Аудит цикла 2», старый журнал доступен в git-истории (коммит 612a47b).
- Изменённые файлы: WORKLOG.md (заполнение шаблона).
- Проверки: pytest 152 passed; сайт 200.
- Бэкапы: см. выше.
- Следующий шаг: **Этап 1 / CN-T12+CN-T10+CN-T8-рендер — серверная часть конструктора**: модель данных (published_blocks, page_versions, block/page templates, global zones, theme history), расширение blocks.py (фон/отступы/типографика/кнопки/эффекты/zero-рендер), все эндпоинты CN-T12.1, тесты. Затем CN-T1–CN-T4 (новый фронтенд-редактор).
- Примечания: порядок работы — сервер (A) → фронтенд-редактор (B) → фичи (C: зоны/тема/zero/шаблоны) → приёмка (Этап 3). Этап 2 PLAN_v2 пропускаем (матрица = AUDIT_612a47b.md, цикл 1 = done).

---

## ЦИКЛ 2 — памятка для продолжения

- План текущего цикла: `PLAN_v2.md` (главный приоритет — конструктор как Tilda, раздел CN-T).
- Первое действие цикла 2 — **аудит**: сверить фактическое состояние с `PLAN.md` (цикл 1),
  записать матрицу статусов (`done`/`partly`/`not_done` + доказательство) в раздел ниже.
- Дальше — Этап 1 `PLAN_v2.md` (конструктор), затем недоделанное по аудиту, затем приёмка.
- Правила неизменны: бэкап перед глобальными изменениями (макс. 3, ротация), запись после
  каждой задачи, `pytest` + коммит `[цикл2-этап-N]`, ничего не ломать из работающего.

## Аудит цикла 2 (заполняется первым действием)

- Статус: done (2026-09-21)
- Матрица статусов задач цикла 1 (этапы 1–7): **весь цикл 1 = done**, подтверждено внешним
  аудитом в файле `AUDIT_612a47b.md` (проверка кода коммита 612a47b). Краткая сводка:
  миграция на БД (db/models/storage), кэш, jobs/autosync, Ozon API, PR-2 (pricing),
  фильтр выгрузки (export_filter), единый каталог WB+Ozon, рекомендации/события,
  сегменты/CSV/ПДн, ЛК/корзина, роли/дашборд/аудит-лог/бэкап, безопасность
  (upload/CSP/sanitize/rate-limit/lockout/SSRF), Docker/CI, 152 теста — всё работает, не трогать.
- Частично/не сделано по ТЗ v1.1: только конструктор — сверка по пунктам CN-T в
  `AUDIT_612a47b.md` раздел «ЦИКЛ 2 … Не реализовано» (CN-T1…CN-T13, берём в работу
  по `PLAN_v2.md` Этап 1). Этап 2 `PLAN_v2.md` (недоделанное по циклу 1) — пустой, задач нет.

