# -*- coding: utf-8 -*-
"""Безопасность (Этап 6.1, раздел 2.2.7 ТЗ): upload-валидация, HTML-санитайзер,
allowlist proxy_image, CSP, rate-limit публичных POST, lockout входа в админку."""
import io
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import blocks

PNG_1PX = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
           b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
           b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


@pytest.fixture()
def admin_client():
    """Авторизованный test_client с CSRF-токеном (как в test_admin_api)."""
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'secadmin', 'hash': generate_password_hash('secpass'),
        'must_change_password': False,
    })
    client = app_module.app.test_client()
    resp = client.post('/admin/login', data={'username': 'secadmin', 'password': 'secpass'})
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'sec-csrf'
    return client


def _csrf():
    return {'X-CSRF-Token': 'sec-csrf'}


def _upload(client, name, data, ctype):
    return client.post('/admin/api/upload',
                       data={'file': (io.BytesIO(data), name, ctype)},
                       headers=_csrf(), content_type='multipart/form-data')


def test_upload_rejects_exe(admin_client):
    r = _upload(admin_client, 'evil.exe', b'MZ\x90\x00', 'application/x-msdownload')
    assert r.status_code == 400
    assert not r.get_json()['success']


def test_upload_rejects_mime_mismatch(admin_client):
    """.png с текстовым содержимым и чужим MIME — отклоняется."""
    r = _upload(admin_client, 'fake.png', b'not an image at all', 'text/plain')
    assert r.status_code == 400


def test_upload_accepts_png_and_never_overwrites(admin_client, monkeypatch):
    """Валидный PNG принимается; повторная загрузка даёт НОВОЕ имя (перезаписи нет)."""
    import tempfile
    fake_dir = Path(tempfile.mkdtemp(prefix='ekotoriya-upload-test-'))
    monkeypatch.setattr(app_module, 'BASE_DIR', fake_dir)
    monkeypatch.setattr(app_module, 'IMAGE_CACHE_DIR', fake_dir / 'image_cache')
    r1 = _upload(admin_client, 'photo.png', PNG_1PX, 'image/png')
    assert r1.status_code == 200 and r1.get_json()['success']
    url1 = r1.get_json()['url']
    assert url1.startswith('/static/uploads/') and url1.endswith('.png')
    assert (fake_dir / 'static' / 'uploads' / url1.rsplit('/', 1)[-1]).exists()
    # Имя не равно исходному (file.filename не используется как путь) — перезапись невозможна
    assert url1 != '/static/uploads/photo.png'
    # Второй раз — другое уникальное имя, первый файл не затёрт
    r2 = _upload(admin_client, 'photo.png', PNG_1PX, 'image/png')
    url2 = r2.get_json()['url']
    assert r2.status_code == 200 and url2 != url1
    assert (fake_dir / 'static' / 'uploads' / url1.rsplit('/', 1)[-1]).exists()


def test_upload_rejects_oversize(admin_client, monkeypatch):
    import tempfile
    fake_dir = Path(tempfile.mkdtemp(prefix='ekotoriya-upload-test-'))
    monkeypatch.setattr(app_module, 'BASE_DIR', fake_dir)
    big = io.BytesIO(b'\xff\xd8\xff' + b'0' * (11 * 1024 * 1024))
    r = _upload(admin_client, 'big.jpg', big.read(), 'image/jpeg')
    assert r.status_code == 400


def test_sanitize_html_strips_script_and_iframe():
    out = blocks._sanitize_html(
        '<p>Привет <b>мир</b></p><script>alert(1)</script>'
        '<iframe src="https://evil"></iframe>'
        '<img src="https://ok/img.png" onerror="x()">'
        '<a href="javascript:alert(1)">x</a>')
    assert '<script' not in out and 'alert' not in out
    assert '<iframe' not in out
    assert '<b>мир</b>' in out
    assert 'onerror' not in out
    assert 'javascript:' not in out
    assert 'src="https://ok/img.png"' in out


