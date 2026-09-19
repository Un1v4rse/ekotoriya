# -*- coding: utf-8 -*-
"""Слой хранения: roundtrip контента и настроек в БД (AR-1, TS-1)."""
import storage


def test_load_content_shape():
    content = storage.load_content()
    assert set(content.keys()) >= {'menu', 'pages', 'site', 'socials'}
    assert any(p['slug'] == 'home' for p in content['pages'])
    # возвращается копия — мутации результата не портят кэш
    content['site']['phone'] = 'changed'
    assert storage.load_content()['site']['phone'] == '+7 900 000-00-00'


def test_save_content_roundtrip():
    content = storage.load_content()
    content['site']['phone'] = '+7 911 111-11-11'
    content['pages'].append({
        'slug': 'new-page', 'url': '/new-page/', 'title': 'Новая',
        'blocks': [{'type': 'text', 'text': 'текст'}],
    })
    storage.save_content(content)

    loaded = storage.load_content()
    assert loaded['site']['phone'] == '+7 911 111-11-11'
    assert any(p['slug'] == 'new-page' for p in loaded['pages'])
    new_page = next(p for p in loaded['pages'] if p['slug'] == 'new-page')
    assert new_page['blocks'][0]['text'] == 'текст'


def test_save_content_removes_absent_pages():
    content = storage.load_content()
    content['pages'] = [p for p in content['pages'] if p['slug'] == 'home']
    storage.save_content(content)
    slugs = [p['slug'] for p in storage.load_content()['pages']]
    assert slugs == ['home']


def test_get_set_setting():
    storage.set_setting('test.key', {'a': 1})
    assert storage.get_setting('test.key') == {'a': 1}
    assert storage.get_setting('test.missing') is None
    assert storage.get_setting('test.missing', 'default') == 'default'


def test_invalidate_cache_forces_reload():
    storage.load_content()
    storage.set_setting('site', {'name': 'Другое имя'})
    assert storage.load_content()['site']['name'] == 'Другое имя'
