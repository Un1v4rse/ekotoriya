# -*- coding: utf-8 -*-
"""Черновики / совместная работа / история (Приоритет 5):
- optimistic locking для /admin/api/page-draft;
- конфликт 409 при одновременном редактировании двумя админами;
- восстановление версии попадает в черновик, а не публикуется;
- настраиваемая глубина архива версий.
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from db import db
from models import Page, PageVersion


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
        sess['admin_user'] = 'testadmin'
    return client


@pytest.fixture()
def other_admin_client():
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
        sess['admin_user'] = 'otheradmin'
    return client


@pytest.fixture(autouse=True)
def page_fixture():
    page = db.session.query(Page).filter_by(slug='draft-test').first()
    if not page:
        page = Page(slug='draft-test', title='Draft Test', content_html='',
                    published_blocks=[{'type': 'text', 'text': 'published'}])
        db.session.add(page)
        db.session.commit()
    page.draft_blocks = None
    db.session.query(PageVersion).filter_by(page_slug='draft-test').delete()
    db.session.commit()
    yield
    db.session.query(Page).filter_by(slug='draft-test').delete()
    db.session.query(PageVersion).filter_by(page_slug='draft-test').delete()
    db.session.commit()


def test_draft_save_returns_saved_at_and_author(admin_client):
    resp = admin_client.post('/admin/api/page-draft', json={
        'slug': 'draft-test', 'blocks': [{'type': 'text', 'text': 'v1'}]},
        headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['saved_by'] == 'testadmin'
    assert data['saved_at']


def test_draft_conflict_409(admin_client, other_admin_client):
    r1 = admin_client.post('/admin/api/page-draft', json={
        'slug': 'draft-test', 'blocks': [{'type': 'text', 'text': 'v1'}]},
        headers={'X-CSRF-Token': 'test-csrf'})
    saved_at = r1.get_json()['saved_at']

    # Второй админ меняет черновик без expected_saved_at (force)
    other_admin_client.post('/admin/api/page-draft', json={
        'slug': 'draft-test', 'blocks': [{'type': 'text', 'text': 'v2'}]},
        headers={'X-CSRF-Token': 'test-csrf'})

    # Первый админ пытается сохранить со старым expected_saved_at
    resp = admin_client.post('/admin/api/page-draft', json={
        'slug': 'draft-test', 'blocks': [{'type': 'text', 'text': 'v3'}],
        'expected_saved_at': saved_at},
        headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 409
    data = resp.get_json()
    assert data['success'] is False
    assert 'другим' in data['error'].lower() or 'измен' in data['error'].lower()


def test_version_restore_goes_to_draft_not_published(admin_client):
    resp = admin_client.post('/admin/api/page-version-save', json={
        'slug': 'draft-test', 'label': 'Тест'},
        headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 200

    version = db.session.query(PageVersion).filter_by(page_slug='draft-test').order_by(PageVersion.id.desc()).first()
    assert version is not None

    resp = admin_client.post('/admin/api/page-version-restore', json={
        'slug': 'draft-test', 'version_id': version.id},
        headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True

    page = db.session.query(Page).filter_by(slug='draft-test').first()
    assert page.draft_blocks['blocks'][0]['text'] == 'published'
    assert page.published_blocks[0]['text'] == 'published'


def test_page_version_limit_env(monkeypatch):
    monkeypatch.setenv('PAGE_VERSION_LIMIT', '3')
    # Перезагрузка модуля не нужна: константа читается при импорте.
    # Проверим через приватную константу, доступную после импорта.
    import importlib
    import app as _app
    importlib.reload(_app)
    assert _app._VERSIONS_PER_PAGE == 3
