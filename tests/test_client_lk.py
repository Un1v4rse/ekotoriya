# -*- coding: utf-8 -*-
"""Регистрация/вход клиента, ЛК, корзина (LC-1…LC-5), роли и дашборд (AD-2…AD-5), TS-3."""
import io
import zipfile

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import customers
from db import db
from models import AdminUser, Order


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture()
def admin_client():
    """Суперадмин через формальный логин (роль проставится из admin_users)."""
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


def role_client(username, role):
    """Клиент админки с готовой сессией нужной роли."""
    db.session.add(AdminUser(username=username,
                             password_hash=generate_password_hash('x' * 10), role=role))
    db.session.commit()
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['admin_user'] = username
        s['admin_role'] = role
        s['csrf_token'] = 'csrf-' + username
    return c


# ---------- LC-1: регистрация и вход ----------

def test_client_register_login_logout(client):
    r = client.post('/api/client/register', json={
        'name': 'Клиент К', 'phone': '+7 900 300-00-01', 'password': 'secret1'})
    assert r.get_json()['success']
    # дубликат — ошибка
    r = client.post('/api/client/register', json={
        'name': 'Клиент К', 'phone': '8 900 300 00 01', 'password': 'secret1'})
    assert r.status_code == 400
    client.post('/api/client/logout')
    # вход с нормализацией телефона
    r = client.post('/api/client/login', json={'phone': '8 (900) 300-00-01', 'password': 'secret1'})
    assert r.get_json()['success']
    # неверный пароль
    r = client.post('/api/client/login', json={'phone': '+7 900 300-00-01', 'password': 'wrong'})
    assert r.status_code == 401
    # короткий пароль при регистрации
    r = client.post('/api/client/register', json={'phone': '+7 900 300-00-09', 'password': '123'})
    assert r.status_code == 400


def test_personal_requires_login(client):
    assert client.get('/personal/').status_code == 302
    assert client.get('/personal/orders/').status_code == 302


def test_personal_page_renders_for_client(client):
    client.post('/api/client/register', json={
        'name': 'ЛК Пользователь', 'phone': '+7 900 300-00-02', 'password': 'secret1'})
    r = client.get('/personal/')
    assert r.status_code == 200
    assert 'ЛК Пользователь'.encode('utf-8') in r.data


def test_client_profile_update(client):
    client.post('/api/client/register', json={
        'name': 'Старое Имя', 'phone': '+7 900 300-00-03', 'password': 'secret1'})
    r = client.post('/api/client/profile', json={'name': 'Новое Имя', 'email': 'n@example.com'})
    assert r.get_json()['success']
    c = customers.get_customer_by_phone('+79003000003')
    assert c['name'] == 'Новое Имя' and c['email'] == 'n@example.com'


# ---------- LC-4: корзина ----------

def test_cart_add_update_checkout(client):
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()  # сброс кэша каталога от предыдущих тестов
    r = client.post('/api/cart/add', json={'product_id': '890129778', 'source': 'wb', 'qty': 2})
    assert r.get_json()['count'] == 2
    client.post('/api/cart/add', json={'product_id': '67928567', 'source': 'wb'})
    r = client.get('/basket/')
    assert r.status_code == 200 and 'Корзина'.encode('utf-8') in r.data
    # checkout без имени — ошибка
    r = client.post('/api/cart/checkout', json={'phone': '+7 900 300-00-04'})
    assert r.status_code == 400
    r = client.post('/api/cart/checkout', json={
        'name': 'Покупатель Корзины', 'phone': '+7 900 300-00-04', 'consent_pd': True})
    data = r.get_json()
    assert data['success'] and len(data['orders']) == 2
    assert all(o['source'] == 'basket' for o in data['orders'])
    # корзина очищена
    r = client.post('/api/cart/checkout', json={'name': 'X', 'phone': '+7 900 300-00-04'})
    assert r.status_code == 400


# ---------- LC-5: привязка заявок к аккаунту по телефону ----------

