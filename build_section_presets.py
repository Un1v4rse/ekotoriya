#!/usr/bin/env python3
"""Собрать data/section_presets.json — пресеты секций дизайна для библиотеки
конструктора (CN-T1), чтобы любую секцию сайта можно было создать с нуля.

Берём канонические секции из data/content.json: для каждого CSS-класса из
SECTION_NAMES — первую непустую секцию, у которой этот класс есть в html.
Пустые секции (плейсхолдеры ленивых блоков) тоже включаем — в их html
сохраняется data-file для ленивой загрузки на странице.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / 'data' / 'content.json'
OUT = ROOT / 'data' / 'section_presets.json'

# CSS-класс (верхний регистр) → читаемое имя. Порядок важен: более специфичные
# ключи (VK_VIDEO) идут раньше общих (VK).
SECTION_NAMES = [
    ('TOP_BIG_BANNERS', 'Главный баннер'),
    ('BIG_BANNER_INDEX', 'Главный баннер'),
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
    ('CATALOG_SECTION_LIST', 'Разделы каталога'),
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

CLASS_ATTR_RE = re.compile(r'class="([^"]*)"', re.I)


def class_tokens(html):
    """Все токены CSS-классов html в верхнем регистре."""
    tokens = set()
    for m in CLASS_ATTR_RE.finditer(html or ''):
        tokens.update(t.strip().upper() for t in m.group(1).split())
    return tokens


def sections_by_class(pages):
    """Первую непустую секцию для каждого ключа SECTION_NAMES: {key: html}."""
    found = {}
    for page in pages:
        for block in page.get('blocks', []):
            if block.get('type') != 'section':
                continue
            html = block.get('html') or ''
            if not html.strip():
                continue
            tokens = class_tokens(html)
            for key, _label in SECTION_NAMES:
                if key not in found and key in tokens:
                    found[key] = html
    return found


def page_sections(pages, slug):
    return [b for b in next(
        (p.get('blocks', []) for p in pages if p.get('slug') == slug), [])
        if b.get('type') == 'section']


def strip_html(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html or '')).strip()


def extra_presets(pages):
    """Типовые внутристраничные секции, собранные по смыслу."""
    extras = []

    # SEO-текст (/catalog/): первый <h2> + первый абзац после него.
    h2_html = p_html = None
    seen_h2 = False
    for b in page_sections(pages, 'catalog'):
        html = (b.get('html') or '').strip()
        if not html:
            continue
        if h2_html is None and re.match(r'<h2[>\s]', html, re.I):
            h2_html = html
            seen_h2 = True
            continue
        if seen_h2 and h2_html is not None and p_html is None and re.match(r'<p[>\s]', html, re.I):
            p_html = html
            break
    if h2_html:
        extras.append({'id': 'seo_text', 'name': 'SEO-текст',
                       'html': h2_html + ('\n' + p_html if p_html else '')})

    # FAQ-аккордеон и форма обратной связи (/help/faq/).
    for b in page_sections(pages, 'help__faq'):
        html = b.get('html') or ''
        if not any(e['id'] == 'faq_accordion' for e in extras) and 'accordion' in html.lower():
            extras.append({'id': 'faq_accordion', 'name': 'FAQ-аккордеон', 'html': html})
        if not any(e['id'] == 'feedback_form' for e in extras) and '<form' in html.lower():
            extras.append({'id': 'feedback_form', 'name': 'Форма обратной связи', 'html': html})

    # Видео-галерея VK (/proizvodstvo/): секции с iframe video_ext.php.
    for b in page_sections(pages, 'proizvodstvo'):
        html = b.get('html') or ''
        if 'video_ext' in html:
            extras.append({'id': 'vk_video_gallery', 'name': 'Видео-галерея VK', 'html': html})
            break

    return [e for e in extras if e['html'].strip()]


def main():
    content = json.loads(CONTENT.read_text(encoding='utf-8'))
    pages = content.get('pages', [])

    presets = []
    used_ids = set()
    used_names = set()
    used_html = set()
    by_class = sections_by_class(pages)
    for key, label in SECTION_NAMES:
        html = by_class.get(key)
        if not html or label in used_names or html in used_html:
            continue
        pid = key.lower()
        if pid in used_ids:
            pid = pid + '_2'
        used_ids.add(pid)
        used_names.add(label)
        used_html.add(html)
        presets.append({'id': pid, 'name': label, 'html': html})

    for e in extra_presets(pages):
        if e['id'] in used_ids:
            continue
        used_ids.add(e['id'])
        presets.append(e)

    OUT.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(presets)} пресетов → {OUT}')
    for p in presets:
        print(f"  {p['id']}: {p['name']} ({strip_html(p['html'])[:60] or 'плейсхолдер / lazy-блок'})")


if __name__ == '__main__':
    main()
