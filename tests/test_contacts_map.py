# -*- coding: utf-8 -*-
"""Цикл 4, этап 3: страница «Контакты» — чистый email-якорь и Яндекс.Карта.

Карта инжектится в рендер (маркер wb-contacts-map, идемпотентно), адрес для
поиска — из настройки site.map_query (дефолт: Череповец, ул. Гоголя 56).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module

BROKEN_EMAIL = (
    '<a href=\'mailto:&lt;a href="mailto:HelloEkotoriya@yandex.ru"&gt;'
    'helloekotoriya@yandex.ru&lt;/a&gt;\'>'
    '<a href="mailto:HelloEkotoriya@yandex.ru">helloekotoriya@yandex.ru</a></a>')
DEFAULT_SITE = {'phone': '8 800 201 08 24',
                'email': 'helloekotoriya@yandex.ru',
                'address': 'г. Череповец, ул. Гоголя 56'}


def test_broken_email_anchor_fixed():
    out = app_module._sync_contacts_html(f'<div>{BROKEN_EMAIL}</div>', DEFAULT_SITE)
    # вложенный mailto с экранированным HTML исчез, остался один чистый якорь
    assert 'mailto:&lt;' not in out
    assert out.count('<a href="mailto:') == 1
    assert 'mailto:helloekotoriya@yandex.ru' in out


def test_map_injected_once_with_default_address():
    out = app_module._sync_contacts_html('<div>info</div>', DEFAULT_SITE)
    assert out.count('wb-contacts-map') == 1
    assert 'yandex.ru/map-widget/v1/' in out
    # дефолтный адрес закодирован в параметре text=
    assert '%D0%A7%D0%B5%D1%80%D0%B5%D0%BF%D0%BE%D0%B2%D0%B5%D1%86' in out  # Череповец
    # повторный прогон не плодит блоки
    again = app_module._sync_contacts_html(out, DEFAULT_SITE)
    assert again.count('wb-contacts-map') == 1


def test_map_query_from_site_settings():
    site = dict(DEFAULT_SITE, map_query='Вологда, улица Ленина, 1')
    out = app_module._sync_contacts_html('<div>info</div>', site)
    assert '%D0%92%D0%BE%D0%BB%D0%BE%D0%B3%D0%B4%D0%B0' in out  # Вологда


def test_map_query_sanitized():
    # кавычки/теги в настройке не должны ломать разметку
    site = dict(DEFAULT_SITE, map_query='"><script>alert(1)</script>')
    out = app_module._sync_contacts_html('<div>info</div>', site)
    assert '<script>alert' not in out.split('wb-contacts-map')[0][-200:] \
        or '<script>alert' not in out
    assert '<script>alert(1)</script>' not in out