def test_orders_attach_to_account_by_phone(client):
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    reg = client.post('/api/client/register', json={
        'name': 'Владелец', 'phone': '+7 900 300-00-05', 'password': 'secret1'})
    cid = reg.get_json()['customer']['id']
    client.post('/api/client/logout')
    # «в 1 клик» без регистрации с тем же телефоном
    r = client.post('/api/order', json={
        'name': 'Владелец', 'phone': '8 900 300 00 05',
        'product_id': 890129778, 'product_name': 'Наматрасник', 'price': 1000})
    assert r.get_json()['order']['customer_id'] == cid
    # корзина с тем же телефоном
    client.post('/api/cart/add', json={'product_id': '890129778', 'source': 'wb'})
    r = client.post('/api/cart/checkout', json={'name': 'Владелец', 'phone': '+7 900 300-00-05'})
    assert r.get_json()['orders'][0]['customer_id'] == cid
    # и в ЛК заявки видны
    client.post('/api/client/login', json={'phone': '+7 900 300-00-05', 'password': 'secret1'})
    r = client.get('/personal/')
    assert 'Наматрасник'.encode('utf-8') in r.data


# ---------- AD-2: роли ----------

def test_role_orders_cannot_edit_pages():
    c = role_client('mgr', 'orders')
    assert c.get('/admin/api/customers').status_code == 200
    r = c.post('/admin/api/page-save', json={'slug': 'x', 'blocks': []}, headers=csrf())
    assert r.status_code == 403
    r = c.post('/admin/api/admin-users', json={'username': 'z', 'password': '123456', 'role': 'orders'}, headers=csrf())
    assert r.status_code == 403


def test_role_content_cannot_see_customers():
    c = role_client('content1', 'content')
    assert c.get('/admin/api/customers').status_code == 403
    assert c.get('/admin/api/segments').status_code == 403


def test_admin_users_crud(admin_client):
    r = admin_client.post('/admin/api/admin-users', json={
        'username': 'manager2', 'password': 'pass123', 'role': 'orders'}, headers=csrf())
    assert r.get_json()['success']
    r = admin_client.get('/admin/api/admin-users')
    names = [u['username'] for u in r.get_json()['users']]
    assert 'manager2' in names
    # дубликат
    r = admin_client.post('/admin/api/admin-users', json={
        'username': 'manager2', 'password': 'pass123', 'role': 'orders'}, headers=csrf())
    assert r.status_code == 400
    # удалить себя нельзя
    r = admin_client.post('/admin/api/admin-user/delete', json={'username': 'testadmin'}, headers=csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/admin-user/delete', json={'username': 'manager2'}, headers=csrf())
    assert r.get_json()['success']


# ---------- AD-3/AD-4: дашборд и аудит ----------

def test_dashboard_and_audit(admin_client):
    customers.add_order('Даш', '+7 900 300-00-06', '', '890129778', 'Наматрасник', 1000)
    r = admin_client.get('/admin/api/dashboard')
    d = r.get_json()
    assert d['success'] and d['orders_total'] >= 1 and 'popular_products' in d
    r = admin_client.get('/admin/api/audit')
    assert r.get_json()['success']


# ---------- AD-5: бэкап и восстановление ----------

def test_backup_full_and_restore(admin_client):
    customers.add_order('Бэкап', '+7 900 300-00-07', '', '890129778', 'Наматрасник', 1000)
    r = admin_client.get('/admin/api/backup/full')
    assert r.status_code == 200 and 'application/zip' in r.headers.get('Content-Type', '')
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert 'content.json' in zf.namelist()
    r = admin_client.post('/admin/api/restore',
                          data={'file': (io.BytesIO(r.data), 'backup.zip')},
                          headers={'X-CSRF-Token': 'csrf'},
                          content_type='multipart/form-data')
    assert r.get_json()['success']
    # не-zip отклоняется
    r = admin_client.post('/admin/api/restore',
                          data={'file': (io.BytesIO(b'not a zip'), 'x.txt')},
                          headers={'X-CSRF-Token': 'csrf'},
                          content_type='multipart/form-data')
    assert r.status_code == 400


def test_backup_requires_superadmin():
    c = role_client('mgr3', 'orders')
    assert c.get('/admin/api/backup/full').status_code == 403
