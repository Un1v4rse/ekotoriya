# -*- coding: utf-8 -*-
"""Пресеты секций дизайна: /admin/api/section-presets (CN-T1)."""
import json
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as app_module

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture()
def admin_client():
    """Авторизованный test_client."""
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    client = app_module.app.test_client()
    resp = client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    assert resp.status_code == 302
    return client


def test_section_presets_requires_auth():
    assert app_module.app.test_client().get('/admin/api/section-presets').status_code == 401


def test_section_presets_returns_json_with_canonical_presets(admin_client):
    resp = admin_client.get('/admin/api/section-presets')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert isinstance(data['presets'], list)
    names = [p['name'] for p in data['presets']]
    # Канонические секции главной — обязательно присутствуют (см. data/content.json).
    assert 'Главный баннер' in names
    assert 'Преимущества' in names
    for p in data['presets']:
        assert p['id'] and p['name'] and isinstance(p['html'], str)
        assert p['html'].strip()  # плейсхолдеры lazy-блоков тоже непустые


def test_section_presets_missing_file_ok(admin_client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, 'SECTION_PRESETS_FILE', tmp_path / 'no-such-file.json')
    data = admin_client.get('/admin/api/section-presets').get_json()
    assert data == {'success': True, 'presets': []}


def test_section_presets_file_in_sync_with_content():
    """data/section_presets.json собран из реального data/content.json."""
    presets = json.loads(
        (BASE_DIR / 'data' / 'section_presets.json').read_text(encoding='utf-8'))
    names = [p['name'] for p in presets]
    assert 'Главный баннер' in names
    assert 'Преимущества' in names
