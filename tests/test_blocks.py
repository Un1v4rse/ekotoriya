# -*- coding: utf-8 -*-
"""Рендер всех типов блоков конструктора (TS-1, регресс «НЕ ЛОМАТЬ» п.2)."""
import pytest

from blocks import render_block, render_blocks

MINIMAL_BLOCKS = {
    'heading': {'type': 'heading', 'level': 2, 'text': 'Заголовок'},
    'text': {'type': 'text', 'text': 'Просто **текст**.'},
    'image': {'type': 'image', 'src': '/static/img/x.png', 'alt': 'картинка'},
    'button': {'type': 'button', 'text': 'Купить', 'url': '/catalog/'},
    'video': {'type': 'video', 'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'},
    'products': {'type': 'products', 'title': 'Популярное', 'category': 'all'},
    'divider': {'type': 'divider'},
    'reviews': {'type': 'reviews', 'items': [{'name': 'Анна', 'text': 'Отлично'}]},
    'form': {'type': 'form', 'title': 'Заказать звонок'},
    'map': {'type': 'map', 'address': 'Москва'},
    'faq': {'type': 'faq', 'items': [{'q': 'Вопрос?', 'a': 'Ответ.'}]},
    'banner': {'type': 'banner', 'title': 'Баннер', 'text': 'Текст баннера'},
    'html': {'type': 'html', 'html': '<div>raw</div>'},
    'section': {'type': 'section', 'html': '<div class="raw">Секция целиком</div>'},
    'text_image': {'type': 'text_image', 'text': 'Текст', 'src': '/static/img/x.png'},
    'gallery': {'type': 'gallery', 'items': [
        {'src': '/static/img/a.png', 'alt': 'а'},
        {'src': '/static/img/b.png', 'alt': 'б'},
    ]},
    'features': {'type': 'features', 'items': [{'title': 'Ф1', 'text': 'Описание'}]},
    'table': {'type': 'table', 'header_row': True, 'items': [
        {'cells': 'A | B'},
        {'cells': ['1', '2']},
    ]},
    'spacer': {'type': 'spacer', 'height': 40},
    'text_columns': {'type': 'text_columns', 'items': [
        {'text': 'Колонка 1'}, {'text': 'Колонка 2'},
    ]},
    'contacts': {'type': 'contacts', 'phone': '+7 900 000-00-00'},
}


@pytest.mark.parametrize('btype', sorted(MINIMAL_BLOCKS))
def test_render_block_returns_html(btype):
    html = render_block(MINIMAL_BLOCKS[btype])
    assert isinstance(html, str)
    assert html.strip() != ''


def test_render_blocks_joins_all():
    html = render_blocks([MINIMAL_BLOCKS['heading'], MINIMAL_BLOCKS['text']])
    assert 'Заголовок' in html
    assert 'текст' in html


def test_render_block_unknown_type_safe():
    html = render_block({'type': 'nonexistent-type'})
    assert isinstance(html, str)


def test_products_block_renders_from_catalog():
    """products-блок подтягивает товары из БД (wb_sync.load_products)."""
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    html = render_block({'type': 'products', 'category': 'all'})
    assert html.strip() != ''
