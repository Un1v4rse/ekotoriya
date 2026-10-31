# STATE — текущее состояние проекта «Экотория»

## Базовые параметры
- Проект: Ekotoriya (Flask-магазин, клон aspro_max)
- Локальный путь: `c:/Users/1/Desktop/Market Project/ekotoriya`
- Git: https://github.com/Un1v4rse/ekotoriya.git, ветка `main`
- Базовый commit (baseline): `a4a850d` — «[фикс] карточки: кнопки внизу + имя 3 строки; подтипы каталога = дизайн раздела; дропдаун height:auto»
- Последний зафиксированный commit: `ed2233a` — «[цикл5-приоритет4] Zero-редактор: copy/paste, guides, alignment, mobile styles»
- Текущие незакоммиченные изменения: Приоритет 5 — Черновики / совместная работа / история (готово к коммиту)
- Сервер prod: 185.56.162.91 (деплой только по явному запросу)

## Текущая фаза
- Активная задача: **Приоритет 5 — Черновики / совместная работа / история** (готова к коммиту)
  - Optimistic locking для `/admin/api/page-draft` (expected_saved_at, 409).
  - Сохранение автора (`saved_by`) и времени (`saved_at`) изменений черновика.
  - Настраиваемая глубина архива версий через env `PAGE_VERSION_LIMIT`.
  - Клиентский диалог конфликта с кнопками «Загрузить чужие» / «Перезаписать моими».
  - Тесты двух клиентов и 409.

## Что изменено в активной задаче
- `app.py`: optimistic locking в `/admin/api/page-draft`; `saved_at` с миллисекундами; `saved_by`; `PAGE_VERSION_LIMIT`.
- `page-editor.js`: `draftSavedBy`, expected_saved_at в saveDraft, диалог конфликта, force save.
- `tests/test_draft_conflict.py`: 4 теста.

## Изменённые файлы (после последнего commit)
- `ekotoriya/app.py`
- `ekotoriya/static/js/page-editor.js`
- `ekotoriya/tests/test_draft_conflict.py`
- `.kimi-code/STATE.md`
- `.kimi-code/WORKLOG.md`
- `.kimi-code/BACKUPS.md`

## Состояние тестов
- Последний полный прогон: 489 passed, 1 failed (2026-10-31).
- Падающий тест: `test_product_create.py::test_recommendations_card_links_internal` — исходная ошибка (TemplateNotFound), не связана с текущей задачей.

## Бэкапы
- `backups/backup_2026-11-01_00-00` (перед Приоритетом 5)
- `backups/backup_2026-10-31_23-45` (перед Zero)
- `backups/backup_2026-10-31_23-35` (перед Приоритетом 4)

## Известные ограничения / отложенное
- Сервер prod временно недоступен; деплой отложен.
- Исходный failing test остаётся.
- Сравнение diff двух версий в UI не реализовано (только загрузка/перезапись).

## Точное следующее действие
1. Закоммитить `[цикл5-приоритет5] черновики: optimistic locking, автор, глубина версий`.
2. Перейти к Приоритету 6 — Универсальные коллекции (если нужны) или Приоритету 7.
