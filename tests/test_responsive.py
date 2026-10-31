# -*- coding: utf-8 -*-
"""Адаптивность (Приоритет 3): responsive overrides и custom preview.

- Desktop — базовое значение, старые блоки без responsive не меняются.
- Tablet (≤1024px) и mobile (≤640px) overrides: hide, padding, align,
  min-height.
- Блок получает уникальный класс cb-{uid}, к которому привязываются
  media-query стили.
"""
import re

import blocks as blocks_module


def test_responsive_hide_generates_media_query():
    css = blocks_module._responsive_css({'responsive': {'mobile': {'hide': True}}}, 'abc123')
    assert '@media (max-width:640px)' in css
    assert '.cb-abc123' in css
    assert 'display:none' in css


def test_responsive_padding_and_align():
    css = blocks_module._responsive_css({
        'responsive': {
            'tablet': {'padding_top': 10, 'padding_bottom': 20, 'align': 'center'},
            'mobile': {'min_height': 300},
        }
    }, 'u1')
    assert '@media (max-width:1024px)' in css
    assert 'padding-top:10px' in css
    assert 'padding-bottom:20px' in css
    assert 'text-align:center' in css
    assert '@media (max-width:640px)' in css
    assert 'min-height:300px' in css


def test_no_responsive_returns_empty():
    assert blocks_module._responsive_css({}, 'u1') == ''
    assert blocks_module._responsive_css({'responsive': None}, 'u1') == ''


def test_block_wrapper_adds_uid_class_and_responsive_style():
    html = blocks_module._block_wrapper(
        {'type': 'text', 'responsive': {'mobile': {'hide': True}}},
        '<p>hi</p>',
        uid='testuid'
    )
    assert 'cb-testuid' in html
    assert '@media (max-width:640px)' in html
    assert 'display:none' in html


def test_legacy_block_without_responsive_has_no_responsive_html():
    html = blocks_module._block_wrapper({'type': 'text'}, '<p>hi</p>', uid='legacy')
    assert '@media' not in html
