# STATE — текущее состояние проекта «Экотория»

## Базовые параметры
- Проект: Ekotoriya (Flask-магазин, клон aspro_max)
- Локальный путь: `c:/Users/1/Desktop/Market Project/ekotoriya`
- Git: https://github.com/Un1v4rse/ekotoriya.git, ветка `main`
- Базовый commit (baseline): `a4a850d` — «[фикс] карточки: кнопки внизу + имя 3 строки; подтипы каталога = дизайн раздела; дропдаун height:auto»
- Последний зафиксированный commit: `b6d665d` — «[цикл5-приоритет5] черновики: optimistic locking, автор, глубина версий»
- Текущие незакоммиченные изменения: Приоритет 7 — Безопасные сторонние виджеты (готов к коммиту)
- Сервер prod: 185.56.162.91 (деплой только по явному запросу)

## Текущая фаза
- Активная задача: **Приоритет 7 — Безопасные сторонние виджеты** (готова к коммиту)
  - Новый блок `embed` с белым списком провайдеров (YouTube, Vimeo, RuTube, VK Video, Dailymotion, SoundCloud, Spotify, Яндекс.Карты, Google Maps).
  - iframe с `sandbox` и ограниченными permissions; видео — 16:9, карты — фиксированная высота.
  - Серверная валидация URL в `validate_blocks` (save, preview, publish).
  - Блок `html` скрыт в библиотеке для не-суперадминов; сервер сохраняет html только суперадмину.
  - Роль админа передаётся в редактор (`window.ADMIN_ROLE`).
  - Preview доступен content-менеджеру (`/admin/api/preview` → section `pages`).
  - Тесты: 19 штук в `tests/test_embed_widgets.py`.

## Что изменено в активной задаче
- `blocks.py`: `_embed_url`, `_embed_iframe_html`, `validate_blocks`, рендер `embed`.
- `app.py`: `validate_blocks` в `/admin/api/page-save`, `/admin/api/page-publish`, `/admin/api/preview`; `/admin/api/preview` в `ADMIN_PATH_SECTIONS`; `admin_role` в `page_editor_wrapper.html`.
- `templates/page_editor_wrapper.html`: `window.ADMIN_ROLE`.
- `static/js/page-editor.js`: `ADMIN_ROLE`, `BLOCK_TYPES.embed`, категория «Виджет», редактор embed, скрытие html для не-суперадминов.
- `tests/test_embed_widgets.py`: 19 тестов.

## Изменённые файлы (после последнего commit)
- `ekotoriya/app.py`
- `ekotoriya/blocks.py`
- `ekotoriya/static/js/page-editor.js`
- `ekotoriya/templates/page_editor_wrapper.html`
- `ekotoriya/tests/test_embed_widgets.py`
- `.kimi-code/STATE.md`
- `.kimi-code/WORKLOG.md`
- `.kimi-code/BACKUPS.md`

## Состояние тестов
- Последний полный прогон: 509 passed, 0 failed (2026-10-31).
- `node --check static/js/page-editor.js` — OK.
- Smoke-test: редактор открывается, блок «Виджет» добавляется, панель настроек embed отображается.

## Бэкапы
- `backups/backup_2026-10-31_23-18` (перед Приоритетом 7)
- `backups/backup_2026-11-01_00-00` (перед Приоритетом 5)
- `backups/backup_2026-10-31_23-45` (перед Zero)

## Известные ограничения / отложенное
- Сервер prod временно недоступен; деплой отложен.
- Исходный failing test больше не воспроизводится (все 509 тестов проходят).

## Точное следующее действие
1. Закоммитить `[цикл5-приоритет7] безопасные сторонние виджеты: embed, белый список, html для суперадмина`.
2. Обновить TODO: отметить приоритет 7 done.
3. Если пользователь не требует продолжения — завершить цикл 5 master-промта и сообщить итог.
