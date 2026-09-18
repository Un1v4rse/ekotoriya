#!/usr/bin/env python3
"""Разбить единый legacy-блок страниц (весь HTML целиком) на секции-блоки.

Главная режется по .drag-block (как в конструкторе Bitrix), внутренние
страницы — по корневым элементам контента. Сами страницы на сайте не меняются:
content_html остаётся прежним до первого сохранения в редакторе.
"""
import json
import re
import shutil
import sys

from bs4 import BeautifulSoup, Comment

CONTENT = 'data/content.json'
BACKUP = 'data/content_backup_before_sections.json'

SECTION_NAMES = [
    ('TOP_BIG_BANNERS', 'Главный баннер'),
    ('TIZERS', 'Преимущества'),
    ('MIDDLE_ADV', 'Промо-блок'),
    ('FLOAT_BANNERS', 'Баннеры'),
    ('CATALOG_TAB', 'Вкладки каталога'),
    ('BRANDS', 'Бренды'),
    ('REVIEWS', 'Отзывы'),
    ('COMPANY_TEXT', 'О компании'),
    ('INSTAGRAMM', 'Фото из Instagram'),
    ('BOTTOM_BANNERS', 'Нижние баннеры'),
    ('CATALOG_SECTIONS', 'Разделы каталога'),
    ('SALE', 'Акции'),
    ('NEWS', 'Новости'),
    ('BLOG', 'Блог'),
    ('MAPS', 'Карта'),
    ('FAVORIT_ITEM', 'Популярные товары'),
    ('LOOKBOOKS', 'Лукбук'),
    ('STORIES', 'Истории'),
    ('SERVICES', 'Услуги'),
    ('GALLERY', 'Галерея'),
    ('PARTNERS', 'Партнёры'),
    ('PROJECTS', 'Проекты'),
    ('VK_VIDEO', 'Видео VK'),
    ('YOUTUBE', 'Видео YouTube'),
    ('VK', 'Виджет ВКонтакте'),
    ('RUTUBE', 'Видео Rutube'),
]


def name_for(classes, index):
    for key, label in SECTION_NAMES:
        if key in classes:
            return label
    if 'page-top' in classes:
        return 'Заголовок страницы'
    return f'Секция {index}'


def meaningful(el):
    """Есть ли в элементе что-то кроме пробелов и комментариев."""
    if isinstance(el, Comment):
        return False
    if isinstance(el, str):
        return bool(el.strip())
    if getattr(el, 'name', None):
        return True
    return False


def serialize(el):
    if isinstance(el, str):
        return el
    return str(el)