def test_html_block_sanitized_even_for_superadmin(admin_client):
    """Defense in depth: рендер html-блока суперадмином проходит санитайзер."""
    with app_module.app.test_request_context('/'):
        from flask import session
        session['admin_role'] = 'superadmin'
        html = blocks.render_block({'type': 'html', 'html': '<b>x</b><script>bad()</script>'})
    assert '<b>x</b>' in html and '<script' not in html


def test_proxy_host_allowlist():
    allowed = app_module._proxy_host_allowed
    assert allowed('basket-10.wbbasket.ru')
    assert allowed('basket-12.wbbasket.ru')
    assert allowed('static-basket-01.wb.ru')
    assert allowed('cdn1.wildberries.ru')
    assert allowed('www.wildberries.ru')
    assert allowed('cdn1.ozone.ru')
    # SSRF / чужие хосты
    assert not allowed('localhost')
    assert not allowed('127.0.0.1')
    assert not allowed('169.254.169.254')
    assert not allowed('192.168.1.1')
    assert not allowed('basket-10.wbbasket.ru.evil.com')
    assert not allowed('evil-cdn.ru')
    assert not allowed('cdn.wildberries.ru.evil.com')


def test_proxy_image_rejects_localhost_and_foreign_host():
    client = app_module.app.test_client()
    for url in ('http://127.0.0.1:5000/x.png', 'http://localhost/x.png',
                'http://evil-cdn.ru/x.png', 'file:///etc/passwd'):
        r = client.get('/proxy/image', query_string={'url': url})
        assert r.status_code == 200  # отдаётся placeholder, не проксируется


def test_proxy_image_wb_host(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, 'IMAGE_CACHE_DIR', tmp_path / 'imgcache')
    (tmp_path / 'imgcache').mkdir()

    class FakeResp:
        status_code = 200
        content = PNG_1PX

    monkeypatch.setattr(app_module.requests, 'get', lambda *a, **kw: FakeResp())
    client = app_module.app.test_client()
    r = client.get('/proxy/image',
                   query_string={'url': 'https://basket-10.wbbasket.ru/vol1/1.jpg'})
    assert r.status_code == 200
    if app_module._PILImage is not None:
        assert r.mimetype == 'image/webp'   # PNG-заглушка сконвертирована в WebP
    else:  # pragma: no cover — Pillow в venv есть
        assert r.mimetype == 'image/jpeg'


def test_csp_header_present():
    client = app_module.app.test_client()
    r = client.get('/')
    csp = r.headers.get('Content-Security-Policy', '')
    assert "default-src 'self'" in csp
    assert 'frame-ancestors' in csp
    # Рендер страницы не сломан заголовком
    assert r.status_code == 200 and b'<html' in r.data.lower()


def test_rate_limit_like_returns_429():
    client = app_module.app.test_client()
    statuses = []
    for i in range(21):
        r = client.post('/api/like', json={'product_id': 1000 + i, 'product_name': 't'})
        statuses.append(r.status_code)
    assert statuses[:20] == [200] * 20
    assert statuses[20] == 429


def test_admin_login_lockout_after_5_failures():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'lockuser', 'hash': generate_password_hash('rightpass'),
        'must_change_password': False,
    })
    try:
        client = app_module.app.test_client()
        for _ in range(5):
            r = client.post('/admin/login',
                            data={'username': 'lockuser', 'password': 'wrong'})
            assert r.status_code == 200  # форма с ошибкой, не редирект
        # 6-й вход ДАЖЕ с правильным паролем отклонён
        r = client.post('/admin/login',
                        data={'username': 'lockuser', 'password': 'rightpass'})
        assert r.status_code == 200
        body = r.data.decode('utf-8')
        assert 'Слишком много попыток' in body
        # в админку не пустило (редирект на логин)
        assert client.get('/admin/').status_code == 302
    finally:
        with app_module._login_lock:
            app_module._login_attempts.clear()


def test_order_endpoint_still_works():
    """Не сломано: /api/order принимает заявку (rate-limit первому запросу не мешает)."""
    client = app_module.app.test_client()
    r = client.post('/api/order', json={'name': 'Тест', 'phone': '+7 900 000-00-00',
                                        'product_id': 1, 'product_name': 'Товар',
                                        'price': 100})
    assert r.status_code == 200
    assert r.get_json()['success']
