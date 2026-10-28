# -*- coding: utf-8 -*-
"""Фиксы UI-багов: попап корзины при наведении + широкое мега-меню каталога.

Заказчик (живой сайт 185.56.162.91):
1. При наведении на иконку корзины aspro-JS постил на /ajax/showBasketHover.php,
   роута не было — catch-all отдавал полную 404-страницу внутри попапа
   («сломанное окно»).
2. Кнопка КАТАЛОГ раскрывала узкий dropdown с пустым пространством внизу —
   на оригинале это широкое мега-меню (td.wide_menu, колонки с картинками).
"""
import pytest

import app as app_module


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _sync_demo():
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


# ---------- _enrich_menu_defaults ----------

def test_enrich_sets_wide_and_images_only_when_empty():
    menu = [
        {'title': 'Каталог', 'url': '/catalog/', 'children': [
            {'title': 'Наматрасники', 'url': '/catalog/namatrasniki/'},
            {'title': 'Одеяла', 'url': '/catalog/odeyala/', 'image': '/custom/odeyala.png'},
        ]},
        {'title': 'Компания', 'url': '/company/'},
    ]
    app_module._enrich_menu_defaults(menu)
    cat = menu[0]
    assert cat['wide'] is True                      # /catalog/ с детьми → wide_menu
    assert cat['children'][0]['image']              # дефолт подставился
    assert cat['children'][0]['image'].endswith('.jpg')
    assert cat['children'][1]['image'] == '/custom/odeyala.png'  # не перезаписали
    assert 'wide' not in menu[1]                    # не-каталог не тронут


def test_enrich_ignores_non_dicts_and_no_children():
    app_module._enrich_menu_defaults(None)
    app_module._enrich_menu_defaults([None, 'x'])
    app_module._enrich_menu_defaults([{'title': 'Каталог', 'url': '/catalog/'}])
    # без детей wide не ставится
    app_module._enrich_menu_defaults([{'title': 'Каталог', 'url': '/catalog/',
                                       'children': [None, {'url': '/x/'}]}])


# ---------- рендер верхнего меню ----------

def test_site_menu_renders_wide_menu_and_images(client):
    # фикстура даёт минимальное меню — задаём каталог с детьми, как в проде
    content = app_module.load_content()
    content['menu'] = [
        {'title': 'Каталог', 'url': '/catalog/', 'children': [
            {'title': 'Наматрасники', 'url': '/catalog/namatrasniki/'},
            {'title': 'Одеяла', 'url': '/catalog/odeyala/'},
            {'title': 'Наперники', 'url': '/catalog/naperniki/'},
        ]},
    ]
    app_module.save_content(content)
    r = client.get('/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'wide_menu' in body          # Каталог — широкое мега-меню
    assert 'has_img' in body            # колонки с картинками разделов
    assert 'menu_img' in body
    assert 'trianglearrow_sprite.svg' in body  # стрелки у подразделов с детьми


def test_main_page_injects_site_fixes_css(client):
    r = client.get('/')
    body = r.get_data(as_text=True)
    assert '/static/css/site-fixes.css' in body
    assert 'site-fixes.css?v=' in body   # с версией по mtime (обход кэша)


# ---------- попап корзины /ajax/showBasketHover.php ----------

def test_basket_hover_empty(client):
    r = client.post('/ajax/showBasketHover.php')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'В корзине пока пусто' in body
    assert 'Страница не найдена' not in body   # раньше сюда падала 404-страница
    assert '<header' not in body                # попап — не полная страница


def test_basket_hover_with_items(client):
    _sync_demo()
    client.post('/api/cart/add', json={'product_id': '890129778', 'source': 'wb', 'qty': 2})
    r = client.post('/ajax/showBasketHover.php')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'В корзине пока пусто' not in body
    assert 'Итого' in body
    assert '/catalog/wb/890129778/' in body
    # та же корзина, что видит страница /basket/
    assert 'wb-bh-item' in body


def test_basket_hover_get_also_works(client):
    r = client.get('/ajax/showBasketHover.php/')
    assert r.status_code == 200
    assert 'В корзине пока пусто' in r.get_data(as_text=True)
