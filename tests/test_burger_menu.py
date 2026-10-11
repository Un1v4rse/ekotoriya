# -*- coding: utf-8 -*-
"""Бургер-меню: структура как на оригинале (ekotoriya.ru).

Пункт с двухуровневой вложенностью (Каталог) раскрывается блоком на всю
ширину — дети колонками, внуки ссылками под заголовком каждой колонки;
остальные пункты идут сеткой. Меню без такой вложенности рендерится
обычной сеткой без каталог-блока.
"""
import pytest

import app as app_module
import customers
import storage

ORIGINAL_MENU = [
    {'title': 'Каталог', 'url': '/catalog/', 'children': [
        {'title': 'Наматрасники', 'url': '/catalog/namatrasniki/', 'children': [
            {'title': 'Наматрасники на резинке',
             'url': '/catalog/namatrasniki/namatrasniki-na-rezinke/'},
            {'title': 'Наматрасники на молнии',
             'url': '/catalog/namatrasniki/namatrasniki-na-molnii/'},
            {'title': 'Непромокаемые наматрасники',
             'url': '/catalog/namatrasniki/nepromokaemye-namatrasniki/'},
        ]},
        {'title': 'Одеяла', 'url': '/catalog/odeyala/', 'children': [
            {'title': 'Зимние', 'url': '/catalog/odeyala/zimnie/'},
            {'title': 'Всесезонные', 'url': '/catalog/odeyala/vsesezonnye/'},
            {'title': 'Летние', 'url': '/catalog/odeyala/letnie/'},
        ]},
        {'title': 'Наперники', 'url': '/catalog/naperniki/'},
    ]},
    {'title': 'Оптовым покупателям', 'url': '/help/opt/'},
    {'title': 'Компания', 'url': '/company/', 'children': [
        {'title': 'О компании', 'url': '/company/'},
        {'title': 'Отзывы', 'url': '/company/reviews/'},
        {'title': 'Документы', 'url': '/company/docs/'},
    ]},
    {'title': 'Наше производство', 'url': '/proizvodstvo/'},
    {'title': 'Советы покупателям', 'url': '/blog/'},
    {'title': 'Контакты', 'url': '/contacts/'},
]


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed_menu(menu=None):
    content = storage.load_content()
    content['menu'] = menu if menu is not None else ORIGINAL_MENU
    storage.save_content(content)
    app_module.invalidate_site_cache()


def test_burger_menu_catalog_block_with_columns(client):
    _seed_menu()
    html = client.get('/').data.decode('utf-8')
    # Каталог раскрыт блоком на всю ширину: три колонки детей
    # (кавычка в конце — чтобы не матчить имена классов в инлайн-CSS)
    assert 'wb-burger-catalog"' in html
    assert html.count('wb-burger-catcol"') == 3
    # внуки — ссылки с оригинальными адресами
    for url in ('/catalog/namatrasniki/namatrasniki-na-rezinke/',
                '/catalog/namatrasniki/namatrasniki-na-molnii/',
                '/catalog/namatrasniki/nepromokaemye-namatrasniki/',
                '/catalog/odeyala/zimnie/', '/catalog/odeyala/vsesezonnye/',
                '/catalog/odeyala/letnie/'):
        assert f'href="{url}"' in html
    assert 'Наматрасники на резинке' in html
    assert 'Непромокаемые наматрасники' in html
    assert 'Всесезонные' in html
    # Наперники — колонка без внуков
    assert 'href="/catalog/naperniki/"' in html


def test_burger_menu_other_items_in_grid(client):
    _seed_menu()
    html = client.get('/').data.decode('utf-8')
    # пункты первого уровня (кроме Каталога) остаются ячейками сетки
    for title, url in (('Оптовым покупателям', '/help/opt/'),
                       ('Наше производство', '/proizvodstvo/'),
                       ('Советы покупателям', '/blog/'),
                       ('Контакты', '/contacts/')):
        assert title in html
        assert f'href="{url}"' in html
    # у Компании дочерние ссылки раскрыты
    assert 'href="/company/reviews/"' in html
    assert 'href="/company/docs/"' in html
    # у пунктов сетки класс колонки col-md-4, у каталога — col-md-12
    assert 'menu-item col-md-4 unvisible' in html
    assert 'menu-item col-md-12 unvisible dropdown wb-burger-catalog' in html


def test_burger_menu_flat_menu_renders_grid_only(client):
    _seed_menu([{'title': 'Каталог', 'url': '/catalog/'},
                {'title': 'Контакты', 'url': '/contacts/'}])
    html = client.get('/').data.decode('utf-8')
    assert 'wb-burger-catalog"' not in html
    assert 'href="/contacts/"' in html
    assert 'href="/catalog/"' in html


def test_burger_menu_search_row(client):
    _seed_menu()
    html = client.get('/').data.decode('utf-8')
    # строка поиска меню: форма ведёт в каталог с параметром q
    assert 'wb-burger-search' in html
    assert 'action="/catalog/"' in html
    assert 'name="q"' in html
    # aspro-дубль поиска в меню скрыт
    assert '.mega_fixed_menu .left_menu_block > .search_block{display:none' in html


def test_burger_menu_right_column_guest(client):
    _seed_menu()
    html = client.get('/').data.decode('utf-8')
    assert 'wb-burger-right' in html
    assert 'ЗАДАТЬ ВОПРОС' in html
    assert 'href="/contacts/"' in html
    # сегмент правой колонки (в шапке/моб.меню aspro свои «ЛК» — их не трогаем)
    seg = html.split('wb-burger-right"')[1][:6000]
    assert 'ВОЙТИ' in seg
    assert 'href="/auth/"' in seg
    assert 'ЛИЧНЫЙ КАБИНЕТ' not in seg
    # корзина и избранное со счётчиками (те же классы-хуки, что в шапке)
    assert 'КОРЗИНА' in seg
    assert 'ИЗБРАННЫЕ ТОВАРЫ' in seg
    assert 'basket-link basket' in seg
    assert 'basket-link delay' in seg
    assert seg.count('class="count js-count empted"') == 2
    assert 'href="/basket/"' in seg
    assert 'href="/personal/favorite/"' in seg
    # телефон, email, адрес, соцсети
    assert 'tel:' in seg
    assert 'mailto:' in seg
    assert 'АДРЕС ПРОИЗВОДСТВА' in seg
    assert 'social-icons' in seg


def test_burger_menu_right_column_authenticated(client):
    _seed_menu()
    customer = customers.add_customer('Тест', '+7 900 300-00-00')
    with client.session_transaction() as s:
        s['customer_id'] = customer['id']
    html = client.get('/').data.decode('utf-8')
    # авторизованный видит «Личный кабинет» вместо «Войти»
    seg = html.split('wb-burger-right"')[1][:6000]
    assert 'ЛИЧНЫЙ КАБИНЕТ' in seg
    assert 'href="/personal/"' in seg
    assert 'href="/auth/"' not in seg
