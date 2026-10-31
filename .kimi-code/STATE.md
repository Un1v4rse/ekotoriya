# STATE — текущее состояние проекта «Экотория»

## Базовые параметры
- Проект: Ekotoriya (Flask-магазин, клон aspro_max)
- Локальный путь: `c:/Users/1/Desktop/Market Project/ekotoriya`
- Git: https://github.com/Un1v4rse/ekotoriya.git, ветка `main`
- Базовый commit (baseline): `a4a850d` — «[фикс] карточки: кнопки внизу + имя 3 строки; подтипы каталога = дизайн раздела; дропдаун height:auto»
- Последний зафиксированный commit: `01dbc47` — «[цикл5-приоритет3] адаптивность: breakpoint overrides + custom width preview»
- Текущие незакоммиченные изменения: Zero-редактор (Приоритет 4)
- Сервер prod: 185.56.162.91 (деплой только по явному запросу)

## Текущая фаза
- Активная задача: **Приоритет 4 — Zero-редактор** (готова к коммиту)
  - Копирование/вставка элементов (Ctrl+C / Ctrl+V + кнопки).
  - Направляющие при drag/resize.
  - Предупреждение о переполнении текста.
  - Кнопки выравнивания элемента по краям/центру.
  - Независимые мобильные стили (font_size, color, bg).

## Что изменено в активной задаче
- `blocks.py`: zero-элементы получили классы `cb-zero-text`, `cb-zero-btn`, `cb-zero-shape`; responsive CSS поддерживает font_size/color/bg.
- `page-editor.js`: clipboard copy/paste, guides, overflow warning, alignment buttons, responsive style fields.
- `page-editor.css`: стили для guides, overflow warning, float actions.
- `tests/test_cycle2_blocks.py`: 2 новых zero-теста.

## Изменённые файлы (после последнего commit)
- `ekotoriya/blocks.py`
- `ekotoriya/static/js/page-editor.js`
- `ekotoriya/static/css/page-editor.css`
- `ekotoriya/tests/test_cycle2_blocks.py`
- `.kimi-code/STATE.md`
- `.kimi-code/WORKLOG.md`
- `.kimi-code/BACKUPS.md`

## Состояние тестов
- Последний полный прогон: 485 passed, 1 failed (2026-10-31).
- Падающий тест: `test_product_create.py::test_recommendations_card_links_internal` — исходная ошибка (TemplateNotFound), не связана с текущей задачей.

## Бэкапы
- `backups/backup_2026-10-31_23-45` (перед Zero)
- `backups/backup_2026-10-31_23-35` (перед Приоритетом 4)
- `backups/backup_2026-10-31_23-00` (перед Приоритетом 3)

## Известные ограничения / отложенное
- Сервер prod временно недоступен; деплой отложен.
- Исходный failing test `test_recommendations_card_links_internal` остаётся.
- Группировка/разгруппировка Zero-элементов не реализована (сложность; можно добавить позже).

## Точное следующее действие
1. Закоммитить `[цикл5-приоритет4] Zero-редактор: copy/paste, guides, alignment, mobile styles`.
2. Перейти к Приоритету 5 — Черновики / совместная работа / история.
