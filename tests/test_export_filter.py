# -*- coding: utf-8 -*-
"""Фильтр выгрузки (WB-5, OZ-4) и цикл «синк → каталог → заявка» (TS-2)."""
import storage
import wb_sync
import export_filter


def _sync_demo():
    return wb_sync.sync(token=None, demo=True)


def test_export_mode_all_shows_everything():
    _sync_demo()
    settings = {'mode': 'all', 'selected_ids': [], 'category': ''}
    products = wb_sync.load_products()
    assert all(export_filter.is_visible(p, settings) for p in products)


def test_export_mode_selected():
    _sync_demo()
    products = wb_sync.load_products()
    keep = products[0]['nm_id']
    settings = {'mode': 'selected', 'selected_ids': [keep], 'category': ''}
    visible_ids = [p['nm_id'] for p in products if export_filter.is_visible(p, settings)]
    assert visible_ids == [keep]


def test_export_mode_category():
    products = [
        {'nm_id': 1, 'source': 'wb', 'category': 'Наматрасники'},
        {'nm_id': 2, 'source': 'wb', 'category': 'Подушки'},
    ]
    settings = {'mode': 'category', 'selected_ids': [], 'category': 'Наматрасники'}
    assert export_filter.is_visible(products[0], settings)
    assert not export_filter.is_visible(products[1], settings)


def test_reapply_visibility_hides_from_site_catalog():
    """Скрытые товары не попадают в get_products() админа/сайта (WB-5)."""
    import app as app_module
    _sync_demo()
    target = wb_sync.load_products()[0]
    content = storage.load_content()
    content.setdefault('site', {})['wb_export'] = {
        'mode': 'selected', 'selected_ids': [], 'category': '',
    }
    storage.save_content(content)
    export_filter.reapply_visibility('wb')
    app_module.invalidate_site_cache()
    site_products = app_module.get_products()
    assert all(p['nm_id'] != target['nm_id'] for p in site_products)
    # все товары остались в БД
    assert len(wb_sync.load_products()) >= 8
    # возврат режима «все» восстанавливает видимость
    content = storage.load_content()
    content['site']['wb_export'] = {'mode': 'all', 'selected_ids': [], 'category': ''}
    storage.save_content(content)
    export_filter.reapply_visibility('wb')
    app_module.invalidate_site_cache()
    assert any(p['nm_id'] == target['nm_id'] for p in app_module.get_products())


def test_hide_zero_stock_setting():
    """Настройка site.hide_zero_stock скрывает товары без остатка (CT-4)."""
    import app as app_module
    from db import db
    from models import Product
    _sync_demo()
    with app_module.app.app_context():
        db.session.query(Product).filter_by(source='wb').update({'stock': 0})
        db.session.commit()
    app_module.invalidate_site_cache()
    # по умолчанию — показываются
    assert len(app_module.get_products()) >= 8
    content = storage.load_content()
    content.setdefault('site', {})['hide_zero_stock'] = True
    storage.save_content(content)
    app_module.invalidate_site_cache()
    assert app_module.get_products() == []
    content['site']['hide_zero_stock'] = False
    storage.save_content(content)
    app_module.invalidate_site_cache()


def test_full_cycle_sync_catalog_order():
    """Цикл: демо-синк → страница каталога → заявка → клиент в базе (TS-2)."""
    import app as app_module
    import customers
    _sync_demo()
    app_module.invalidate_site_cache()
    client = app_module.app.test_client()
    r = client.get('/catalog/')
    assert r.status_code == 200
    assert 'Наматрасник'.encode('utf-8') in r.data
    nm = wb_sync.load_products()[0]['nm_id']
    r = client.get(f'/catalog/wb/{nm}/')
    assert r.status_code == 200
    r = client.post('/api/order', json={
        'name': 'Цикл Тестов', 'phone': '+7 900 777-66-55',
        'product_id': str(nm), 'product_name': 'Наматрасник', 'price': 1000,
    })
    assert r.status_code == 200
    customer = customers.get_customer_by_phone('+79007776655')
    assert customer is not None
    assert customers.get_customer_stats(customer['id'])['orders_count'] == 1


def test_catalog_filters_and_search():
    """Каталог: фильтр по источнику, поиск, сортировка по цене (CT-1, CT-5)."""
    import app as app_module
    _sync_demo()
    app_module.invalidate_site_cache()
    client = app_module.app.test_client()
    # только WB
    r = client.get('/catalog/?source=wb')
    assert r.status_code == 200
    # поиск
    r = client.get('/catalog/?q=200х200')
    assert r.status_code == 200
    assert '200х200'.encode('utf-8') in r.data
    # сортировка по цене
    r = client.get('/catalog/?sort=price_asc')
    assert r.status_code == 200
    # фильтр цены
    r = client.get('/catalog/?price_from=2500&price_to=3000')
    assert r.status_code == 200