def split_page(html):
    """Вернуть список {name, html} секций или None, если резать не стоит."""
    # 1) Страницы с drag-block: разворачиваем вложенные друг в друга
    # drag-block (в скопированном HTML не закрыты div) в соседей одного
    # уровня, затем режем СТРОКУ по границам drag-block. Дерево остаётся
    # эквивалентным браузерному, а секции получаются сбалансированными.
    if len(re.findall(r'<div\s+class="[^"]*\bdrag-block\b[^"]*"[^>]*>', html)) >= 2:
        soup = BeautifulSoup(html, 'html5lib')
        dbs = soup.select('.drag-block')
        total = len(dbs)
        if total >= 2:
            # Общий предок, содержащий ВСЕ drag-block. Секциями становятся
            # дети этого предка; контейнер с несколькими drag-block режется
            # на отдельные блоки, обёртка над одним блоком сохраняется
            # целиком (например div.middle вокруг главного баннера).
            # Каждая секция — сбалансированный элемент дерева, поэтому
            # <!--blk-->-маркеры между секциями не меняют HTML5-разбор.
            ca = dbs[0]
            while ca is not None and len(ca.select('.drag-block')) < total:
                ca = ca.parent
            if ca is None:
                return None
            blocks = []

            def is_drag(c):
                return getattr(c, 'name', None) and 'drag-block' in (c.get('class') or [])

            def emit(container):
                for c in [x for x in container.contents if meaningful(x)]:
                    if not getattr(c, 'name', None):
                        blocks.append(c)
                        continue
                    n_db = len(c.select('.drag-block'))
                    kids = [x for x in c.contents if meaningful(x)]
                    if n_db == 0 or is_drag(c) or (n_db == 1 and len(kids) == 1):
                        blocks.append(c)
                    else:
                        emit(c)

            emit(ca)
            result = [{'name': name_for(' '.join(c.get('class') or []) if getattr(c, 'name', None) else '', i),
                       'html': serialize(c)}
                      for i, c in enumerate(blocks, 1)]
            return result or None
        return None

    # 2) Внутренняя страница: спускаемся по цепочке одиночных обёрток
    # (top-block-wrapper > wrapper_inner > container_inner > ...) до контейнера
    # с несколькими осмысленными детьми и режем его.
    soup = BeautifulSoup(html, 'html.parser')

    def elem_kids(el):
        return [c for c in el.children if getattr(c, 'name', None) and meaningful(c)]

    def diverse(kids):
        """Дети не должны быть однотипным списком (карточки товаров и т.п.)."""
        if len(kids) < 3:
            return True
        from collections import Counter
        cnt = Counter((c.get('class') or [c.name])[0] for c in kids)
        top, n = cnt.most_common(1)[0]
        return n < len(kids) * 0.6

    container = None
    fallback = None
    current = soup
    for _ in range(12):
        kids = elem_kids(current)
        if len(kids) == 1:
            current = kids[0]
            continue
        if 3 <= len(kids) <= 30 and diverse(kids):
            container = current
            break
        if len(kids) >= 2 and not diverse(kids):
            break
        if len(kids) == 2:
            fallback = current  # запомним уровень: заголовок + контент
            current = max(kids, key=lambda k: len(str(k)))
            continue
        break
    if container is None and fallback is not None:
        container = fallback
    if container is None:
        return None
    blocks = []
    for i, c in enumerate(elem_kids(container), 1):
        classes = ' '.join(c.get('class') or []) if getattr(c, 'name', None) else ''
        blocks.append({'name': name_for(classes, i), 'html': serialize(c)})
    blocks = [b for b in blocks if has_content(b['html'])]
    if len(blocks) >= 2:
        return blocks

    # 3) Запасной вариант: режем по осмысленным элементам верхнего уровня
    # (заголовок страницы + контейнер контента и т.п.).
    top = [c for c in soup.contents if getattr(c, 'name', None) and meaningful(c)]
    if 2 <= len(top) <= 12:
        blocks = [{'name': name_for(' '.join(c.get('class') or []), i), 'html': serialize(c)}
                  for i, c in enumerate(top, 1)]
        blocks = [b for b in blocks if has_content(b['html'])]
        if len(blocks) >= 2:
            return blocks
    return blocks or None


def has_content(h):
    t = BeautifulSoup(h, 'html.parser').get_text(strip=True)
    return bool(t) or '<img' in h or '<iframe' in h


def main():
    shutil.copy(CONTENT, BACKUP)
    data = json.load(open(CONTENT, encoding='utf-8'))
    migrated = 0
    for page in data.get('pages', []):
        blocks = page.get('blocks')
        if blocks is not None and not (len(blocks) == 1 and blocks[0].get('type') == 'html'
                                       and blocks[0].get('legacy')):
            continue  # страница уже собрана из осмысленных блоков
        html = page.get('content_html') or (blocks and blocks[0].get('html')) or ''
        sections = split_page(html)
        if not sections:
            continue
        page['blocks'] = [{'type': 'section', 'name': s['name'], 'html': s['html']}
                          for s in sections]
        migrated += 1
        print(f"  {page['slug']}: {len(sections)} секций")
    json.dump(data, open(CONTENT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'Мигрировано страниц: {migrated}. Бэкап: {BACKUP}')


if __name__ == '__main__':
    sys.exit(main())
