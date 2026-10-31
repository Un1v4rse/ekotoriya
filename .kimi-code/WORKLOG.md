# WORKLOG — журнал доработки конструктора Ekotoriya

> Ведётся в `.kimi-code/WORKLOG.md`. Предыдущие записи по проекту находятся в
> `WORKLOG.md` в корне проекта.

Формат записи:
```
[Дата и время] [Этап/задача]
Действие:
Причина:
Изменённые файлы:
Проверки и результат:
Созданный бэкап или commit:
Проблемы:
Следующий шаг:
```

---

[2026-10-31 20:10] [Приоритет 1] — безопасный Rich Text для текстовых блоков
Действие:
  - Добавлен серверный санитайзер `_sanitize_rich_text` (whitelist: p, br,
    strong/b, em/i, a[href/target/rel], ul, ol, li; удаляет script/style/
    event handlers и небезопасные URL).
  - Блок `text` в `render_block`: при `rich=True` использует отсанитизированный
    HTML, иначе — прежний plain text с экранированием.
  - В клиентском редакторе `page-editor.js` добавлен `richTextInput` с тулбаром
    (жирный, курсив, подчёркивание, списки, ссылка) на базе contenteditable.
    Текстовый блок теперь по умолчанию сохраняет HTML и выставляет `rich: true`.
  - Инлайн-редактирование на холсте для `text` + `rich` теперь сохраняет
    `innerHTML` вместо `innerText`, но для plain-блоков остаётся plain.
  - Добавлен тест `test_rich_text_sanitizes_and_renders`.
Причина:
  - Заказчик: форматирование текста в редакторе терялось, потому что сервер
    экранировал любой HTML. Нужно было разрешить безопасное форматирование,
    не открывая XSS.
Изменённые файлы:
  `blocks.py`, `static/js/page-editor.js`, `tests/test_cycle2_blocks.py`
Проверки и результат:
  - `pytest tests/test_cycle2_blocks.py -q` — 37 passed.
  - Полный прогон `pytest tests/` — запущен фоном, ожидается.
Созданный бэкап или commit:
  - будет создан после зелёного прогона.
Проблемы:
  - Нет.
Следующий шаг:
  - Закоммитить и перейти к Приоритету 2 (расширение форм).

[2026-10-31 19:55] [Приоритет 0.3] — актуализация README.md
Действие:
  - Полностью переписан README.md: добавлены актуальные разделы установки,
    запуска, ролей, тестов, редактора страниц, структуры данных,
    синхронизации товаров, резервного копирования и известных ограничений.
  - Убран устаревший фрагмент про `data/content.json` как единственный
    источник контента; указана SQLite-база и миграции.
  - Убран демонстрационный пароль `admin/admin` из публичной документации;
    описан безопасный способ через `ADMIN_USER`/`ADMIN_PASS`.
Причина:
  - README не соответствовал фактическому коду: не упоминались БД,
    конструктор, черновики/публикация, миграции, товары и роли.
Изменённые файлы:
  `README.md`
Проверки и результат:
  - `pytest tests/` — запущен фоновый прогон, ожидается результат.
  - Markdown-файл валиден.
Созданный бэкап или commit:
  - будет создан после получения зелёного прогона.
Проблемы:
  - Нет.
Следующий шаг:
  - Закоммитить README и перейти к Приоритету 1 (безопасный Rich Text).

[2026-10-31 19:40] [Приоритет 0.1] — настройки колонок gallery/features/text_columns
Действие:
  - Добавлен параметр `extra_classes` в `_block_wrapper` (blocks.py).
  - Блоки `gallery`, `features`, `text_columns` теперь передают в обёртку
    CSS-классы `cb-gallery-cN`, `cb-features-cN`, `cb-cols-N`, которые уже
    были описаны в инжектируемых стилях app.py.
  - `text_columns` научился учитывать явный `cols` (2-4), но не превышает
    количество реальных пунктов; без настройки поведение остаётся как раньше
    (по числу колонок).
  - Добавлен тест `test_block_column_classes_respected`.
Причина:
  - В редакторе настройки колонок уже были, но серверный рендер их игнорировал:
    классы `cb-gallery-cN` и т.п. не попадали в HTML, поэтому сетка не
    менялась.
Изменённые файлы:
  `blocks.py`, `tests/test_cycle2_blocks.py`
Проверки и результат:
  - `pytest tests/test_cycle2_blocks.py -q` — 36 passed.
  - `pytest tests/` (baseline) — 469 passed.
Созданный бэкап или commit:
  - commit `a9aff23` — "[цикл5-приоритет0.1] настройки колонок gallery/features/text_columns применяются в HTML".
  - бэкап `backups/backup_2026-10-31_19-08` актуален.
Проблемы:
  - Нет.
Следующий шаг:
  - Приоритет 0.2/0.3: проверить воспроизводимость пресетов и обновить README.

[2026-10-31 19:38] [Старт master-промта / фаза 0] — инициализация STATE/WORKLOG/BACKUPS
Действие:
  - Созданы служебные файлы `.kimi-code/STATE.md`, `.kimi-code/WORKLOG.md`,
    `.kimi-code/BACKUPS.md`.
  - Зафиксирован baseline commit `a4a850d`, текущие изменения, бэкапы и
    временно недоступный prod-сервер.
  - Запущен фоновый baseline-прогон тестов для подтверждения исходного состояния.
Причина:
  - Новый master-промт требует восстановления состояния через `.kimi-code/STATE.md`
    и последовательной работы по приоритетам.
Изменённые файлы:
  `.kimi-code/STATE.md`, `.kimi-code/WORKLOG.md`, `.kimi-code/BACKUPS.md`
