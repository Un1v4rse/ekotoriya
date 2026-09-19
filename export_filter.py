# -*- coding: utf-8 -*-
"""Фильтр выгрузки товаров на сайт (WB-5, OZ-4).

Настройки режима выгрузки хранятся в site_settings (site.<source>_export):
    {
      "mode": "all" | "selected" | "category",
      "selected_ids": [123, 456],     # для mode=selected
      "category": "Наматрасники"      # для mode=category
    }

Товары, исключённые из выгрузки, на сайте не показываются, но в БД
остаются (Product.visible=False). При каждом синке флаг пересчитывается
по текущему режиму, поэтому возврат режима в «все» восстанавливает видимость.
"""
import storage


def get_export_settings(source):
    """Режим выгрузки для источника ('wb'|'ozon') с значениями по умолчанию."""
    settings = {'mode': 'all', 'selected_ids': [], 'category': ''}
    try:
        site = storage.get_setting('site') or {}
        saved = site.get(f'{source}_export') or {}
        settings.update(saved)
    except Exception:
        pass
    return settings


def is_visible(product, settings=None):
    """Показывать ли товар на сайте по режиму выгрузки."""
    if settings is None:
        settings = get_export_settings(product.get('source', 'wb'))
    mode = settings.get('mode', 'all')
    if mode == 'selected':
        ids = settings.get('selected_ids') or []
        pid = product.get('nm_id') or product.get('id')
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pass
        return pid in ids
    if mode == 'category':
        return str(product.get('category') or '') == str(settings.get('category') or '')
    return True


def reapply_visibility(source):
    """Пересчитать visible у всех товаров источника по текущему режиму (WB-5/OZ-4).

    Вызывается при смене режима выгрузки в админке — не дожидаясь синка.
    """
    from db import db
    from models import Product
    settings = get_export_settings(source)
    rows = db.session.query(Product).filter_by(source=source).all()
    for row in rows:
        row.visible = is_visible(row.to_product_dict(), settings)
    db.session.commit()
    return len(rows)


def apply_visibility(items, source):
    """Проставить visible каждому dict товара источника по режиму выгрузки."""
    settings = get_export_settings(source)
    for item in items:
        item['visible'] = is_visible(item, settings)
    return items
