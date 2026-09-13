#!/usr/bin/env python3
"""Download missing static assets referenced by generated templates."""
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
BASE_URL = 'https://ekotoriya.ru'


def collect_missing():
    needed = set()
    for tmpl in (ROOT / 'templates' / 'pages').glob('*.html'):
        text = tmpl.read_text(encoding='utf-8', errors='ignore')
        # URLs may be escaped as \"/bitrix/...\" inside JS strings; normalize for search.
        search_text = text.replace('\\/', '/')
        # Find any absolute Bitrix/upload/include asset paths.
        for m in re.finditer(r'(?:^|[^\w/])(/bitrix/[^"\'\s<>\\,]+)', search_text):
            needed.add(m.group(1))
        for m in re.finditer(r'(?:^|[^\w/])(/upload/[^"\'\s<>\\,]+)', search_text):
            needed.add(m.group(1))
        for m in re.finditer(r'(?:^|[^\w/])(/include/[^"\'\s<>\\,]+)', search_text):
            needed.add(m.group(1))
        for m in re.finditer(r'(?:^|[^\w/])(/proizvodstvo/[^"\'\s<>\\,]+)', search_text):
            needed.add(m.group(1))

    missing = []
    seen = set()
    for p in needed:
        path = urlsplit(p).path
        if not path or path in seen:
            continue
        seen.add(path)
        # Skip dynamic endpoints
        if path.endswith('.php') or path.startswith('/bitrix/tools/') or path.startswith('/bitrix/admin/'):
            continue
        local = STATIC / path.lstrip('/')
        if not local.exists():
            missing.append(path)
    return missing


def download(url, local_path):
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        local_path.write_bytes(r.content)
        return True
    except Exception as e:
        print('  ERROR', url, e)
        return False


def main():
    missing = collect_missing()
    print('missing assets:', len(missing))
    downloaded = 0
    failed = 0
    for path in sorted(missing):
        url = BASE_URL + path
        print('GET', url)
        local = STATIC / path.lstrip('/')
        if download(url, local):
            downloaded += 1
        else:
            failed += 1
        time.sleep(0.2)
    print('done. downloaded:', downloaded, 'failed:', failed)


if __name__ == '__main__':
    main()
