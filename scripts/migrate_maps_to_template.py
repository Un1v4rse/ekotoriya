# -*- coding: utf-8 -*-
"""Вырезать MAPS-блок (Контакты/карта) из данных страницы index в БД.

Секция MAPS перенесена в templates/pages/index.html (перед footer) —
статично, в самом низу главной. Чтобы она не дублировалась из БД
и не была доступна в конструкторе как переставляемый блок, блок MAPS
удаляется из content_html, blocks, draft_blocks, published_blocks.

Применение на проде:
    docker compose exec app python scripts/migrate_maps_to_template.py
"""
import json
import re
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else '/app/data/ekotoriya.db'


def cut_maps_html(html):
    """Удалить <div class="drag-block container MAPS ...">...</div> (с балансировкой div)."""
    if not html or 'MAPS' not in html:
        return html, False
    start = html.find('<div class="drag-block container MAPS')
    if start == -1:
        return html, False
    depth = 0
    for m in re.finditer(r'<div\b|</div>', html[start:], re.I):
        if m.group(0).lower().startswith('<div'):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return html[:start] + html[start + m.end():], True
    return html, False


def cut_maps_blocks_json(raw):
    """Удалить блок MAPS из JSON-списка блоков."""
    if not raw:
        return raw, False
    try:
        blocks = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(blocks, list):
        return raw, False
    new = [b for b in blocks if not (isinstance(b, dict) and 'MAPS' in (b.get('html') or ''))]
    if len(new) == len(blocks):
        return raw, False
    return json.dumps(new, ensure_ascii=False), True


def cut_maps_draft_json(raw):
    """Удалить блок MAPS из draft_blocks ({blocks: [...], saved_at})."""
    if not raw:
        return raw, False
    try:
        d = json.loads(raw)
    except Exception:
        return raw, False
    if not isinstance(d, dict) or not isinstance(d.get('blocks'), list):
        return raw, False
    new, changed = cut_maps_blocks_json(json.dumps(d['blocks'], ensure_ascii=False))
    if not changed:
        return raw, False
    d['blocks'] = json.loads(new)
    return json.dumps(d, ensure_ascii=False), True


def main():
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT id, content_html, blocks, draft_blocks, published_blocks FROM pages WHERE slug='index'"
    ).fetchone()
    if not row:
        print('page index not found')
        return
    page_id, content_html, blocks, draft_blocks, published_blocks = row

    content_html, ch = cut_maps_html(content_html)
    blocks, cb = cut_maps_blocks_json(blocks)
    draft_blocks, db_ = cut_maps_draft_json(draft_blocks)
    published_blocks, pb = cut_maps_blocks_json(published_blocks)

    if ch or cb or db_ or pb:
        conn.execute(
            "UPDATE pages SET content_html=?, blocks=?, draft_blocks=?, published_blocks=? WHERE id=?",
            (content_html, blocks, draft_blocks, published_blocks, page_id),
        )
        conn.commit()
    print('content_html:', ch, '| blocks:', cb, '| draft_blocks:', db_, '| published_blocks:', pb)

    row = conn.execute(
        "SELECT content_html, blocks, draft_blocks, published_blocks FROM pages WHERE slug='index'"
    ).fetchone()
    for name, val in zip(['content_html', 'blocks', 'draft_blocks', 'published_blocks'], row):
        print(name, 'MAPS present:', 'MAPS' in (val or ''))
    conn.close()


if __name__ == '__main__':
    main()
