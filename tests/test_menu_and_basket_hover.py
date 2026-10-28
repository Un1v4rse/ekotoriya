# -*- coding: utf-8 -*-
"""Фикс UI-бага: попап корзины при наведении (aspro basketTop).

Заказчик (живой сайт 185.56.162.91): при наведении на иконку корзины aspro-JS
постил на /ajax/showBasketHover.php, роута не было — catch-all отдавал полную
404-страницу внутри попапа («сломанное окно»).

Внимание: попытка сделать «Каталог» широким мега-меню как на оригинале
(wide_menu + картинки) заказчиком отклонена — вернули прежний узкий dropdown
во всех пунктах (git checkout HEAD~2 -- templates/site_menu.html, enrich
дефолты удалены). Этот файл больше не тестирует меню.
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


# ---------- инжект CSS фиксов ----------

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
    assert 'wb-bh-item' in body


def test_basket_hover_get_also_works(client):
    r = client.get('/ajax/showBasketHover.php/')
    assert r.status_code == 200
    assert 'В корзине пока пусто' in r.get_data(as_text=True)


def test_basket_page_row_items_centered(client):
    """Позиции строки корзины выровнены по центру по вертикали.

    Было vertical-align: top — название/цена/поле количества/сумма «прилипали»
    к верху строки (замечание заказчика, скриншот 2).
    """
    _sync_demo()
    client.post('/api/cart/add', json={'product_id': '890129778', 'source': 'wb'})
    r = client.get('/basket/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'vertical-align: middle' in body
