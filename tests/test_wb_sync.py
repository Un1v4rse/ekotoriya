# -*- coding: utf-8 -*-
"""WB-интеграция в демо-режиме (WB-1, WB-2, TS-1): синк в БД, скидки, журнал."""
import wb_sync


def test_demo_sync_persists_products_to_db():
    result = wb_sync.sync(token=None, demo=True)
    assert result['success'] and result['demo']
    products = wb_sync.load_products()
    assert result['count'] == len(products) > 0
    for p in products:
        assert p.get('id')
        assert p.get('name')
        assert 'price' in p
        assert p.get('source') == 'wb'


def test_admin_discount_survives_resync():
    wb_sync.sync(token=None, demo=True)
    target = wb_sync.load_products()[0]
    wb_sync.set_admin_discount(target['nm_id'], 15)

    wb_sync.sync(token=None, demo=True)  # повторный синк из «маркетплейса»

    updated = next(p for p in wb_sync.load_products() if p['nm_id'] == target['nm_id'])
    assert updated['admin_discount_percent'] == 15
    # итоговая цена (старая формула этапа 1) ниже базовой
    assert wb_sync.final_price(updated) < updated['price']


def test_set_admin_discount_zero_clears():
    wb_sync.sync(token=None, demo=True)
    target = wb_sync.load_products()[0]
    wb_sync.set_admin_discount(target['nm_id'], 10)
    wb_sync.set_admin_discount(target['nm_id'], 0)
    updated = next(p for p in wb_sync.load_products() if p['nm_id'] == target['nm_id'])
    assert updated['admin_discount_percent'] == 0


def test_sync_log_written_to_db():
    wb_sync.sync(token=None, demo=True)
    log = wb_sync.get_sync_log(limit=10)
    assert len(log) >= 1
    entry = log[0]
    assert entry.get('time')
    assert entry.get('status') in ('ok', 'error')
    assert entry.get('source') == 'wb'


def test_sync_light_keeps_products():
    wb_sync.sync(token=None, demo=True)
    before = {p['nm_id'] for p in wb_sync.load_products()}
    wb_sync.sync_light(token=None)
    after = {p['nm_id'] for p in wb_sync.load_products()}
    assert before == after


def test_app_level_products_have_final_price():
    """get_products() (кэш app.py) добавляет final_price к каждому товару."""
    import app as app_module
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    products = app_module.get_products()
    assert products
    for p in products:
        assert 'final_price' in p
        if p.get('price'):
            assert p['final_price'] <= p['price']
