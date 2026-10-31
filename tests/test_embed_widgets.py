"""Приоритет 7: безопасные сторонние виджеты (embed-блок)."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import blocks
from db import db
from models import AdminUser


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


def _csrf(client):
    return {'X-CSRF-Token': 'test-csrf', 'Content-Type': 'application/json'}


def test_embed_url_youtube_watch():
    assert blocks._embed_url('https://www.youtube.com/watch?v=abc123def456') == \
        'https://www.youtube.com/embed/abc123def456'


def test_embed_url_youtube_short():
    assert blocks._embed_url('https://youtu.be/abc123def456') == \
        'https://www.youtube.com/embed/abc123def456'


def test_embed_url_vimeo():
    assert blocks._embed_url('https://vimeo.com/123456789') == \
        'https://player.vimeo.com/video/123456789'


def test_embed_url_rutube():
    assert blocks._embed_url('https://rutube.ru/video/abcdef123456/') == \
        'https://rutube.ru/play/embed/abcdef123456'


def test_embed_url_vk():
    assert blocks._embed_url('https://vk.com/video-123_456789') == \
        'https://vk.com/video_ext.php?oid=-123&id=456789'


def test_embed_url_spotify():
    assert blocks._embed_url('https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC') == \
        'https://open.spotify.com/embed/track/4uLU6hMCjMI75M1A2tKUQC'


def test_embed_url_yandex_maps():
    url = 'https://yandex.ru/maps/?text=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0%2C%20%D0%9A%D1%80%D0%B0%D1%81%D0%BD%D0%B0%D1%8F%20%D0%BF%D0%BB%D0%BE%D1%89%D0%B0%D0%B4%D1%8C%2C%201&z=16'
    embed = blocks._embed_url(url)
    assert embed.startswith('https://yandex.ru/map-widget/v1/')
    assert 'text=' in embed


def test_embed_url_rejects_unsafe():
    assert blocks._embed_url('https://evil.com/video/1') is None
    assert blocks._embed_url('javascript:alert(1)') is None
    assert blocks._embed_url('') is None
    assert blocks._embed_url('not-a-url') is None
    assert blocks._embed_url('https://yandex.ru/maps/') is None  # нет параметров


def test_validate_blocks_embed_ok():
    ok, err = blocks.validate_blocks([
        {'type': 'embed', 'url': 'https://www.youtube.com/watch?v=abc123def456'},
    ])
    assert ok is True
    assert err == ''


def test_validate_blocks_embed_empty_url():
    ok, err = blocks.validate_blocks([{'type': 'embed', 'url': ''}])
    assert ok is False
    assert 'укажите ссылку' in err


def test_validate_blocks_embed_unsafe_url():
    ok, err = blocks.validate_blocks([{'type': 'embed', 'url': 'https://evil.com/x'}])
    assert ok is False
    assert 'не из разрешённого списка' in err


def test_render_embed_video():
    html = blocks.render_block({'type': 'embed', 'url': 'https://youtu.be/abc123def456'})
    assert 'iframe' in html
    assert 'https://www.youtube.com/embed/abc123def456' in html
    assert 'sandbox=' in html
    assert 'allowfullscreen' in html
    assert '<script' not in html.lower()


def test_render_embed_map():
    html = blocks.render_block({'type': 'embed', 'url': 'https://yandex.ru/maps/?text=Moscow&z=16', 'height': 300})
    assert 'iframe' in html
    assert 'map-widget/v1/' in html
    assert 'height:300px' in html
    assert 'sandbox=' in html


def test_render_embed_placeholder():
    html = blocks.render_block({'type': 'embed', 'url': ''})
    assert 'iframe' not in html
    assert 'Виджет:' in html


def test_render_embed_rejects_inline_event():
    # embed URL не может содержать inline-атрибуты, но проверим, что src не
    # экранирован неверно и не содержит опасных схем.
    html = blocks.render_block({'type': 'embed', 'url': 'https://www.youtube.com/watch?v=abc123'})
    assert 'javascript:' not in html
    assert 'onerror' not in html.lower()



def test_preview_accepts_embed(admin_client):
    r = admin_client.post('/admin/api/preview',
                          json={'slug': 'index', 'blocks': [{'type': 'embed', 'url': 'https://youtu.be/abc123def456'}]},
                          headers=_csrf(admin_client))
    assert r.status_code == 200
    assert 'youtube.com/embed/abc123def456' in r.get_json()['html']


def test_preview_rejects_embed_unsafe_url(admin_client):
    r = admin_client.post('/admin/api/preview',
                          json={'slug': 'index', 'blocks': [{'type': 'embed', 'url': 'https://evil.com/x'}]},
                          headers=_csrf(admin_client))
    assert r.status_code == 400
    data = r.get_json()
    assert 'не из разрешённого списка' in data['error']


def test_preview_superadmin_can_render_html(admin_client):
    r = admin_client.post('/admin/api/preview',
                          json={'slug': 'index', 'blocks': [{'type': 'html', 'html': '<p class="x">hello</p>'}]},
                          headers=_csrf(admin_client))
    assert r.status_code == 200
    assert '<p class="x">hello</p>' in r.get_json()['html']


def test_preview_non_superadmin_html_is_restricted():
    with app_module.app.app_context():
        db.session.query(AdminUser).filter_by(username='__content_embed__').delete()
        db.session.add(AdminUser(username='__content_embed__', password_hash=generate_password_hash('Test1234!'), role='content'))
        db.session.commit()
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['admin_user'] = '__content_embed__'
        sess['admin_role'] = 'content'
        sess['csrf_token'] = 'test-csrf'
    # Прогреваем сессионный cookie безопасным GET-запросом
    client.get('/admin/login')
    r = client.post('/admin/api/preview',
                    json={'slug': 'index', 'blocks': [{'type': 'html', 'html': '<p>hello</p>'}]},
                    headers=_csrf(client))
    assert r.status_code == 400
    assert 'HTML-код' in r.get_json()['error']
    with app_module.app.app_context():
        db.session.query(AdminUser).filter_by(username='__content_embed__').delete()
        db.session.commit()
