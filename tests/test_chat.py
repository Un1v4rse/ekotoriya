# -*- coding: utf-8 -*-
"""Этап 7 (цикл 4): чат клиент↔магазин (7.3–7.5), профиль (7.1), повтор заказа (7.2)."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import customers


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Rate-limit счётчики в памяти процесса — чистим перед каждым тестом."""
    app_module._rate_buckets.clear()
    yield


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture()
def admin_client():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    c = app_module.app.test_client()
    c.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    with c.session_transaction() as s:
        s['csrf_token'] = 'csrf'
    return c


def csrf():
    return {'X-CSRF-Token': 'csrf', 'Content-Type': 'application/json'}


def register(c, phone='+7 900 300-00-01', password='secret1', name='Клиент К'):
    r = c.post('/api/client/register', json={
        'name': name, 'phone': phone, 'password': password})
    assert r.get_json()['success'], r.get_json()
    return r.get_json()['customer']


# ---------- 7.3: отправка/получение, непрочитанные, ответ админа ----------

def test_chat_send_receive_and_admin_reply(client, admin_client):
    cust = register(client)
    r = client.post('/api/client/chat', json={'text': 'Здравствуйте! Вопрос по наматраснику.'})
    assert r.get_json()['success']

    r = client.get('/api/client/chat')
    data = r.get_json()
    assert data['success'] and len(data['messages']) == 1
    assert data['messages'][0]['sender'] == 'client'
    assert data['dialog']['status'] == 'open'

    # админ видит диалог с непрочитанным сообщением
    r = admin_client.get('/admin/api/chats')
    chats = r.get_json()['chats']
    assert len(chats) == 1
    assert chats[0]['unread'] == 1
    assert chats[0]['customer']['id'] == cust['id']
    cid = cust['id']

    # открытие истории помечает клиентские сообщения прочитанными
    r = admin_client.get('/admin/api/chat/{}'.format(cid))
    assert r.get_json()['success']
    r = admin_client.get('/admin/api/chats')
    assert r.get_json()['chats'][0]['unread'] == 0

    # ответ админа; у клиента он прочитан сразу после GET
    r = admin_client.post('/admin/api/chat/{}/reply'.format(cid),
                          json={'text': 'Добрый день! Конечно, подскажем.'},
                          headers=csrf())
    assert r.get_json()['success']
    r = client.get('/api/client/chat')
    msgs = r.get_json()['messages']
    assert len(msgs) == 2
    assert msgs[1]['sender'] == 'admin'
    assert msgs[1]['read_by_client']


def test_chat_client_sees_only_own_dialog(client):
    register(client, phone='+7 900 300-00-01', name='Клиент А')
    client.post('/api/client/chat', json={'text': 'Секретное сообщение А'})
    client.post('/api/client/logout')
    register(client, phone='+7 900 300-00-02', name='Клиент Б')
    r = client.get('/api/client/chat')
    data = r.get_json()
    assert data['success'] and data['messages'] == []


def test_chat_requires_login(client):
    assert client.get('/api/client/chat').status_code == 401
    assert client.post('/api/client/chat', json={'text': 'x'}).status_code == 401


def test_chat_admin_requires_admin(client):
    r = client.get('/admin/api/chats')
    assert r.status_code in (302, 401)
    r = client.post('/admin/api/chat/1/reply', json={'text': 'x'}, headers=csrf())
    assert r.status_code in (302, 401, 403)


def test_chat_validation(client):
    register(client)
    assert client.post('/api/client/chat', json={'text': '  '}).status_code == 400
    assert client.post('/api/client/chat',
                       json={'text': 'x' * 2001}).status_code == 400
    r = client.post('/api/client/chat', json={'text': 'нормальное сообщение'})
    assert r.get_json()['success']


def test_chat_rate_limit(client):
    register(client)
    ok = 0
    last = None
    for i in range(12):
        last = client.post('/api/client/chat', json={'text': 'msg {}'.format(i)})
        if last.status_code == 200:
            ok += 1
    assert ok == 10
    assert last.status_code == 429


