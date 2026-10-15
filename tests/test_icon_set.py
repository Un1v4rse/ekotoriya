# -*- coding: utf-8 -*-
"""Иконки конструктора (цикл 4 / этап 5): встроенный набор SVG + своя загрузка.

Покрытие:
- icon:<имя> рендерит inline-SVG из набора (в блоке «Преимущества» и в zero-блоке);
- неизвестное имя набора — пусто, без ошибки;
- ссылка на свой файл — <img>; эмодзи — текстовый блок;
- SVG принимает цвет элемента (stroke=currentColor) и заданный размер;
- HTML-инъекция через поле иконки невозможна.
"""
import blocks as blocks_module
from blocks import SVG_ICON_NAMES, _icon_html, _svg_icon_html


def test_svg_icon_set_nonempty_and_unique_names():
    assert len(SVG_ICON_NAMES) >= 15
    assert len(SVG_ICON_NAMES) == len(set(SVG_ICON_NAMES))
    for name in ('truck', 'shield', 'percent', 'star', 'phone'):
        assert name in SVG_ICON_NAMES


def test_svg_icon_html_renders_svg():
    html = _svg_icon_html('truck')
    assert html.startswith('<svg')
    assert '</svg>' in html
    assert 'stroke="currentColor"' in html
    assert 'width="48"' in html


def test_svg_icon_html_unknown_name_empty():
    assert _svg_icon_html('nope') == ''
    assert _svg_icon_html('') == ''


def test_svg_icon_size_clamped():
    html = _svg_icon_html('star', size=5000)
    assert 'width="120"' in html
    html = _svg_icon_html('star', size=1)
    assert 'width="12"' in html


def test_icon_html_by_value_type():
    # набор
    assert _icon_html('icon:shield').startswith('<svg')
    # своя загрузка (ссылка)
    img = _icon_html('/static/upload/my-icon.svg')
    assert img.startswith('<img') and 'src="/static/upload/my-icon.svg"' in img
    # эмодзи
    assert '🚚' in _icon_html('🚚')
    # пусто
    assert _icon_html('') == ''
    # HTML не проходит
    assert '<script>' not in _icon_html('<script>')


def test_features_block_with_svg_icon():
    html = blocks_module.render_block({
        'type': 'features',
        'items': [{'title': 'Доставка', 'text': 'По России', 'icon': 'icon:truck'}],
    })
    assert '<svg' in html and 'Доставка' in html


def test_zero_block_icon_element_svg():
    html = blocks_module.render_block({
        'type': 'zero',
        'elements': [
            {'id': 'i1', 'type': 'icon', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
             'props': {'icon': 'icon:star', 'color': '#b49d84', 'size': 24}},
        ],
    })
    assert '<svg' in html
    assert 'color:#b49d84' in html


def test_zero_block_icon_element_emoji_still_works():
    html = blocks_module.render_block({
        'type': 'zero',
        'elements': [
            {'id': 'i1', 'type': 'icon', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
             'props': {'icon': '★', 'size': 32}},
        ],
    })
    assert '★' in html


def test_zero_block_icon_element_rejects_html():
    html = blocks_module.render_block({
        'type': 'zero',
        'elements': [
            {'id': 'i1', 'type': 'icon', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
             'props': {'icon': '<script>alert(1)</script>'}},
        ],
    })
    assert '<script>' not in html