Проверки и результат:
  - Git статус зафиксирован (WORKLOG.md modified, служебные скрипты untracked).
  - Последний полный прогон: 469 passed (до старта master-промта).
Созданный бэкап или commit:
  - бэкап `backups/backup_2026-10-31_19-08` уже существует.
Проблемы:
  - Сервер 185.56.162.91 недоступен; деплой предыдущих фиксов отложен.
Следующий шаг:
  - Приоритет 0.1: исправить связь настроек колонок с серверным рендером.



[2026-10-31 22:05] [Приоритет 2 — старт] — расширение форм
Действие:
  - Проверен синтаксис `blocks.py` (`py_compile` OK).
  - Изучены текущий рендер блока `form` в `blocks.py`, endpoint `/api/order`,
    модель `FeedbackRequest` и UI настроек формы в `page-editor.js`.
  - Создан бэкап текущего рабочего состояния с незакоммиченными правками формы.
  - Обновлён `.kimi-code/STATE.md`.
Причина:
  - Продолжение master-промта после compaction контекста; нужна защита
    текущих изменений перед дальнейшими правками.
Изменённые файлы:
  `.kimi-code/STATE.md`, `.kimi-code/WORKLOG.md`, `.kimi-code/BACKUPS.md`
Проверки и результат:
  - `python -m py_compile ekotoriya/blocks.py` — OK.
Созданный бэкап или commit:
  - `backups/backup_2026-10-31_22-05` (src.tar 167M + ekotoriya.db 14M).
Проблемы:
  - Нет.
Следующий шаг:
  - Добавить `/api/form` в `app.py`, UI `fields` в `page-editor.js`, тесты.


[2026-10-31 22:40] [Приоритет 2 — завершение] — расширение форм
Действие:
  - Добавлен endpoint `/api/form` в `app.py`: валидация по `form_schema`,
    хонипот-поле `website`, серверная проверка обязательных полей, создание
    `FeedbackRequest` с сохранением всех значений в JSON в `message`.
  - Добавлен UI `formFieldsEditor` в `page-editor.js`: список полей с типами
    (text/tel/email/textarea/select/checkbox), подписью, placeholder,
    обязательностью и вариантами для select.
  - Обновлена панель настроек блока `form`: заголовок, кнопка, сообщение об
    успехе, получатель, поля формы.
  - Исправлен рендер select-опций в `blocks.py` для хранения options как
    массива строк.
  - Добавлены тесты `tests/test_form_builder.py` (8 шт.): рендер старой и
    кастомной форм, select/checkbox, валидация, хонипот, redirect.
  - Прогнаны тесты: 8/8 новых passed, полный набор 478 passed / 1 failed
    (исходный `test_recommendations_card_links_internal`).
  - Синтаксическая проверка `app.py`, `blocks.py`, `page-editor.js` — OK.
  - Сделан commit `b0ffb97`.
Причина:
  - Завершение Приоритета 2 master-промта.
Изменённые файлы:
  `app.py`, `blocks.py`, `static/js/page-editor.js`, `tests/test_form_builder.py`,
  `.kimi-code/STATE.md`, `.kimi-code/WORKLOG.md`, `.kimi-code/BACKUPS.md`,
  `WORKLOG.md`
Проверки и результат:
  - `pytest tests/test_form_builder.py` — 8 passed.
  - `pytest tests/` — 478 passed, 1 failed (исходный).
Созданный бэкап или commit:
  - commit `b0ffb97` на ветке `main`.
Проблемы:
  - Исходный failing test остаётся.
Следующий шаг:
  - Приоритет 3: адаптивность (произвольная ширина превью + breakpoint overrides).


[2026-10-31 23:35] [Приоритет 3 — завершение] — адаптивность
Действие:
  - Добавлена серверная функция `_responsive_css()` в `blocks.py`: генерация
    `@media (max-width:1024px)` и `@media (max-width:640px)` для override'ов
    `hide`, `padding_top`, `padding_bottom`, `align`, `min_height`.
  - В `_block_wrapper` uid-класс `cb-{uid}` добавляется только при наличии
    responsive CSS или custom CSS суперадмина; это сохраняет старую разметку
    для блоков без адаптива.
  - В `page-editor.js` добавлена вкладка «Адаптив» с настройками tablet/mobile
    (скрыть блок, отступы, мин. высота, выравнивание).
  - В топ-бар добавлен input `#pe-device-width` для произвольной ширины
    превью (320–1920 px); значение сохраняется в localStorage.
  - Добавлены стили для input ширины в `page-editor.css`.
  - Добавлены тесты `tests/test_responsive.py` (5 шт.).
  - Прогнаны тесты: 5/5 новых passed, полный набор 483 passed / 1 failed
    (исходный `test_recommendations_card_links_internal`).
  - Создан бэкап `backups/backup_2026-10-31_23-00`.
Причина:
  - Завершение Приоритета 3 master-промта.
Изменённые файлы:
  `blocks.py`, `static/js/page-editor.js`, `static/css/page-editor.css`,
  `tests/test_responsive.py`, `.kimi-code/STATE.md`, `.kimi-code/WORKLOG.md`,
  `.kimi-code/BACKUPS.md`
Проверки и результат:
  - `pytest tests/test_responsive.py tests/test_form_builder.py` — 13 passed.
  - `pytest tests/` — 483 passed, 1 failed (исходный).
  - `node --check static/js/page-editor.js`, `py_compile` — OK.
Созданный бэкап или commit:
  - бэкап `backups/backup_2026-10-31_23-00`.
Проблемы:
  - Исходный failing test остаётся.
Следующий шаг:
  - Commit и переход к Приоритету 4 — Zero-редактор.
