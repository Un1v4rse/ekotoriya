# -*- coding: utf-8 -*-
"""Клиентская база: нормализация телефонов, дедупликация, события, популярное (CL-1, CL-2, TS-1)."""
import customers


def test_normalize_phone_strips_formatting():
    assert customers.normalize_phone('+7 (900) 123-45-67') == '+79001234567'
    assert customers.normalize_phone('8 900 123 45 67') == '+79001234567'
    assert customers.normalize_phone('900 123 45 67') == '+79001234567'
    assert customers.normalize_phone('') == ''
    assert customers.normalize_phone(None) == ''


def test_add_customer_dedup_by_phone():
    c1 = customers.add_customer('Иван Иванов', '+7 (900) 111-22-33')
    c2 = customers.add_customer('Иван И.', '89001112233', email='ivan@example.com')
    assert c1['id'] == c2['id']
    assert len(customers.get_customers()) == 1


def test_add_order_creates_customer_and_order():
    order = customers.add_order(
        name='Пётр Петров', phone='+7 900 222-33-44',
        email='', product_id='wb-1', product_name='Наматрасник', price=1000, qty=2,
    )
    assert order['id']
    customer = customers.get_customer_by_phone('+79002223344')
    assert customer is not None
    stats = customers.get_customer_stats(customer['id'])
    assert stats['orders_count'] == 1
    assert stats['total_spent'] == 2000


def test_add_order_links_existing_customer_by_phone():
    c = customers.add_customer('Сидорова Анна', '+7 900 333-44-55')
    customers.add_order('Анна Сидорова', '+7 (900) 333-44-55', '', 'wb-2', 'Подушка', 500)
    customer = customers.get_customer_by_phone('+79003334455')
    assert customer['id'] == c['id']
    assert len(customers.get_customer_orders(customer['id'])) == 1


def test_likes_events_and_counts():
    c = customers.add_customer('Лайкер', '+7 900 444-55-66')
    customers.add_like('890129778', 'Наматрасник', customer_id=c['id'], session_id='s1')
    customers.add_like('890129778', 'Наматрасник', session_id='s2')
    customers.add_like('890130000', 'Подушка', session_id='s2')
    counts = customers.get_like_counts()
    assert counts.get(890129778) == 2
    assert counts.get(890130000) == 1
    likes = customers.get_customer_likes(c['id'])
    assert len(likes) == 1
    assert str(likes[0]['product_id']) == '890129778'


def test_like_dedup_same_session_and_customer():
    """Повторный лайк той же сессией/клиентом не накручивает счётчик."""
    assert customers.add_like('777000111', 'Товар', session_id='sess-x') is True
    assert customers.add_like('777000111', 'Товар', session_id='sess-x') is False
    c = customers.add_customer('Дедуп', '+7 900 444-55-77')
    assert customers.add_like('777000222', 'Товар2', customer_id=c['id']) is True
    assert customers.add_like('777000222', 'Товар2', customer_id=c['id']) is False
    counts = customers.get_like_counts()
    assert counts.get(777000111) == 1
    assert counts.get(777000222) == 1
    # другая сессия/клиент — отдельный лайк
    assert customers.add_like('777000111', 'Товар', session_id='sess-y') is True
    assert customers.get_like_counts().get(777000111) == 2


def test_add_event_generic():
    c = customers.add_customer('События', '+7 900 555-66-77')
    customers.add_event('view', product_id='890129778', customer_id=c['id'])
    customers.add_event('search', query='наматрасник', customer_id=c['id'])
    # события не падают и не портят статистику заказов
    stats = customers.get_customer_stats(c['id'])
    assert stats['orders_count'] == 0
    assert stats['total_spent'] == 0


def test_get_popular_products_uses_likes():
    products = [
        {'nm_id': 890129778, 'name': 'Наматрасник'},
        {'nm_id': 890130000, 'name': 'Подушка'},
    ]
    customers.add_like('890129778', 'Наматрасник', session_id='a')
    customers.add_like('890129778', 'Наматрасник', session_id='b')
    popular = customers.get_popular_products(products, limit=8)
    assert popular[0]['product']['nm_id'] == 890129778
    assert popular[0]['likes'] == 2
    assert popular[0]['score'] == 2


def test_get_popular_products_uses_orders():
    products = [{'nm_id': 890140000, 'name': 'Одеяло'}]
    customers.add_order('Покупатель', '+7 900 666-77-88', '', '890140000', 'Одеяло', 3000)
    popular = customers.get_popular_products(products, limit=8)
    assert popular[0]['product']['nm_id'] == 890140000
    assert popular[0]['orders'] == 1
