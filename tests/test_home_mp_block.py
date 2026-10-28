# -*- coding: utf-8 -*-
"""Главная: блок «Товары на маркетплейсах» без отсутствующих товаров.

Замечание заказчика (2026-10-28): на главной рекомендовались товары «Нет в
наличии» с неактивной кнопкой «Купить в 1 клик». Фильтр (mp_block_products
в make_context) действует ТОЛЬКО на главной; единый каталог /catalog/ по-прежнему
показывает все товары — их можно найти там.
"""
import pytest

import app as app_module
from db import db
from models import Product


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _sync_demo():
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def test_home_excludes_out_of_stock_but_catalog_keeps_them(client):
    _sync_demo()
    victim = None
    for p in db.session.query(Product).all():
        if (p.stock or 0) > 0:
            victim = p
            break
    if victim is None:
        victim = db.session.query(Product).first()
    victim.stock = 0
    db.session.commit()
    app_module.invalidate_site_cache()

    pid = str(victim.nm_id or victim.id)
    name = str(victim.name or '')

    # главная: товара нет ни в одном блоке, бейджей «Нет в наличии» нет
    r = client.get('/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'Нет в наличии' not in body
    if name:
        assert name not in body

    # товар по-прежнему доступен (искать/открывать можно в каталоге)
    r = client.get(f'/catalog/{victim.source or "wb"}/{pid}/')
    assert r.status_code == 200
