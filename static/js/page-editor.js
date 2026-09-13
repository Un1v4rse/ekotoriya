/*
 * Редактор страницы прямо на сайте (режим /admin/page/<slug>).
 * Позволяет выделять блоки на странице, менять их порядок перетаскиванием,
 * редактировать текст на месте, удалять, дублировать и добавлять новые блоки
 * через визуальную галерею — всё как в Tilda, но прямо на рабочей странице.
 */
(function () {
    const scriptEl = document.currentScript || document.querySelector('script[data-slug]');
    const SLUG = scriptEl.dataset.slug;

    const BLOCK_TYPES = {
        heading: {
            icon: '𝐇', label: 'Заголовок', desc: 'Крупный заголовок раздела',
            mock: '<div class="gm-bar" style="width:70%;height:10px;background:#9a8269;"></div>',
            make: () => ({type: 'heading', level: 'h2', text: 'Новый заголовок'}),
        },
        text: {
            icon: '📝', label: 'Текст', desc: 'Абзацы текста',
            mock: '<div class="gm-bar" style="width:95%;height:6px;"></div><div class="gm-bar" style="width:90%;height:6px;"></div><div class="gm-bar" style="width:60%;height:6px;"></div>',
            make: () => ({type: 'text', text: 'Новый текст. Напишите здесь о вашем товаре, услуге или акции.'}),
        },
        image: {
            icon: '🖼', label: 'Картинка', desc: 'Фото с компьютера или по ссылке',
            mock: '<div class="gm-img">🖼</div>',
            make: () => ({type: 'image', src: '', alt: '', caption: ''}),
        },
        button: {
            icon: '🔘', label: 'Кнопка', desc: 'Кнопка с ссылкой',
            mock: '<div class="gm-btn">КНОПКА</div>',
            make: () => ({type: 'button', text: 'Подробнее', url: '/', color: '#b49d84', align: 'center'}),
        },
        video: {
            icon: '🎬', label: 'Видео', desc: 'Ссылка на YouTube или Vimeo',
            mock: '<div class="gm-video">▶</div>',
            make: () => ({type: 'video', url: ''}),
        },
        products: {
            icon: '🛒', label: 'Товары', desc: 'Карточки WB/Ozon с кнопкой «Купить»',
            mock: '<div class="gm-grid"><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div></div>',
            make: () => ({type: 'products', source: 'all', limit: 8, title: ''}),
        },
        divider: {
            icon: '—', label: 'Разделитель', desc: 'Горизонтальная линия',
            mock: '<div class="gm-line"></div>',
            make: () => ({type: 'divider'}),
        },
        html: {
            icon: '⚙', label: 'HTML', desc: 'Свой код (для специалистов)',
            mock: '<div class="gm-code">&lt;/&gt;</div>',
            make: () => ({type: 'html', html: ''}),
        },
    };

    let blocks = [];
    let pageMeta = {};
    let hasLegacy = false;
    let blockMap = [];       // [{i, elements:[...]}]
    let hoverIdx = -1;
    let selectedIdx = -1;
    let dirty = false;
    let contentEl = null;
    let refreshTimer = null;

    /* ---------------- UI-элементы ---------------- */
    const topbar = el('div', '', {id: 'pe-topbar'});
    topbar.innerHTML =
        '<span class="pe-title">🛠 Редактирование страницы</span>' +
        '<span class="pe-status" id="pe-status"></span>' +
        '<button type="button" id="pe-btn-props">⚙ Параметры страницы</button>' +
        '<button type="button" class="pe-save" id="pe-btn-save">💾 Сохранить</button>' +
        '<button type="button" class="pe-exit" id="pe-btn-exit">✖ Выйти</button>';

    const highlight = el('div', 'pe-highlight');
    const toolbar = el('div', 'pe-toolbar');
    highlight.appendChild(toolbar);
    const dropLine = el('div', '', {id: '', class: 'pe-drop-line'});

    const addBtn = el('button', 'pe-addbtn', {type: 'button', title: 'Добавить блок ниже'});
    addBtn.textContent = '+';

    const drawer = el('div', '', {id: 'pe-drawer'});
    drawer.innerHTML = '<div class="pe-drawer-head"><span id="pe-drawer-title">Настройки блока</span>' +
        '<button type="button" id="pe-drawer-close">✕</button></div><div class="pe-drawer-body" id="pe-drawer-body"></div>';

    const pageProps = el('div', '', {id: 'pe-pageprops'});
    pageProps.innerHTML =
        '<div class="field"><label>Название вкладки браузера (title)</label><input type="text" id="pe-pp-title"></div>' +
        '<div class="field"><label>Заголовок страницы (H1)</label><input type="text" id="pe-pp-h1"></div>' +
        '<div class="field" style="margin-bottom:0"><label>Описание для поисковиков</label><textarea id="pe-pp-desc" rows="2"></textarea></div>';

    const galleryOverlay = el('div', '', {id: 'pe-gallery-overlay'});
    galleryOverlay.innerHTML = '<div id="pe-gallery"><div class="pe-gallery-head"><h3>Добавить блок</h3>' +
        '<button type="button" class="pe-gallery-close">✕</button></div><div class="pe-gallery-grid"></div></div>';

    let booted = false;
    document.addEventListener('DOMContentLoaded', boot);
    if (document.readyState !== 'loading') boot();

    function boot() {
        if (booted) return;
        booted = true;
        document.body.classList.add('pe-active');
        document.body.append(topbar, highlight, dropLine, addBtn, drawer, pageProps, galleryOverlay);
        $('pe-btn-save').addEventListener('click', save);
        $('pe-btn-exit').addEventListener('click', exitEditor);
        $('pe-btn-props').addEventListener('click', () => pageProps.classList.toggle('open'));
        $('pe-drawer-close').addEventListener('click', closeDrawer);
        addBtn.addEventListener('click', () => openGallery(insertIndexAfter(addBtn.dataset.target)));
        galleryOverlay.querySelector('.pe-gallery-close').addEventListener('click', closeGallery);
        galleryOverlay.addEventListener('click', e => { if (e.target === galleryOverlay) closeGallery(); });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeGallery(); closeDrawer(); pageProps.classList.remove('open'); } });

        fetch(`${location.origin}/admin/api/page-blocks?slug=${encodeURIComponent(SLUG)}`)
            .then(r => r.json())
            .then(d => {
                if (!d.success) { status('Ошибка: ' + (d.error || '')); return; }
                blocks = d.blocks;
                pageMeta = d.page;
                hasLegacy = d.legacy;
                document.querySelector('#pe-topbar .pe-title').textContent = '🛠 Редактирование: ' + (pageMeta.h1 || pageMeta.title || pageMeta.url);
                $('pe-pp-title').value = pageMeta.title || '';
                $('pe-pp-h1').value = pageMeta.h1 || '';
                $('pe-pp-desc').value = pageMeta.meta_description || '';
                $('pe-pp-title').addEventListener('input', e => { pageMeta.title = e.target.value; markDirty(); });
                $('pe-pp-h1').addEventListener('input', e => { pageMeta.h1 = e.target.value; markDirty(); });
                $('pe-pp-desc').addEventListener('input', e => { pageMeta.meta_description = e.target.value; markDirty(); });
                buildMap();
                bindPageEvents();
                status(hasLegacy ? 'Страница с перенесённым дизайном: редактируйте блоки вокруг него' : 'Готово к редактированию');
            })
            .catch(e => status('Ошибка загрузки: ' + e.message));
    }

    function $(id) { return document.getElementById(id); }
    function el(tag, cls, attrs) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (attrs) Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
        return e;
    }
    function status(text) { const s = $('pe-status'); if (s) s.textContent = text; }
    function markDirty() { dirty = true; status('есть несохранённые изменения'); }

    /* ---------------- Карта блоков на странице ---------------- */
    function buildMap() {
        blockMap = [];
        contentEl = document.getElementById('content');
        if (!contentEl) {
            blockMap.push({i: 0, elements: [document.body]});
            return;
        }
        let cur = null;
        [...contentEl.childNodes].forEach(n => {
            if (n.nodeType === Node.COMMENT_NODE) {
                const m = n.nodeValue.match(/^\s*blk:(\d+)\s*$/);
                if (m) { cur = {i: +m[1], elements: []}; blockMap.push(cur); return; }
                if (n.nodeValue.trim() === '/blk') { cur = null; return; }
            }
            if (cur && n.nodeType === Node.ELEMENT_NODE) cur.elements.push(n);
        });
        blockMap.sort((a, b) => a.i - b.i);
    }

    function entryAt(idx) { return blockMap.find(e => e.i === idx); }

    function entryRect(idx) {
        const entry = entryAt(idx);
        if (!entry || !entry.elements.length) return null;
        const first = entry.elements[0].getBoundingClientRect();
        const last = entry.elements[entry.elements.length - 1].getBoundingClientRect();
        const cr = contentEl ? contentEl.getBoundingClientRect() : {left: 0, right: window.innerWidth};
        return {
            top: first.top + window.scrollY,
            bottom: last.bottom + window.scrollY,
            left: Math.min(first.left, cr.left) + window.scrollX,
            right: Math.max(first.right, cr.right) + window.scrollX,
        };
    }

    /* ---------------- Подсветка и тулбар ---------------- */
    function bindPageEvents() {
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('scroll', updateOverlayPositions, {passive: true});
        window.addEventListener('resize', updateOverlayPositions);
        document.addEventListener('click', e => {
            if (e.target && e.target.closest && e.target.closest('#pe-topbar, #pe-drawer, #pe-pageprops, #pe-gallery-overlay, .pe-toolbar, .pe-addbtn')) return;
            const idx = blockIndexFromPoint(e.clientX, e.clientY);
            select(idx);
        }, true);
    }

    function blockIndexFromPoint(x, y) {
        for (const entry of blockMap) {
            if (!entry.elements.length) continue;
            for (const elm of entry.elements) {
                const r = elm.getBoundingClientRect();
                if (r.height === 0) continue;
                if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return entry.i;
            }
        }
        return -1;
    }

    function onMouseMove(e) {
        if (dragState.active || textEdit.active) return;
        const idx = blockIndexFromPoint(e.clientX, e.clientY);
        if (idx !== hoverIdx) {
            hoverIdx = idx;
            updateOverlayPositions();
        }
    }

    function updateOverlayPositions() {
        if (dragState.active || textEdit.active) return;
        const showIdx = selectedIdx >= 0 ? selectedIdx : hoverIdx;
        const anyHover = hoverIdx >= 0 && hoverIdx !== selectedIdx;
        if (showIdx < 0) { highlight.style.display = 'none'; addBtn.style.display = 'none'; return; }
        const r = entryRect(showIdx);
        if (!r || r.bottom - r.top < 2) { highlight.style.display = 'none'; return; }
        highlight.style.display = 'block';
        highlight.style.top = r.top + 'px';
        highlight.style.left = r.left + 'px';
        highlight.style.width = (r.right - r.left) + 'px';
        highlight.style.height = (r.bottom - r.top) + 'px';
        highlight.className = 'pe-highlight' + (selectedIdx === showIdx ? ' pe-selected' : '') + (anyHover ? '' : '');

        if (selectedIdx === showIdx) {
            renderToolbar(selectedIdx);
            toolbar.style.display = 'flex';
        } else {
            toolbar.style.display = 'none';
        }

        // Кнопка «+» внизу подсветки
        if (hoverIdx >= 0 && !hasLegacyBlockAt(hoverIdx)) {
            addBtn.dataset.target = hoverIdx;
            addBtn.style.display = 'flex';
            addBtn.style.left = ((r.left + r.right) / 2) + 'px';
            addBtn.style.top = r.bottom + 'px';
        } else if (hoverIdx >= 0) {
            addBtn.style.display = 'none';
        } else {
            addBtn.style.display = 'none';
        }
    }

    function hasLegacyBlockAt(idx) {
        const b = blocks[idx];
        return !!(b && b.legacy);
    }

    function renderToolbar(idx) {
        const b = blocks[idx];
        if (!b) return;
        const type = b.legacy ? 'html' : (b.type || 'text');
        const cfg = BLOCK_TYPES[type] || BLOCK_TYPES.text;
        toolbar.innerHTML = `<span class="pe-tb-name">${b.legacy ? '🧩 Дизайн страницы' : cfg.icon + ' ' + cfg.label}</span>` +
            '<button type="button" data-act="drag" title="Перетащить, чтобы переместить блок">⠿</button>' +
            '<button type="button" data-act="up" title="Выше">↑</button>' +
            '<button type="button" data-act="down" title="Ниже">↓</button>' +
            (b.legacy ? '' : '<button type="button" data-act="dup" title="Дублировать">⧉</button>') +
            ((type === 'heading' || type === 'text') && !b.legacy ? '<button type="button" data-act="edit" title="Редактировать текст прямо на странице">✏</button>' : '') +
            '<button type="button" data-act="settings" title="Настройки">⚙</button>' +
            '<button type="button" data-act="del" class="pe-tb-del" title="Удалить">🗑</button>';
        toolbar.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const act = btn.dataset.act;
                if (act === 'drag') return; // handled by mousedown
                if (act === 'up') moveBy(idx, -1);
                if (act === 'down') moveBy(idx, 1);
                if (act === 'dup') duplicate(idx);
                if (act === 'del') removeBlock(idx);
                if (act === 'settings') openDrawer(idx);
                if (act === 'edit') startTextEdit(idx);
            });
        });
        const dragHandle = toolbar.querySelector('[data-act="drag"]');
        dragHandle.addEventListener('mousedown', e => startDrag(idx, e));
    }

    function select(idx) {
        if (selectedIdx !== idx) {
            selectedIdx = idx;
            if (idx >= 0) closeDrawer();
            updateOverlayPositions();
        }
    }

    /* ---------------- Перетаскивание блоков на странице ---------------- */
    const dragState = {active: false, from: -1};

    function startDrag(idx, e) {
        e.preventDefault();
        dragState.active = true;
        dragState.from = idx;
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onDragMove);
        document.addEventListener('mouseup', onDragEnd);
    }

    function dropIndexForY(y) {
        // Возвращает {index, before} — куда вставить блок при данной координате Y
        for (const entry of blockMap) {
            const r = entryRect(entry.i);
            if (!r) continue;
            const topView = r.top - window.scrollY;
            const bottomView = r.bottom - window.scrollY;
            if (y < bottomView) {
                const before = y < (topView + bottomView) / 2;
                return {index: entry.i, before};
            }
        }
        const last = blockMap[blockMap.length - 1];
        return last ? {index: last.i, before: false} : {index: 0, before: true};
    }

    function onDragMove(e) {
        if (!dragState.active) return;
        const target = dropIndexForY(e.clientY);
        if (!target || target.index === dragState.from) { dropLine.style.display = 'none'; dragState.target = null; return; }
        const r = entryRect(target.index);
        if (!r) return;
        const topView = r.top - window.scrollY;
        const bottomView = r.bottom - window.scrollY;
        const y = target.before ? topView : bottomView;
        const cr = contentEl ? contentEl.getBoundingClientRect() : {left: 0, right: window.innerWidth};
        dropLine.style.display = 'block';
        dropLine.style.top = (y + window.scrollY) + 'px';
        dropLine.style.left = (cr.left + window.scrollX) + 'px';
        dropLine.style.width = cr.width + 'px';
        dragState.target = target;
    }

    function onDragEnd() {
        document.removeEventListener('mousemove', onDragMove);
        document.removeEventListener('mouseup', onDragEnd);
        document.body.style.userSelect = '';
        dropLine.style.display = 'none';
        if (dragState.active && dragState.target) {
            const {index, before} = dragState.target;
            moveBlockTo(dragState.from, index, before);
        }
        dragState.active = false;
        dragState.from = -1;
        dragState.target = null;
    }

    function moveBlockTo(from, targetIndex, before) {
        if (from === targetIndex) return;
        const [moved] = blocks.splice(from, 1);
        let pos;
        if (from < targetIndex) pos = before ? targetIndex - 1 : targetIndex;
        else pos = before ? targetIndex : targetIndex + 1;
        blocks.splice(pos, 0, moved);
        afterStructuralChange();
    }

    function moveBy(idx, dir) {
        const j = idx + dir;
        if (j < 0 || j >= blocks.length) return;
        const [moved] = blocks.splice(idx, 1);
        blocks.splice(j, 0, moved);
        afterStructuralChange();
    }

    function duplicate(idx) {
        const copy = JSON.parse(JSON.stringify(blocks[idx]));
        delete copy.legacy;
        blocks.splice(idx + 1, 0, copy);
        afterStructuralChange(idx + 1);
    }

    function removeBlock(idx) {
        const b = blocks[idx];
        const name = b.legacy ? 'перенесённый дизайн страницы' : 'блок «' + ((BLOCK_TYPES[b.type] || {}).label || b.type) + '»';
        if (!confirm('Удалить ' + name + '?')) return;
        blocks.splice(idx, 1);
        selectedIdx = -1;
        afterStructuralChange();
    }

    function insertIndexAfter(idx) {
        return idx === undefined || idx === null || idx === '' ? 0 : +idx + 1;
    }

    /* ---------------- Inline-редактирование текста ---------------- */
    const textEdit = {active: false, idx: -1, targets: []};

    function startTextEdit(idx) {
        const b = blocks[idx];
        if (!b) return;
        const entry = entryAt(idx);
        if (!entry || !entry.elements.length) return;
        textEdit.active = true;
        textEdit.idx = idx;
        textEdit.targets = [];
        const sel = b.type === 'heading' ? 'h1, h2, h3, h4' : 'p';
        entry.elements.forEach(elm => {
            (elm.matches(sel) ? [elm] : [...elm.querySelectorAll(sel)]).forEach(t => {
                t.contentEditable = 'true';
                t.classList.add('pe-editable-on');
                textEdit.targets.push(t);
            });
        });
        highlight.style.display = 'none';
        addBtn.style.display = 'none';
        if (textEdit.targets[0]) textEdit.targets[0].focus();
        // Панель завершения правки
        const done = el('div', 'pe-toolbar');
        done.style.cssText = 'position:fixed;top:56px;left:50%;transform:translateX(-50%);display:flex;z-index:100002;border-radius:7px;';
        done.innerHTML = '<span class="pe-tb-name">Редактируете текст</span>' +
            '<button type="button" data-act="ok" style="width:auto;padding:0 10px;">✓ Готово</button>' +
            '<button type="button" data-act="cancel">✖</button>';
        done.querySelector('[data-act="ok"]').addEventListener('click', () => finishTextEdit(true));
        done.querySelector('[data-act="cancel"]').addEventListener('click', () => finishTextEdit(false));
        document.body.appendChild(done);
        textEdit.doneBar = done;
        textEdit.original = textEdit.targets.map(t => t.innerHTML);
    }

    function finishTextEdit(apply) {
        const b = blocks[textEdit.idx];
        textEdit.targets.forEach((t, k) => {
            t.contentEditable = 'false';
            t.classList.remove('pe-editable-on');
            if (!apply) t.innerHTML = textEdit.original[k];
        });
        if (apply && b) {
            if (b.type === 'heading') {
                const t = textEdit.targets[0];
                if (t) b.text = t.textContent.trim();
            } else if (b.type === 'text') {
                b.text = textEdit.targets.map(t => t.innerText.trim()).filter(Boolean).join('\n\n');
            }
            markDirty();
            scheduleRefresh();
        }
        if (textEdit.doneBar) textEdit.doneBar.remove();
        textEdit.active = false;
        textEdit.targets = [];
        updateOverlayPositions();
    }

    /* ---------------- Галерея новых блоков ---------------- */
    function openGallery(insertAt) {
        galleryOverlay.dataset.insertAt = insertAt;
        const grid = galleryOverlay.querySelector('.pe-gallery-grid');
        grid.innerHTML = '';
        Object.entries(BLOCK_TYPES).forEach(([type, cfg]) => {
            const card = el('div', 'gcard');
            card.innerHTML = `<div class="gmock">${cfg.mock}</div><div class="gname">${cfg.icon} ${cfg.label}</div><div class="gdesc">${cfg.desc}</div>`;
            card.addEventListener('click', () => {
                closeGallery();
                const at = +galleryOverlay.dataset.insertAt || 0;
                blocks.splice(at, 0, cfg.make());
                afterStructuralChange(at);
            });
            grid.appendChild(card);
        });
        galleryOverlay.classList.add('open');
    }
    function closeGallery() { galleryOverlay.classList.remove('open'); }

    /* ---------------- Обновление страницы после изменений ---------------- */
    function afterStructuralChange(selectIdx) {
        markDirty();
        closeDrawer();
        if (hasLegacy) {
            // Перенесённый дизайн содержит скрипты — только полная перезагрузка отрендерит его корректно
            location.reload();
            return;
        }
        selectedIdx = (selectIdx === undefined) ? -1 : selectIdx;
        refreshPage();
    }

    function scheduleRefresh() {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refreshPage, 600);
    }

    async function refreshPage() {
        if (hasLegacy) { updateOverlayPositions(); return; }
        status('обновление страницы…');
        try {
            const resp = await fetch(`${location.origin}/admin/api/preview-marked`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({blocks}),
            });
            const result = await resp.json();
            if (!result.success) { status('Ошибка: ' + (result.error || '')); return; }
            if (!contentEl) { location.reload(); return; }
            contentEl.innerHTML = result.content_html;
            reviveScripts(contentEl);
            buildMap();
            updateOverlayPositions();
            status(dirty ? 'есть несохранённые изменения' : 'готово');
        } catch (e) {
            status('Ошибка сети: ' + e.message);
        }
    }

    function reviveScripts(root) {
        [...root.querySelectorAll('script')].forEach(old => {
            const s = document.createElement('script');
            if (old.src) s.src = old.src;
            else s.textContent = old.textContent;
            old.replaceWith(s);
        });
    }

    /* ---------------- Панель настроек блока ---------------- */
    function openDrawer(idx) {
        selectedIdx = idx;
        const b = blocks[idx];
        if (!b) return;
        const type = b.legacy ? 'html' : (b.type || 'text');
        const cfg = BLOCK_TYPES[type] || BLOCK_TYPES.text;
        $('pe-drawer-title').textContent = (b.legacy ? '🧩 Дизайн страницы' : cfg.icon + ' ' + cfg.label);
        const body = $('pe-drawer-body');
        body.innerHTML = '';
        renderBlockSettings(b, idx, body);
        drawer.classList.add('open');
        updateOverlayPositions();
    }
    function closeDrawer() { drawer.classList.remove('open'); }

    function field(labelText, inputEl) {
        const f = el('div', 'field');
        const l = document.createElement('label');
        l.textContent = labelText;
        f.appendChild(l);
        f.appendChild(inputEl);
        return f;
    }
    function helpEl(text) {
        const d = el('div', 'help');
        d.textContent = text;
        return d;
    }
    function textInput(value, onchange, placeholder) {
        const inp = document.createElement('input');
        inp.type = 'text';
        inp.value = value || '';
        if (placeholder) inp.placeholder = placeholder;
        inp.addEventListener('input', () => onchange(inp.value));
        return inp;
    }
    function textareaInput(value, onchange, rows) {
        const t = document.createElement('textarea');
        t.rows = rows || 4;
        t.value = value || '';
        t.addEventListener('input', () => onchange(t.value));
        return t;
    }
    function selectInput(options, value, onchange) {
        const s = document.createElement('select');
        options.forEach(([val, label]) => {
            const o = document.createElement('option');
            o.value = val;
            o.textContent = label;
            s.appendChild(o);
        });
        s.value = value;
        s.addEventListener('change', () => onchange(s.value));
        return s;
    }

    function renderBlockSettings(block, idx, container) {
        const set = (k, v) => { block[k] = v; markDirty(); scheduleRefresh(); };
        const type = block.legacy ? 'html' : (block.type || 'text');

        if (type === 'heading') {
            container.appendChild(field('Текст заголовка', textInput(block.text, v => set('text', v))));
            container.appendChild(field('Размер', selectInput([
                ['h1', 'Самый крупный (H1)'], ['h2', 'Крупный (H2)'], ['h3', 'Средний (H3)'],
            ], block.level || 'h2', v => set('level', v))));
            container.appendChild(helpEl('Совет: текст можно править прямо на странице кнопкой ✏ в панели блока.'));
        } else if (type === 'text') {
            container.appendChild(field('Текст', textareaInput(block.text, v => set('text', v), 8)));
            container.appendChild(helpEl('Пустая строка — новый абзац. Можно править прямо на странице кнопкой ✏.'));
        } else if (type === 'image') {
            const file = document.createElement('input');
            file.type = 'file';
            file.accept = 'image/*';
            const st = el('div', 'help');
            file.addEventListener('change', async () => {
                if (!file.files.length) return;
                st.textContent = 'Загрузка…';
                const fd = new FormData();
                fd.append('file', file.files[0]);
                try {
                    const resp = await fetch(`${location.origin}/admin/api/upload`, {method: 'POST', body: fd});
                    const result = await resp.json();
                    if (result.success) {
                        set('src', result.url);
                        st.textContent = 'Загружено.';
                        const img = container.querySelector('.preview-img img');
                        if (img) img.src = result.url;
                    } else {
                        st.textContent = 'Ошибка: ' + (result.error || '');
                    }
                } catch (e) { st.textContent = 'Ошибка сети: ' + e.message; }
            });
            container.appendChild(field('Загрузить с компьютера', file));
            container.appendChild(st);
            container.appendChild(field('…или ссылка на картинку', textInput(block.src, v => {
                set('src', v);
                const img = container.querySelector('.preview-img img');
                if (img) img.src = v;
            }, 'https://…')));
            const pv = el('div', 'preview-img');
            const img = document.createElement('img');
            img.src = block.src || '';
            pv.appendChild(img);
            container.appendChild(pv);
            container.appendChild(field('Подпись для поисковиков (alt)', textInput(block.alt, v => set('alt', v))));
            container.appendChild(field('Подпись под картинкой', textInput(block.caption, v => set('caption', v))));
        } else if (type === 'button') {
            container.appendChild(field('Текст на кнопке', textInput(block.text, v => set('text', v))));
            container.appendChild(field('Куда ведёт (адрес страницы)', textInput(block.url, v => set('url', v), '/catalog/')));
            const colorRow = el('div', 'field');
            const l = document.createElement('label');
            l.textContent = 'Цвет кнопки';
            const color = document.createElement('input');
            color.type = 'color';
            color.value = block.color || '#b49d84';
            color.addEventListener('input', () => set('color', color.value));
            colorRow.appendChild(l);
            colorRow.appendChild(color);
            container.appendChild(colorRow);
            container.appendChild(field('Положение', selectInput([
                ['left', 'Слева'], ['center', 'По центру'], ['right', 'Справа'],
            ], block.align || 'center', v => set('align', v))));
        } else if (type === 'video') {
            container.appendChild(field('Ссылка на видео YouTube или Vimeo', textInput(block.url, v => set('url', v), 'https://youtube.com/watch?v=…')));
            container.appendChild(helpEl('Достаточно обычной ссылки из адресной строки.'));
        } else if (type === 'products') {
            container.appendChild(field('Заголовок над товарами (можно пусто)', textInput(block.title, v => set('title', v), 'Наши товары')));
            container.appendChild(field('Откуда товары', selectInput([
                ['all', 'Все (WB + Ozon)'], ['wb', 'Только Wildberries'], ['ozon', 'Только Ozon'],
            ], block.source || 'all', v => set('source', v))));
            const limit = document.createElement('input');
            limit.type = 'number';
            limit.min = 1;
            limit.max = 48;
            limit.value = block.limit || 8;
            limit.addEventListener('input', () => set('limit', parseInt(limit.value || 8, 10)));
            container.appendChild(field('Сколько карточек показать', limit));
            container.appendChild(helpEl('Цены и скидки подтягиваются из разделов WB/Ozon админки автоматически.'));
        } else if (type === 'divider') {
            container.appendChild(helpEl('Горизонтальная линия-разделитель. Настроек нет.'));
        } else if (type === 'html') {
            if (block.legacy) {
                container.appendChild(helpEl('Это содержимое перенесённой страницы. Вы можете добавить свои блоки выше или ниже, либо удалить этот блок и собрать страницу заново.'));
            }
            container.appendChild(field('HTML-код', textareaInput(block.html, v => set('html', v), 14)));
        }
    }

    /* ---------------- Сохранение и выход ---------------- */
    async function save() {
        if (textEdit.active) finishTextEdit(true);
        status('Сохранение…');
        try {
            const resp = await fetch(`${location.origin}/admin/api/page-save`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({slug: SLUG, page: pageMeta, blocks}),
            });
            const result = await resp.json();
            if (result.success) {
                dirty = false;
                status('Сохранено ✓');
                setTimeout(() => location.reload(), 400);
            } else {
                status('Ошибка: ' + (result.error || ''));
            }
        } catch (e) {
            status('Ошибка сети: ' + e.message);
        }
    }

    function exitEditor() {
        if (dirty && !confirm('Есть несохранённые изменения. Выйти без сохранения?')) return;
        location.href = pageMeta.url || '/';
    }
})();
