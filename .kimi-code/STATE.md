# STATE — текущее состояние проекта «Экотория»

## Базовые параметры
- Проект: Ekotoriya (Flask-магазин, клон aspro_max)
- Локальный путь: `c:/Users/1/Desktop/Market Project/ekotoriya`
- Git: https://github.com/Un1v4rse/ekotoriya.git, ветка `main`
- Базовый commit (baseline): `a4a850d` — «[фикс] карточки: кнопки внизу + имя 3 строки; подтипы каталога = дизайн раздела; дропдаун height:auto»
- Последний зафиксированный commit: `b0ffb97` — «[цикл5-приоритет2] расширение форм: кастомные поля, /api/form, хонипот, тесты»
- Текущие незакоммиченные изменения: адаптивность (Приоритет 3)
- Сервер prod: 185.56.162.91 (деплой только по явному запросу)

## Текущая фаза
- Активная задача: **Приоритет 3 — Адаптивность** (готова к коммиту)
  - Добавлена вкладка «Адаптив» с per-breakpoint overrides.
  - Добавлена поддержка `block.responsive.{tablet|mobile}.{hide, padding_top, padding_bottom, align, min_height}`.
  - Добавлен input произвольной ширины превью в топ-бар.
  - Старый JSON без responsive не меняет разметку.

## Что изменено в активной задаче
- `blocks.py`: `_responsive_css()`, uid-класс добавляется только при наличии кастомных стилей.
- `page-editor.js`: вкладка «Адаптив», custom width input.
- `page-editor.css`: стили для input ширины.
- `tests/test_responsive.py`: 5 регрессионных тестов.

## Изменённые файлы (после последнего commit)
- `ekotoriya/blocks.py`
- `ekotoriya/static/js/page-editor.js`
- `ekotoriya/static/css/page-editor.css`
- `ekotoriya/tests/test_responsive.py`
- `.kimi-code/STATE.md`
- `.kimi-code/WORKLOG.md`
- `.kimi-code/BACKUPS.md`

## Состояние тестов
- Последний полный прогон: 483 passed, 1 failed (2026-10-31).
- Падающий тест: `test_product_create.py::test_recommendations_card_links_internal` — исходная ошибка (TemplateNotFound), не связана с текущей задачей.

## Бэкапы
- `backups/backup_2026-10-31_23-00` (перед Приоритетом 3)
- `backups/backup_2026-10-31_22-05` (перед Приоритетом 2)
- `backups/backup_2026-10-31_19-08` (baseline перед master-промтом)

## Известные ограничения / отложенное
- Сервер prod временно недоступен; деплой отложен.
- Исходный failing test `test_recommendations_card_links_internal` остаётся.

## Точное следующее действие
1. Закоммитить `[цикл5-приоритет3] адаптивность`.
2. Перейти к Приоритету 4 — Zero-редактор (группировка, копирование/вставка, выравнивание, направляющие, мобильные стили).
