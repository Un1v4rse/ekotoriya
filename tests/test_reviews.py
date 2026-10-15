# -*- coding: utf-8 -*-
"""Отзывы покупателей (цикл 4 / этап 6).

Покрытие:
- POST /api/review: сохранение (approved=False — на модерацию), валидация
  (имя/текст обязательны, рейтинг 1–5), rate-limit оставляем декоратору;
- вывод на страницу «Отзывы»: одобренные видны, неодобренные — нет;
- админ-API: список только с логином, модерация (одобрить/скрыть), удаление.
"""
import copy

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import storage
from db import db
from models import Review


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
    client = app_module.app.test_client()
    resp = client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'test-csrf'
    return client


def _register_reviews_page():
    content = copy.deepcopy(storage.load_content())
    pages = content.setdefault('pages', [])
    if not any(p.get('slug') == 'company__reviews' for p in pages):
        pages.append({
            'slug': 'company__reviews',
            'url': '/company/reviews/',
            'title': 'Отзывы',
            'h1': 'Отзывы',
            'template': 'pages/company__reviews.html',
            'blocks': [],
        })
        storage.save_content(content)
        app_module.invalidate_site_cache()


def test_review_success_goes_to_moderation(client):
    resp = client.post('/api/review', json={
        'name': 'Мария', 'rating': 5, 'text': 'Отличные наматрасники!',
        'product_name': 'Наматрасник 200×200'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    row = db.session.query(Review).one()
    assert row.name == 'Мария'
    assert row.rating == 5
    assert row.approved is False
    assert row.product_name == 'Наматрасник 200×200'


def test_review_validation(client):
    assert client.post('/api/review', json={'name': '', 'text': 'x', 'rating': 5}).status_code == 400
    assert client.post('/api/review', json={'name': 'Иван', 'text': '', 'rating': 5}).status_code == 400
    assert client.post('/api/review', json={'name': 'Иван', 'text': 'x', 'rating': 0}).status_code == 400
    assert client.post('/api/review', json={'name': 'Иван', 'text': 'x', 'rating': 6}).status_code == 400
    assert db.session.query(Review).count() == 0


def test_reviews_page_shows_only_approved(client):
    _register_reviews_page()
    db.session.add(Review(name='Анна', rating=5, text='Супер', approved=True))
    db.session.add(Review(name='Петр', rating=3, text='На проверке', approved=False))
    db.session.commit()
    html = client.get('/company/reviews/').get_data(as_text=True)
    assert 'Супер' in html
    assert 'На проверке' not in html
    assert 'wb-reviews-dynamic' in html


def test_reviews_page_no_dynamic_block_when_empty(client):
    _register_reviews_page()
    html = client.get('/company/reviews/').get_data(as_text=True)
    assert '<div class="wb-reviews-dynamic">' not in html


def test_admin_reviews_requires_login(client):
    assert client.get('/admin/api/reviews').status_code == 401


def test_admin_review_moderate_and_delete(admin_client):
    db.session.add(Review(name='Ольга', rating=4, text='Хорошо', approved=False))
    db.session.commit()
    rid = db.session.query(Review).one().id

    resp = admin_client.post('/admin/api/review/moderate', json={'id': rid, 'approved': True},
                             headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.get_json()['success'] is True
    assert db.session.get(Review, rid).approved is True

    resp = admin_client.post('/admin/api/review/delete', json={'id': rid},
                             headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.get_json()['success'] is True
    assert db.session.query(Review).count() == 0


def test_admin_review_moderate_validation(admin_client):
    assert admin_client.post('/admin/api/review/moderate', json={},
                             headers={'X-CSRF-Token': 'test-csrf'}).status_code == 400
    assert admin_client.post('/admin/api/review/moderate', json={'id': 9999, 'approved': True},
                             headers={'X-CSRF-Token': 'test-csrf'}).status_code == 404
    assert admin_client.post('/admin/api/review/delete', json={'id': 9999},
                             headers={'X-CSRF-Token': 'test-csrf'}).status_code == 404
