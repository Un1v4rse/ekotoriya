#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск и удаление товаров-заглушек из каталога.

Критерии заглушки (все одновременно, чтобы не задеть живые товары):
  - остаток stock == 0 (товар не продаётся), И
  - нет фото (photo пустое), ИЛИ
  - цена price <= 0 (в т.ч. слитые из WB «нулевые» карточки).

Только такие карточки выглядят на сайте как «заглушки»: битое/пустое фото
и «0 ₽». Реальные товары WB/Ozon (с фото и ценой) не трогаем, даже если
сейчас нет в наличии.

Использование:
    python stub_cleanup.py            # dry-run: только список кандидатов
    python stub_cleanup.py --delete   # удалить
"""
import sys

import db as db_module
import models  # noqa: F401  — регистрирует метаданные
from db import db
from models import Product


def find_stub_products():
    """Список товаров-заглушек: [{id, nm_id, name, reason}]."""
    rows = Product.query.all()
    stubs = []
    for p in rows:
        photo = (p.photo or '').strip()
        price = p.price or 0
        stock = p.stock if p.stock is not None else 0
        if stock != 0:
            continue
        reason = []
        if not photo:
            reason.append('no_photo')
        if price <= 0:
            reason.append('zero_price')
        if reason:
            stubs.append({'id': p.id, 'nm_id': p.nm_id,
                          'name': (p.name or '')[:80],
                          'reason': '+'.join(reason)})
    return stubs


def remove_stub_products():
    """Удалить заглушки. Возвращает список удалённых (как find_stub_products)."""
    stubs = find_stub_products()
    for s in stubs:
        p = db.session.get(Product, s['id'])
        if p is not None:
            db.session.delete(p)
    db.session.commit()
    return stubs


def main(argv):
    do_delete = '--delete' in argv
    app = db_module.standalone_app()
    with app.app_context():
        stubs = find_stub_products()
        print('Найдено заглушек: %d' % len(stubs))
        for s in stubs:
            print('  id=%s nm_id=%s %s [%s]' % (s['id'], s['nm_id'], s['name'], s['reason']))
        if not do_delete:
            print('Dry-run. Для удаления: python stub_cleanup.py --delete')
            return 0
        removed = remove_stub_products()
        print('Удалено: %d' % len(removed))
        for s in removed:
            print('  id=%s nm_id=%s %s' % (s['id'], s['nm_id'], s['name']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