def test_chat_closed_dialog_reopens_on_client_message(client, admin_client):
    cust = register(client)
    client.post('/api/client/chat', json={'text': 'Вопрос'})
    cid = cust['id']
    r = admin_client.post('/admin/api/chat/{}/status'.format(cid),
                          json={'status': 'closed'}, headers=csrf())
    assert r.get_json()['status'] == 'closed'
    # новое сообщение клиента переоткрывает диалог
    r = client.post('/api/client/chat', json={'text': 'Ещё вопрос'})
    assert r.get_json()['success']
    r = client.get('/api/client/chat')
    assert r.get_json()['dialog']['status'] == 'open'


def test_chat_status_validation(client, admin_client):
    cust = register(client)
    r = admin_client.post('/admin/api/chat/{}/status'.format(cust['id']),
                          json={'status': 'nonsense'}, headers=csrf())
    assert r.status_code == 400


# ---------- 7.1: профиль — телефон, конфликт, смена пароля ----------

def test_profile_update_with_phone(client):
    cust = register(client)
    r = client.post('/api/client/profile', json={
        'name': 'Иван Петров', 'email': 'ivan@example.com',
        'phone': '8 (900) 300-00-99'})
    data = r.get_json()
    assert data['success']
    assert data['customer']['name'] == 'Иван Петров'
    assert data['customer']['phone'] == customers.normalize_phone('89003000099')
    assert data['customer']['email'] == 'ivan@example.com'
    assert cust['id'] == data['customer']['id']


def test_profile_phone_conflict(client):
    register(client, phone='+7 900 300-00-01', name='А')
    client.post('/api/client/logout')
    register(client, phone='+7 900 300-00-02', name='Б')
    r = client.post('/api/client/profile', json={'phone': '+7 900 300-00-01'})
    assert r.status_code == 400
    assert 'телефон' in r.get_json()['error'].lower()


def test_password_change(client):
    register(client, phone='+7 900 300-00-01', password='secret1')
    # неверный текущий
    r = client.post('/api/client/password', json={
        'current_password': 'wrong', 'new_password': 'newsecret2'})
    assert r.status_code == 400
    # короткий новый
    r = client.post('/api/client/password', json={
        'current_password': 'secret1', 'new_password': '123'})
    assert r.status_code == 400
    # корректная смена
    r = client.post('/api/client/password', json={
        'current_password': 'secret1', 'new_password': 'newsecret2'})
    assert r.get_json()['success']
    client.post('/api/client/logout')
    r = client.post('/api/client/login', json={
        'phone': '+7 900 300-00-01', 'password': 'secret1'})
    assert r.status_code == 401
    r = client.post('/api/client/login', json={
        'phone': '+7 900 300-00-01', 'password': 'newsecret2'})
    assert r.get_json()['success']


def test_password_change_requires_login(client):
    r = client.post('/api/client/password', json={
        'current_password': 'a', 'new_password': 'bbbbbb'})
    assert r.status_code == 401


# ---------- 7.2: повтор заказа ----------

def test_order_repeat_adds_to_cart(client):
    cust = register(client)
    order = customers.add_order(
        name=cust['name'], phone=cust['phone'], email='',
        product_id='12345', product_name='Наматрасник тестовый',
        price=1000, qty=2, customer_id=cust['id'])
    r = client.post('/api/client/orders/{}/repeat'.format(order['id']),
                    json={})
    data = r.get_json()
    assert data['success']
    assert data['count'] == 2
    # позиция не из каталога — meta с именем/ценой сохранена
    with client.session_transaction() as s:
        assert s['cart'].get('wb:12345') == 2
        assert s['cart_meta']['wb:12345']['name'] == 'Наматрасник тестовый'
    # повтор ещё раз увеличивает количество
    r = client.post('/api/client/orders/{}/repeat'.format(order['id']), json={})
    assert r.get_json()['count'] == 4


def test_order_repeat_other_clients_order_not_found(client):
    cust = register(client, phone='+7 900 300-00-01', name='А')
    order = customers.add_order(
        name='А', phone=cust['phone'], email='', product_id='1',
        product_name='X', price=100, customer_id=cust['id'])
    client.post('/api/client/logout')
    register(client, phone='+7 900 300-00-02', name='Б')
    r = client.post('/api/client/orders/{}/repeat'.format(order['id']), json={})
    assert r.status_code == 404


def test_order_repeat_requires_login(client):
    assert client.post('/api/client/orders/1/repeat', json={}).status_code == 401
