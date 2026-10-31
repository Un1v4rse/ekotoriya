/*
 * Редактор страницы прямо на сайте (режим /admin/page/<slug>) — цикл 2.
 * Трёхзонный layout уровня Tilda: верхняя панель, левая панель
 * (библиотека блоков + структура страницы), холст (реальная страница
 * с маркерами <!--blk:N-->), правая панель настроек выбранного блока.
 *
 * Перенесено из редактора цикла 1: реестр BLOCK_TYPES, undo/redo (50),
 * автосохранение черновика с восстановлением, конфликт 409
 * (expected_updated_at), hover-механика кнопки «+ Добавить блок» (мост),
 * работа с маркерами блоков, инлайн-редактирование текста (contenteditable,
 * сериализация plain text — форматирование не сохраняется, серверный рендер
 * экранирует текст блоков, см. отчёт), панель структуры.
 *
 * Vanilla JS (ES2020+), без jQuery и eval. CSP: инлайн-стили разрешены.
 */
(function () {
    'use strict';

    const scriptEl = document.currentScript || document.querySelector('script[data-slug]');
    const SLUG = scriptEl.dataset.slug;
    const API = p => `${location.origin}/admin/api${p}`;
    const CSRF = () => window.CSRF_TOKEN || '';

    /* Режим обёртки (обычные страницы): контент страницы живёт в same-origin
       iframe (#pe-content-frame из page_editor_wrapper.html) шириной
       1200/768/360 — media queries страницы применяются честно. Без обёртки
       (системные страницы ?edit=1) — старое поведение: перенос body в scaleBox.
       CD/CW заполняются после загрузки iframe (onFrameLoaded). */
    const PE_CONTENT_URL = window.PE_CONTENT_URL || '';
    const IFRAME_MODE = !!PE_CONTENT_URL;
    let contentFrame = null;         // iframe с контентом (IFRAME_MODE)
    let CD = null;                   // contentDocument iframe
    let CW = null;                   // contentWindow iframe

    /* Документ, в котором живут ноды контента: ноды для вставки в контент
       создаём через CD.createElement, иначе вставка чужой ноды ведёт себя
       как adoptNode и ломает обработчики/стили. */
    function contentDoc() { return (IFRAME_MODE && CD) ? CD : document; }
    function cel(tag) { return contentDoc().createElement(tag); }
    /* Визуальные координаты окна для элемента контента. В iframe-режиме
       getBoundingClientRect элемента — в системе viewport iframe (без scale),
       переводим: fr — визуальный rect самого iframe (с учётом transform). */
    function contentRect(elm) {
        const r = elm.getBoundingClientRect();
        if (!IFRAME_MODE || !contentFrame) return r;
        const fr = contentFrame.getBoundingClientRect();
        const s = scaleCurrent || 1;
        return {
            left: fr.left + r.left * s,
            top: fr.top + r.top * s,
            right: fr.left + r.right * s,
            bottom: fr.top + r.bottom * s,
            width: r.width * s,
            height: r.height * s,
        };
    }
    // clientY/clientX события из iframe → визуальные координаты окна редактора.
    function frameToParentY(y) {
        if (!IFRAME_MODE || !contentFrame) return y;
        return contentFrame.getBoundingClientRect().top + y * (scaleCurrent || 1);
    }
    function frameToParentX(x) {
        if (!IFRAME_MODE || !contentFrame) return x;
        return contentFrame.getBoundingClientRect().left + x * (scaleCurrent || 1);
    }
    // На время drag из родительского документа (плашки блоков) гасим iframe,
    // иначе mousemove над контентом уходит в iframe и родитель его не видит.
    function setFramePointerEvents(v) {
        if (IFRAME_MODE && contentFrame) contentFrame.style.pointerEvents = v;
    }

    /* ============================== Хелперы ============================== */
    function $(id) { return document.getElementById(id); }
    function el(tag, cls, attrs) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (attrs) Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
        return e;
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g,
            c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    }
    function debounce(fn, ms) {
        let t = null;
        return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    }
    function fmtDateTime(iso) {
        const d = Date.parse(iso || '');
        return isNaN(d) ? (iso || '') : new Date(d).toLocaleString('ru-RU',
            {day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit'});
    }
    async function apiPost(path, body) {
        const resp = await fetch(API(path), {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': CSRF()},
            body: JSON.stringify(body || {}),
        });
        return resp;
    }
    async function apiGet(path) {
        const resp = await fetch(API(path));
        return resp.json();
    }

    /* ============================== Toast ============================== */
    const toastBox = el('div', '', {id: 'pe-toasts'});
    function toast(text, kind) {
        const t = el('div', 'pe-toast' + (kind ? ' pe-toast-' + kind : ''));
        t.textContent = text;
        toastBox.appendChild(t);
        setTimeout(() => t.classList.add('pe-toast-show'));
        setTimeout(() => {
            t.classList.remove('pe-toast-show');
            setTimeout(() => t.remove(), 300);
        }, 4200);
    }

    /* ==================== Реестр типов блоков (CN-T1) ==================== */
    const BLOCK_TYPES = {
        heading: {
            icon: '𝐇', label: 'Заголовок', desc: 'Крупный заголовок раздела',
            mock: '<div class="gm-bar" style="width:70%;height:10px;background:#9a8269;"></div>',
            make: () => ({type: 'heading', level: 'h2', text: 'Новый заголовок'}),
            presets: [
                {name: 'H1 по центру', make: () => ({type: 'heading', level: 'h1', text: 'Заголовок страницы', align: 'center'})},
                {name: 'H2 слева', make: () => ({type: 'heading', level: 'h2', text: 'Заголовок раздела', align: 'left'})},
                {name: 'H3 слева', make: () => ({type: 'heading', level: 'h3', text: 'Подзаголовок', align: 'left'})},
            ],
        },
        text: {
            icon: '📝', label: 'Текст', desc: 'Абзацы текста',
            mock: '<div class="gm-bar" style="width:95%;height:6px;"></div><div class="gm-bar" style="width:90%;height:6px;"></div><div class="gm-bar" style="width:60%;height:6px;"></div>',
            make: () => ({type: 'text', text: 'Новый текст. Напишите здесь о вашем товаре, услуге или акции.'}),
            presets: [
                {name: 'Обычный', make: () => ({type: 'text', text: 'Абзац текста. Пустая строка между абзацами — новый абзац.'})},
                {name: 'По центру', make: () => ({type: 'text', text: 'Текст по центру.', align: 'center'})},
            ],
        },
        text_image: {
            icon: '📰', label: 'Текст + фото', desc: 'Заголовок и текст рядом с картинкой',
            mock: '<div style="display:flex;gap:8px;width:100%;height:100%;align-items:center;"><div class="gm-img" style="flex:0 0 45%;height:70%;">🖼</div><div style="flex:1;display:flex;flex-direction:column;gap:5px;"><div class="gm-bar" style="width:70%;height:8px;background:#9a8269;"></div><div class="gm-bar" style="width:95%;height:5px;"></div><div class="gm-bar" style="width:90%;height:5px;"></div><div class="gm-bar" style="width:60%;height:5px;"></div></div></div>',
            make: () => ({type: 'text_image', title: 'Заголовок', text: 'Опишите здесь преимущества вашего продукта или расскажите о компании.', src: '', image_pos: 'left'}),
            presets: [
                {name: 'Фото слева', make: () => ({type: 'text_image', title: 'Заголовок', text: 'Текст рядом с фотографией.', src: '', image_pos: 'left'})},
                {name: 'Фото справа', make: () => ({type: 'text_image', title: 'Заголовок', text: 'Текст рядом с фотографией.', src: '', image_pos: 'right'})},
            ],
        },
        image: {
            icon: '🖼', label: 'Картинка', desc: 'Фото из медиатеки или по ссылке',
            mock: '<div class="gm-img">🖼</div>',
            make: () => ({type: 'image', src: '', alt: '', caption: ''}),
            presets: [
                {name: 'По центру', make: () => ({type: 'image', src: '', alt: '', caption: '', align: 'center'})},
                {name: 'На всю ширину', make: () => ({type: 'image', src: '', alt: '', caption: '', align: 'center', width: '100%'})},
            ],
        },
        gallery: {
            icon: '🖼', label: 'Галерея', desc: 'Сетка фотографий 2–4 в ряд',
            mock: '<div class="gm-grid"><div class="gm-img" style="height:34px;border-radius:3px;"></div><div class="gm-img" style="height:34px;border-radius:3px;"></div><div class="gm-img" style="height:34px;border-radius:3px;"></div><div class="gm-img" style="height:34px;border-radius:3px;"></div></div>',
            make: () => ({type: 'gallery', title: '', cols: 3, items: [{src: '', alt: ''}]}),
            presets: [
                {name: '3 в ряд', make: () => ({type: 'gallery', title: '', cols: 3, items: [{src: '', alt: ''}]})},
                {name: '2 в ряд', make: () => ({type: 'gallery', title: '', cols: 2, items: [{src: '', alt: ''}]})},
            ],
        },
        products: {
            icon: '🛒', label: 'Товары', desc: 'Карточки товаров с ценами и кнопкой «Купить»',
            mock: '<div class="gm-grid"><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="im"></div><div class="tx"></div></div></div>',
            make: () => ({type: 'products', mode: 'source', source: 'all', limit: 8, title: ''}),
            presets: [
                {name: 'Сетка товаров', make: () => ({type: 'products', mode: 'source', source: 'all', limit: 8, title: 'Наши товары'})},
                {name: 'Рекомендации', make: () => ({type: 'products', mode: 'recommendations', limit: 8, title: 'Рекомендуем'})},
                {name: 'По категории', make: () => ({type: 'products', mode: 'category', category: '', limit: 8, title: ''})},
            ],
        },
        table: {
            icon: '📊', label: 'Таблица', desc: 'Таблица с размерами и ценами',
            mock: '<div style="width:100%;display:flex;flex-direction:column;gap:4px;"><div style="display:flex;gap:4px;"><div class="gm-bar" style="flex:1;height:10px;background:#9a8269;"></div><div class="gm-bar" style="flex:1;height:10px;background:#9a8269;"></div><div class="gm-bar" style="flex:1;height:10px;background:#9a8269;"></div></div><div style="display:flex;gap:4px;"><div class="gm-bar" style="flex:1;height:8px;"></div><div class="gm-bar" style="flex:1;height:8px;"></div><div class="gm-bar" style="flex:1;height:8px;"></div></div></div>',
            make: () => ({type: 'table', title: '', items: [{cells: 'Параметр | Значение'}, {cells: 'Размер | 160×200'}]}),
            presets: [
                {name: '2 колонки', make: () => ({type: 'table', title: '', items: [{cells: 'Параметр | Значение'}, {cells: 'Размер | 160×200'}]})},
                {name: '3 колонки с шапкой', make: () => ({type: 'table', title: '', header_row: true, items: [{cells: 'Название | Размер | Цена'}, {cells: 'Наматрасник | 160×200 | 2 601 ₽'}]})},
            ],
        },
        form: {
            icon: '📩', label: 'Форма связи', desc: 'Форма обратной связи (заявка в админку)',
            mock: '<div class="gm-bar" style="width:95%;height:10px;border:1px solid #ccc;background:#fff;"></div><div class="gm-bar" style="width:95%;height:10px;border:1px solid #ccc;background:#fff;"></div><div class="gm-btn">ОТПРАВИТЬ</div>',
            make: () => ({type: 'form', title: 'Остались вопросы?', button: 'Отправить', recipient: ''}),
            presets: [
                {name: 'Светлая', make: () => ({type: 'form', title: 'Остались вопросы?', button: 'Отправить', recipient: ''})},
                {name: 'Тёмная', make: () => ({type: 'form', title: 'Остались вопросы?', button: 'Отправить', recipient: '', bg_type: 'color', bg: '#2a2c33', text_color: '#ffffff'})},
            ],
        },
        contacts: {
            icon: '☎', label: 'Контакты', desc: 'Карточка с телефоном, email и адресом',
            mock: '<div style="width:100%;height:100%;background:#f4f4f8;border-radius:6px;display:flex;flex-direction:column;justify-content:center;gap:5px;padding:10px;"><div class="gm-bar" style="width:50%;height:7px;background:#9a8269;"></div><div class="gm-bar" style="width:80%;height:5px;"></div><div class="gm-bar" style="width:70%;height:5px;"></div><div class="gm-bar" style="width:60%;height:5px;"></div></div>',
            make: () => ({type: 'contacts', title: 'Свяжитесь с нами', phone: '8 800 201-08-24', email: 'info@ekotoriya.ru', address: '', worktime: 'Пн–Пт, 9:00–18:00'}),
            presets: [
                {name: 'Полная карточка', make: () => ({type: 'contacts', title: 'Свяжитесь с нами', phone: '8 800 201-08-24', email: 'info@ekotoriya.ru', address: '', worktime: 'Пн–Пт, 9:00–18:00'})},
                {name: 'Минимум', make: () => ({type: 'contacts', title: 'Контакты', phone: '', email: 'info@ekotoriya.ru', address: '', worktime: ''})},
            ],
        },
        reviews: {
            icon: '💬', label: 'Отзывы', desc: 'Карточки отзывов покупателей с оценкой',
            mock: '<div class="gm-bar" style="width:60%;height:8px;background:#9a8269;"></div><div class="gm-bar" style="width:95%;height:5px;"></div><div class="gm-bar" style="width:90%;height:5px;"></div><div class="gm-bar" style="width:40%;height:8px;background:#e8a33d;"></div>',
            make: () => ({type: 'reviews', title: 'Отзывы покупателей', items: [{name: 'Анна', text: 'Отличное качество, быстрая доставка!', rating: 5}]}),
            presets: [
                {name: 'Один отзыв', make: () => ({type: 'reviews', title: 'Отзывы покупателей', items: [{name: 'Анна', text: 'Отличное качество, быстрая доставка!', rating: 5}]})},
                {name: 'Три отзыва', make: () => ({type: 'reviews', title: 'Отзывы покупателей', items: [
                    {name: 'Анна', text: 'Отличное качество, быстрая доставка!', rating: 5},
                    {name: 'Дмитрий', text: 'Всё понравилось, рекомендую.', rating: 5},
                    {name: 'Мария', text: 'Хороший товар за свои деньги.', rating: 4}]})},
            ],
        },
        faq: {
            icon: '❓', label: 'FAQ', desc: 'Вопросы и ответы (аккордеон)',
            mock: '<div class="gm-bar" style="width:95%;height:8px;"></div><div class="gm-bar" style="width:95%;height:8px;"></div><div class="gm-bar" style="width:95%;height:8px;"></div>',
            make: () => ({type: 'faq', title: 'Частые вопросы', items: [{q: 'Как сделать заказ?', a: 'Нажмите «Купить в 1 клик» на карточке товара и оставьте контакты — мы перезвоним.'}]}),
            presets: [
                {name: 'Один вопрос', make: () => ({type: 'faq', title: 'Частые вопросы', items: [{q: 'Как сделать заказ?', a: 'Оставьте заявку на сайте — мы перезвоним.'}]})},
                {name: 'Три вопроса', make: () => ({type: 'faq', title: 'Частые вопросы', items: [
                    {q: 'Как сделать заказ?', a: 'Оставьте заявку на сайте — мы перезвоним.'},
                    {q: 'Сколько стоит доставка?', a: 'Доставка бесплатная при заказе от 3 000 ₽.'},
                    {q: 'Есть ли возврат?', a: 'Да, в течение 14 дней при сохранении упаковки.'}]})},
            ],
        },
        map: {
            icon: '📍', label: 'Карта', desc: 'Яндекс.Карта по адресу',
            mock: '<div class="gm-img" style="background:#dfe8ee;">📍</div>',
            make: () => ({type: 'map', title: 'Как нас найти', address: 'Москва, Красная площадь, 1'}),
            presets: [{name: 'По адресу', make: () => ({type: 'map', title: 'Как нас найти', address: 'Москва, Красная площадь, 1'})}],
        },
        button: {
            icon: '🔘', label: 'Кнопка', desc: 'Кнопка с ссылкой',
            mock: '<div class="gm-btn">КНОПКА</div>',
            make: () => ({type: 'button', text: 'Подробнее', url: '/', color: '#b49d84', align: 'center'}),
            presets: [
                {name: 'Заливка', make: () => ({type: 'button', text: 'Подробнее', url: '/catalog/', align: 'center', button_style: {variant: 'fill', bg: '#b49d84', color: '#ffffff', radius: 6}})},
                {name: 'Обводка', make: () => ({type: 'button', text: 'Подробнее', url: '/catalog/', align: 'center', button_style: {variant: 'outline', border_color: '#b49d84', radius: 6}})},
            ],
        },
        video: {
            icon: '🎬', label: 'Видео', desc: 'Ссылка на YouTube или Vimeo',
            mock: '<div class="gm-video">▶</div>',
            make: () => ({type: 'video', url: ''}),
            presets: [{name: 'YouTube / Vimeo', make: () => ({type: 'video', url: ''})}],
        },
        features: {
            icon: '✨', label: 'Преимущества', desc: 'Карточки «почему мы»: иконка, заголовок, текст',
            mock: '<div class="gm-grid"><div class="gm-card-mini"><div class="tx" style="height:8px;background:#9a8269;"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="tx" style="height:8px;background:#9a8269;"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="tx" style="height:8px;background:#9a8269;"></div><div class="tx"></div></div><div class="gm-card-mini"><div class="tx" style="height:8px;background:#9a8269;"></div><div class="tx"></div></div></div>',
            make: () => ({type: 'features', title: 'Наши преимущества', cols: 4, items: [{title: 'Качество', text: 'Гарантия на всю продукцию', icon: ''}]}),
            presets: [
                {name: '4 в ряд', make: () => ({type: 'features', title: 'Наши преимущества', cols: 4, items: [{title: 'Качество', text: 'Гарантия на всю продукцию', icon: '🏆'}]})},
                {name: '3 в ряд', make: () => ({type: 'features', title: 'Наши преимущества', cols: 3, items: [{title: 'Качество', text: 'Гарантия на всю продукцию', icon: '🏆'}]})},
            ],
        },
        divider: {
            icon: '—', label: 'Разделитель', desc: 'Горизонтальная линия',
            mock: '<div class="gm-line"></div>',
            make: () => ({type: 'divider'}),
            presets: [{name: 'Тонкая линия', make: () => ({type: 'divider'})}],
        },
        spacer: {
            icon: '↕', label: 'Отступ', desc: 'Вертикальный отступ между блоками',
            mock: '<div style="display:flex;flex-direction:column;gap:4px;width:100%;align-items:center;"><div class="gm-bar" style="width:90%;height:4px;"></div><span style="color:#aaa;font-size:0.65rem;">40 px</span><div class="gm-bar" style="width:90%;height:4px;"></div></div>',
            make: () => ({type: 'spacer', height: 40}),
            presets: [
                {name: '40 px', make: () => ({type: 'spacer', height: 40})},
                {name: '80 px', make: () => ({type: 'spacer', height: 80})},
            ],
        },
        text_columns: {
            icon: '📑', label: 'Текст в колонки', desc: 'Текст, разбитый на 2–4 колонки',
            mock: '<div style="display:flex;gap:6px;width:100%;height:100%;"><div style="flex:1;display:flex;flex-direction:column;gap:4px;"><div class="gm-bar" style="height:5px;"></div><div class="gm-bar" style="height:5px;"></div><div class="gm-bar" style="height:5px;"></div></div><div style="flex:1;display:flex;flex-direction:column;gap:4px;"><div class="gm-bar" style="height:5px;"></div><div class="gm-bar" style="height:5px;"></div><div class="gm-bar" style="height:5px;"></div></div></div>',
            make: () => ({type: 'text_columns', title: '', items: [{text: 'Первая колонка текста.'}, {text: 'Вторая колонка текста.'}]}),
            presets: [
                {name: '2 колонки', make: () => ({type: 'text_columns', title: '', items: [{text: 'Первая колонка текста.'}, {text: 'Вторая колонка текста.'}]})},
                {name: '3 колонки', make: () => ({type: 'text_columns', title: '', items: [{text: 'Первая колонка.'}, {text: 'Вторая колонка.'}, {text: 'Третья колонка.'}]})},
            ],
        },
        timer: {
            icon: '⏳', label: 'Таймер акции', desc: 'Обратный отсчёт до конца акции с кнопкой',
            mock: '<div style="width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;"><div class="gm-bar" style="width:60%;height:8px;background:#9a8269;"></div><div class="gm-bar" style="width:80%;height:10px;background:#c00;"></div><div class="gm-btn" style="font-size:0.6rem;padding:2px 8px;">КНОПКА</div></div>',
            make: () => ({type: 'timer', title: 'До конца акции', date: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10), button_text: 'Успеть купить', button_link: '/catalog/'}),
            presets: [
                {name: 'С кнопкой', make: () => ({type: 'timer', title: 'До конца акции', date: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10), button_text: 'Успеть купить', button_link: '/catalog/'})},
                {name: 'Без кнопки', make: () => ({type: 'timer', title: 'До конца акции', date: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10)})},
            ],
        },
        zero: {
            icon: '🎯', label: 'Zero', desc: 'Свободная композиция: элементы в любых местах',
            mock: '<div style="position:relative;width:100%;height:100%;background:#f0ece6;border-radius:5px;"><div style="position:absolute;left:10%;top:20%;width:50%;height:14%;background:#9a8269;border-radius:3px;"></div><div style="position:absolute;left:10%;top:45%;width:70%;height:8%;background:#ccc;border-radius:3px;"></div><div style="position:absolute;left:10%;top:65%;width:25%;height:16%;background:#b49d84;border-radius:10px;"></div></div>',
            make: () => ({type: 'zero', height: 500, elements: [
                {id: 'el-title', type: 'text', x: 10, y: 25, w: 60, h: 20, props: {text: 'Заголовок Zero-блока'}},
                {id: 'el-btn', type: 'button', x: 10, y: 55, w: 25, h: 14, props: {text: 'Подробнее', url: '/catalog/'}},
            ]}),
            presets: [
                {name: 'Пустой холст', make: () => ({type: 'zero', height: 500, elements: []})},
                {name: 'Hero с кнопкой', make: () => ({type: 'zero', height: 400, elements: [
                    {id: 'el-bg', type: 'shape', x: 0, y: 0, w: 100, h: 100, props: {color: '#b49d84', shape: 'rect'}},
                    {id: 'el-title', type: 'text', x: 10, y: 30, w: 60, h: 25, props: {text: 'Заголовок Zero-блока', text_style: {color: '#ffffff', size: '32px'}}},
                    {id: 'el-btn', type: 'button', x: 10, y: 62, w: 25, h: 14, props: {text: 'Подробнее', url: '/catalog/', button_style: {variant: 'fill', bg: '#ffffff', color: '#333333'}}},
                ]})},
            ],
        },
        html: {
            icon: '⚙', label: 'HTML', desc: 'Свой код (рендерится только у суперадмина)',
            mock: '<div class="gm-code">&lt;/&gt;</div>',
            make: () => ({type: 'html', html: ''}),
            presets: [{name: 'Пустой блок', make: () => ({type: 'html', html: ''})}],
        },
        section: {
            icon: '🧩', label: 'Секция дизайна', desc: 'Фрагмент перенесённого дизайна страницы',
            mock: '',
            make: () => ({type: 'section', name: 'Секция', html: ''}),
            presets: [],
        },
    };

    /* Категории библиотеки блоков (левая панель) */
    const CATEGORIES = [
        {id: 'sections', name: 'Секции сайта', types: ['section']},
        {id: 'cover', name: 'Обложка', types: ['banner', 'text_image']},
        {id: 'headings', name: 'Заголовки', types: ['heading']},
        {id: 'text', name: 'Текст', types: ['text', 'text_columns']},
        {id: 'images', name: 'Изображения', types: ['image', 'gallery']},
        {id: 'products', name: 'Товары', types: ['products']},
        {id: 'pricing', name: 'Тарифы и цены', types: ['table']},
        {id: 'forms', name: 'Формы', types: ['form', 'contacts']},
        {id: 'social', name: 'Отзывы', types: ['reviews']},
        {id: 'faq', name: 'FAQ', types: ['faq']},
        {id: 'map', name: 'Карта', types: ['map']},
        {id: 'button', name: 'Кнопка', types: ['button']},
        {id: 'video', name: 'Видео', types: ['video']},
        {id: 'features', name: 'Преимущества', types: ['features']},
        {id: 'separator', name: 'Разделитель', types: ['divider', 'spacer']},
        {id: 'timer', name: 'Таймер', types: ['timer']},
        {id: 'zero', name: 'Zero', types: ['zero']},
        {id: 'html', name: 'HTML', types: ['html']},
    ];
    BLOCK_TYPES.banner = {
        icon: '📢', label: 'Баннер', desc: 'Промо-блок: заголовок, текст и кнопка',
        mock: '<div style="width:100%;height:100%;background:#b49d84;border-radius:5px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:0.7rem;font-weight:700;">БАННЕР</div>',
        make: () => ({type: 'banner', title: 'Скидка 10% на первый заказ', subtitle: 'Успейте до конца месяца', button: 'Выбрать товар', url: '/catalog/', bg: '#b49d84'}),
        presets: [
            {name: 'Брендовый', make: () => ({type: 'banner', title: 'Скидка 10% на первый заказ', subtitle: 'Успейте до конца месяца', button: 'Выбрать товар', url: '/catalog/', bg: '#b49d84'})},
            {name: 'Тёмный', make: () => ({type: 'banner', title: 'Заголовок баннера', subtitle: 'Подзаголовок', button: 'Подробнее', url: '/catalog/', bg: '#333333', text_color: '#ffffff'})},
            {name: 'Светлый', make: () => ({type: 'banner', title: 'Заголовок баннера', subtitle: 'Подзаголовок', button: 'Подробнее', url: '/catalog/', bg: '#f5f1ea', text_color: '#333333'})},
        ],
    };

    /* ============================== Состояние ============================== */
    let blocks = [];
    let pageMeta = {};
    let hasLegacy = false;
    let blockMap = [];               // [{i, elements:[...]}]
    let hoverIdx = -1;
    let selectedIdx = -1;
    let dirty = false;
    let contentEl = null;
    let workspaceEl = null;          // #pe-workspace — flex-строка: панели + холст
    let canvasArea = null;           // #pe-canvas-area — прокручиваемый холст
    let scaleViewport = null;        // #pe-scale-viewport — обрезает масштабированную страницу
    let scaleBox = null;             // #pe-scale-box — обёртка страницы под transform: scale()
    let serverUpdatedAt = '';        // защита от конфликтов (409)
    let draftSavedAt = '';           // saved_at последнего черновика
    let publishedAt = '';            // published_at/updated_at опубликованной версии
    const openedAt = Date.now();
    let device = 'desktop';          // desktop 1200 / tablet 768 / mobile 360
    const DEVICE_WIDTHS = {desktop: 1200, tablet: 768, mobile: 360};
    let pageWidth = DEVICE_WIDTHS[device];   // ширина страницы в обёртке (px)
    let scaleCurrent = 1;            // применённый масштаб холста
    let zoomMode = 'fit';            // 'fit' (по ширине) | число 0.5–1 (ручной zoom)
    let fullwidth = false;           // режим «Во всю ширину» (без масштаба)
    let leftCollapsed = false;       // свёрнута левая панель
    let rightCollapsed = false;      // свёрнута правая панель (на <1440px — оверлей закрыт)
    let pendingInsert = null;        // индекс вставки после клика «+ Добавить блок»
    let fontsCache = null;           // кэш /admin/api/fonts
    let activeTab = 'content';
    let typoElement = null;          // выбранный элемент в вкладке «Типографика»
    let editorBooted = false;

    /* История: undo/redo — снимки JSON blocks, лимит 50 */
    const HISTORY_LIMIT = 50;
    let history = [];
    let historyIdx = -1;
    let historyTimer = null;

    /* ============================ Каркас интерфейса ============================ */
    // Верхняя панель (48px, fixed)
    const topbar = el('div', '', {id: 'pe-topbar'});
    topbar.innerHTML =
        '<button type="button" id="pe-btn-menu" class="pe-tb-icon" title="Панель блоков">☰</button>' +
        '<span class="pe-title" title="Редактируемая страница">Редактор страницы</span>' +
        '<span class="pe-top-group">' +
        '<button type="button" id="pe-btn-undo" class="pe-tb-icon" title="Отменить (Ctrl+Z)" disabled>↶</button>' +
        '<button type="button" id="pe-btn-redo" class="pe-tb-icon" title="Повторить (Ctrl+Shift+Z)" disabled>↷</button>' +
        '</span>' +
        '<span class="pe-save-indicator" id="pe-save-indicator" title="Состояние сохранения">Сохранено</span>' +
        '<span class="pe-device-switch" id="pe-device-switch" title="Ширина страницы (на рендер не влияет)">' +
        '<button type="button" data-dev="desktop" class="pe-dev-active" title="Desktop 1200px">🖥 <span class="pe-dev-label">Desktop</span></button>' +
        '<button type="button" data-dev="tablet" title="Tablet 768px">📱 <span class="pe-dev-label">Tablet</span></button>' +
        '<button type="button" data-dev="mobile" title="Mobile 360px">📱 <span class="pe-dev-label">Mobile</span></button>' +
        '</span>' +
        '<button type="button" id="pe-btn-zoom" class="pe-tb-icon" title="Масштаб холста (Ctrl + колесо — зум 50–100%). Клик — по ширине">100%</button>' +
        '<button type="button" id="pe-btn-fullwidth" title="Во всю ширину: растянуть страницу на весь холст без масштаба">⇤⇥</button>' +
        '<button type="button" id="pe-btn-hotkeys" class="pe-tb-icon" title="Горячие клавиши (?)">?</button>' +
        '<span class="pe-top-spacer"></span>' +
        '<button type="button" id="pe-btn-pagesettings" title="SEO и настройки страницы"><span class="pe-tb-ico" aria-hidden="true">⚙</span><span class="pe-tb-label">Настройки страницы</span></button>' +
        '<button type="button" id="pe-btn-versions" title="Версии и снапшоты страницы"><span class="pe-tb-ico" aria-hidden="true">⧉</span><span class="pe-tb-label">Версии</span></button>' +
        '<button type="button" id="pe-btn-preview" title="Предпросмотр как у посетителя"><span class="pe-tb-ico" aria-hidden="true">▶</span><span class="pe-tb-label">Предпросмотр</span></button>' +
        '<button type="button" id="pe-btn-hideui" class="pe-tb-icon" title="Скрыть все панели и посмотреть на страницу (Esc — вернуть панели)">👁</button>' +
        '<span class="pe-badge-status pe-badge-draft" id="pe-status-badge" title="Состояние публикации">…</span>' +
        '<button type="button" id="pe-btn-draft" title="Сохранить черновик (Ctrl+S)"><span class="pe-tb-ico" aria-hidden="true">💾</span><span class="pe-tb-label">Сохранить черновик</span></button>' +
        '<button type="button" id="pe-btn-publish" class="pe-primary" title="Опубликовать: изменения станут видны посетителям"><span class="pe-tb-ico" aria-hidden="true">🚀</span><span class="pe-tb-label">Опубликовать</span></button>' +
        '<button type="button" id="pe-btn-exit" title="Выйти из редактора"><span class="pe-tb-ico" aria-hidden="true">⎋</span><span class="pe-tb-label">Выйти</span></button>';

    // Левая панель: библиотека блоков + структура страницы
    const leftPanel = el('div', '', {id: 'pe-left'});
    leftPanel.innerHTML =
        '<div class="pe-panel-topbar"><span class="pe-panel-caption">Блоки и структура</span>' +
        '<button type="button" id="pe-left-toggle" class="pe-panel-toggle" title="Свернуть панель — холст станет шире">◀</button></div>' +
        '<div class="pe-panel-section">' +
        '<div class="pe-panel-head">Библиотека блоков</div>' +
        '<input type="text" id="pe-lib-search" placeholder="Поиск блока…" title="Поиск по названию блока">' +
        '<div id="pe-lib"></div>' +
        '</div>' +
        '<div class="pe-panel-section pe-panel-grow">' +
        '<div class="pe-panel-head">Контент страницы</div>' +
        '<div id="pe-structure-list"></div>' +
        '</div>';

    // Узкая полоса-«рейка» для разворачивания левой панели, когда она свёрнута
    const leftRail = el('div', '', {id: 'pe-left-rail'});
    leftRail.innerHTML = '<button type="button" id="pe-left-open" class="pe-rail-btn" title="Показать панель блоков">▶</button>';

    // Правая панель: настройки выбранного блока
    const rightPanel = el('div', '', {id: 'pe-right'});
    rightPanel.innerHTML =
        '<div class="pe-panel-topbar"><span class="pe-panel-caption" id="pe-right-title">Настройки блока</span>' +
        '<button type="button" id="pe-right-toggle" class="pe-panel-toggle" title="Свернуть панель — холст станет шире">▶</button></div>' +
        '<div id="pe-right-tabs"></div>' +
        '<div id="pe-right-body" class="pe-panel-grow"></div>';

    // Узкая полоса-«рейка» для разворачивания правой панели, когда она свёрнута
    const rightRail = el('div', '', {id: 'pe-right-rail'});
    rightRail.innerHTML = '<button type="button" id="pe-right-open" class="pe-rail-btn" title="Показать настройки блока">◀</button>';

    // Каркас холста: workspace (flex-строка) → canvasArea (прокрутка) → viewport → scaleBox (страница).
    // В режиме обёртки скелетон уже размечен в page_editor_wrapper.html (iframe грузится
    // сразу ровно в 1200px, и цепочку iframe ничего не двигает — перемещение iframe
    // по DOM могло бы перезапустить его загрузку) — берём ноды из шаблона.
    workspaceEl = $('pe-workspace') || el('div', '', {id: 'pe-workspace'});
    canvasArea = $('pe-canvas-area') || el('div', '', {id: 'pe-canvas-area'});
    scaleViewport = $('pe-scale-viewport') || el('div', '', {id: 'pe-scale-viewport'});
    scaleBox = $('pe-scale-box') || el('div', '', {id: 'pe-scale-box'});
    if (scaleViewport.parentElement !== canvasArea) canvasArea.appendChild(scaleViewport);
    if (scaleBox.parentElement !== scaleViewport) scaleViewport.appendChild(scaleBox);

    // Подложка правой панели-оверлея (узкие окна <1440px): клик/Esc закрывает панель
    const rightBackdrop = el('div', '', {id: 'pe-right-backdrop'});

    // Подсказка, когда холст уже 360px
    const canvasHint = el('div', '', {id: 'pe-canvas-hint'});
    canvasHint.textContent = 'Холст слишком узкий — сверните панели, чтобы редактировать страницу.';
    canvasHint.style.display = 'none';

    // Оверлеи холста (механика перенесена из редактора цикла 1)
    const highlight = el('div', 'pe-highlight');
    const toolbar = el('div', 'pe-toolbar');
    highlight.appendChild(toolbar);
    const dropLine = el('div', 'pe-drop-line');
    const badgesLayer = el('div', '', {id: 'pe-badges'});
    const emptyState = el('div', 'pe-empty-state');
    emptyState.innerHTML = '<div class="pe-empty-title">Страница пуста</div>' +
        '<div>Добавьте первый блок из библиотеки слева — нажмите на пресет или перетащите карточку на холст.</div>';
    emptyState.style.display = 'none';

    const addBtn = el('button', 'pe-addbtn', {type: 'button', title: 'Добавить блок ниже (выбор — в панели слева)'});
    addBtn.innerHTML = '<span class="plus">+</span> Добавить блок';

    // «Мост» между блоком и кнопкой: пока курсор идёт от блока к кнопке,
    // hover не сбрасывается и кнопка не пропадает (механика цикла 1).
    const addBtnBridge = el('div', '', {id: 'pe-addbtn-bridge'});
    let addBtnHover = false;
    addBtn.addEventListener('mouseenter', () => { addBtnHover = true; });
    addBtn.addEventListener('mouseleave', () => { addBtnHover = false; updateOverlayPositions(); });
    addBtnBridge.addEventListener('mouseenter', () => { addBtnHover = true; });
    addBtnBridge.addEventListener('mouseleave', () => { addBtnHover = false; updateOverlayPositions(); });
    addBtnBridge.addEventListener('click', e => { e.stopPropagation(); armInsert(addBtnBridge.dataset.target); });

    const legacyBanner = el('div', '', {id: 'pe-legacy-banner'});
    legacyBanner.textContent = 'Страница перенесённого дизайна — доступно редактирование секциями. Добавляйте новые блоки выше и ниже существующих.';
    legacyBanner.style.display = 'none';

    // Плавающая кнопка возврата панелей из режима чистого просмотра (👁)
    const uiRestore = el('button', '', {id: 'pe-ui-restore', type: 'button',
        title: 'Вернуть панели редактора (Esc)'});
    uiRestore.innerHTML = '🛠 Показать панели редактора';
    uiRestore.addEventListener('click', () => setUiHidden(false));
    let uiHidden = false;
    function setUiHidden(v) {
        if (uiHidden === !!v) return;
        if (v && textEdit.active) finishTextEdit(true);
        uiHidden = !!v;
        document.body.classList.toggle('pe-ui-hidden', uiHidden);
        if (uiHidden) {
            hoverIdx = -1;
            updateOverlayPositions();
        } else {
            updateOverlayPositions();
        }
    }

    /* ============================== Boot ============================== */
    document.addEventListener('DOMContentLoaded', boot);
    /* defer-скрипт выполняется при readyState 'interactive' — до DOMContentLoaded,
       но после него ещё не инициализированы const ниже (mediaOverlay и др.).
       Откладываем boot до завершения всего скрипта. */
    if (document.readyState !== 'loading') setTimeout(boot, 0);

    function boot() {
        if (editorBooted) return;
        editorBooted = true;
        document.body.classList.add('pe-active');
        if (IFRAME_MODE) {
            // Скелетон с iframe уже на своих местах (шаблон обёртки) — цепочку
            // iframe не перемещаем. Размеры/события контента — после загрузки.
            contentFrame = $('pe-content-frame');
        } else {
            // Переносим весь контент body (реальная страница сайта: шапка, #content,
            // подвал, скрипты) в масштабируемую обёртку — до добавления нод редактора.
            [...document.body.childNodes].forEach(n => scaleBox.appendChild(n));
        }
        scaleBox.appendChild(emptyState);
        if (IFRAME_MODE) {
            workspaceEl.insertBefore(leftRail, canvasArea);
            workspaceEl.insertBefore(leftPanel, canvasArea);
            workspaceEl.append(rightRail, rightPanel);
            canvasArea.append(legacyBanner, badgesLayer, highlight, dropLine,
                addBtn, addBtnBridge, rightBackdrop, canvasHint);
        } else {
            workspaceEl.append(leftRail, leftPanel, canvasArea, rightRail, rightPanel);
            canvasArea.append(legacyBanner, scaleViewport, badgesLayer, highlight, dropLine,
                addBtn, addBtnBridge, rightBackdrop, canvasHint);
        }
        document.body.append(topbar, workspaceEl, mediaOverlay, versionsOverlay,
            pageSettingsOverlay, toastBox, uiRestore);
        restoreLayoutState();
        bindPanelControls();
        bindZoomControls();
        observeCanvas();
        $('pe-lib-search').addEventListener('input', debounce(renderLibrary, 200));
        bindTopbar();
        bindCanvasEvents();
        closeRightPanel();
        document.addEventListener('keydown', onKeyDown);
        // Предупреждение при закрытии вкладки с несохранёнными изменениями (CN-P 3.5)
        window.addEventListener('beforeunload', e => {
            if (!dirty) return;
            e.preventDefault();
            e.returnValue = '';
        });
        if (IFRAME_MODE) armFrameLifecycle();
        else loadInitial().catch(e => {
            setSaveState('error');
            toast('Ошибка загрузки: ' + e.message, 'error');
        });
    }

    /* Жизненный цикл iframe-контента. Первый вход — запуск редактора; каждая
       следующая загрузка — смена устройства (applyDevice): свежий документ
       рендерится сразу под честную ширину, затем мы вписываем в него текущие
       блоки черновика и перестраиваем карту/оверлеи. */
    let frameReady = false;
    let editorStarted = false;

    function armFrameLifecycle() {
        if (!contentFrame) { setTimeout(() => onFrameLoaded(), 200); return; }
        contentFrame.addEventListener('load', () => setTimeout(onFrameLoaded, 60));
        const poll = () => {
            if (frameReady) return;
            try {
                if (contentFrame.contentDocument &&
                    contentFrame.contentDocument.readyState === 'complete') {
                    onFrameLoaded();
                    return;
                }
            } catch (e) { /* ещё не доступен */ }
            setTimeout(poll, 150);
        };
        poll();
        setTimeout(() => { if (!frameReady) onFrameLoaded(); }, 8000);
    }

    /* js-load-block (вкладки каталога, INSTAGRAMM, FAVORIT_ITEM и т.п.) грузят
       контент через IntersectionObserver: iAppear ждёт, пока блок попадёт в
       видимую область окна. В редакторе окно iframe не скроллится — скроллит
       родительский холст, поэтому IO не срабатывает и блоки вечно висят
       прелоадером. Повторяем здесь логику InitJsLoadBlock самого шаблона:
       тот же URL (data-file + BLOCK/SHOW_ALL), тот же таргет, тот же разбор
       ответа через BX.processHTML. Помечаем data-i-appeared, чтобы штатный
       обработчик (если IO всё же дёрнется) не загрузил блок повторно. */
    function forceLazyBlocks() {
        if (!CD || !CW) return;
        const els = [...CD.querySelectorAll('.js-load-block[data-file]')]
            .filter(e => !e.dataset.iAppeared);
        if (!els.length) return;
        const loadOne = () => {
            const el = els.shift();
            if (!el) { updateFrameHeight(); return; }
            el.dataset.iAppeared = 'true';
            let url;
            try { url = new URL(el.dataset.file, CD.location.origin); }
            catch (e) { loadOne(); return; }
            if (el.dataset.block) url.searchParams.set('BLOCK', el.dataset.block);
            if (el.dataset.showAll) url.searchParams.set('SHOW_ALL', 'Y');
            fetch(url).then(r => {
                if (!r.ok) throw new Error('http ' + r.status);
                return r.text();
            }).then(html => {
                const target = el.dataset.appendTo
                    ? el.querySelector(el.dataset.appendTo)
                    : (el.querySelector(':scope > div[id*="bx_incl_"]') || el);
                el.removeAttribute('data-file');
                el.classList.remove('loader_circle');
                if (target) {
                    if (CW.BX && CW.BX.processHTML && CW.BX.ajax && CW.BX.ajax.processScripts) {
                        const ob = CW.BX.processHTML(html);
                        target.innerHTML = ob.HTML;
                        CW.BX.ajax.processScripts(ob.SCRIPT);
                    } else {
                        target.innerHTML = html;
                    }
                    /* раскрыть data-src/data-bg в подгруженном HTML */
                    if (typeof CW.__lazyFixAll === 'function') CW.__lazyFixAll();
                }
                updateFrameHeight();
                loadOne();
            }).catch(() => loadOne());
        };
        loadOne();
    }

    function onFrameLoaded() {
        if (frameReady) return;
        try {
            CD = contentFrame.contentDocument;
            CW = contentFrame.contentWindow;
        } catch (e) { CD = null; CW = null; }
        if (!CD || !CD.getElementById('content')) {
            // документ ещё не разобран — повторим позже
            setTimeout(onFrameLoaded, 200);
            return;
        }
        frameReady = true;
        contentEl = CD.getElementById('content');
        bindContentDocument();
        bindLibraryDnD();          // DnD вешается на contentEl — после каждой загрузки заново
        updateFrameHeight();
        forceLazyBlocks();         // js-load-block грузятся по IO со скроллом окна — в
        setTimeout(forceLazyBlocks, 2500);   // iframe редактора окно не скроллится,
                                             // поэтому подгружаем их принудительно
        if (!editorStarted) {
            editorStarted = true;
            loadInitial().catch(e => {
                setSaveState('error');
                toast('Ошибка загрузки: ' + e.message, 'error');
            });
        } else {
            syncContentAfterReload();
        }
    }

    // После перезагрузки iframe (смена устройства) в свежем документе лежит
    // опубликованный рендер — вписываем актуальные блоки черновика.
    async function syncContentAfterReload() {
        buildMap();
        if (!hasLegacy) {
            try {
                const result = await apiPost('/preview-marked', {blocks}).then(r => r.json());
                if (result.success && frameReady) {
                    replaceContentFromHtml(result.content_html);
                    if (typeof CW.__lazyFixAll === 'function') CW.__lazyFixAll();
                    return;
                }
            } catch (e) { /* оставляем опубликованный рендер */ }
        }
        if (zero.active) rebuildZeroOverlay();
        updateOverlayPositions();
        scheduleScaleUpdate();
    }

    // Обработчики, которые в iframe-режиме живут в документе контента: мышь/клики
    // над iframe не всплывают в родительский документ. Координаты этих событий —
    // в системе viewport iframe (без scale); все геометрические helper'ы выше это
    // учитывают.
    function bindContentDocument() {
        if (!CD) return;
        CD.addEventListener('mousemove', onMouseMove);
        CD.addEventListener('scroll', updateOverlayPositions, {passive: true});
        CD.addEventListener('keydown', onKeyDown);
        CD.addEventListener('click', onDocClick, true);
        CD.addEventListener('dblclick', onDocDblClick, true);
        CD.addEventListener('wheel', onCanvasWheel, {passive: false});
        // Высота iframe = высоте документа контента (пересчёт при изменениях).
        updateFrameHeight();
        if (CW && CW.ResizeObserver) {
            const ro = new CW.ResizeObserver(() => scheduleScaleUpdate());
            if (CD.body) ro.observe(CD.body);
            if (CD.documentElement) ro.observe(CD.documentElement);
        }
    }

    async function loadInitial() {
        const d = await apiGet(`/page-blocks?slug=${encodeURIComponent(SLUG)}`);
        if (!d.success) throw new Error(d.error || 'ошибка загрузки блоков');
        blocks = d.blocks;
        pageMeta = d.page || {};
        hasLegacy = !!d.legacy;
        serverUpdatedAt = d.updated_at || '';
        history = [JSON.stringify(blocks)];
        historyIdx = 0;
        updateHistoryButtons();

        const draft = await fetchDraft().catch(() => null);
        if (draft && Array.isArray(draft.blocks) && draft.blocks.length) {
            const savedAt = Date.parse(draft.saved_at || '');
            if (!isNaN(savedAt) && savedAt > openedAt - 60000) {
                if (confirm('Найден несохранённый черновик этой страницы (сохранён ' +
                        new Date(savedAt).toLocaleString('ru-RU') + '). Восстановить черновик?')) {
                    blocks = draft.blocks;
                    markDirty();
                }
            }
        }
        if (draft && draft.saved_at) draftSavedAt = draft.saved_at;
        if (draft && draft.published_at) publishedAt = draft.published_at;
        updateStatusBadge();

        topbar.querySelector('.pe-title').textContent = '🛠 ' + (pageMeta.h1 || pageMeta.title || pageMeta.url || SLUG);
        legacyBanner.style.display = hasLegacy ? 'block' : 'none';
        setupCanvas();
        buildMap();
        await loadMyBlocks();
        await loadSectionPresets();
        renderLibrary();
        renderStructure();
        startDraftAutosave();
        setSaveState('saved');
    }

    // Холст: контент уже перенесён в #pe-scale-box на boot(); здесь — применение
    // ширины устройства (пересчёт масштаба / перезагрузка iframe). В iframe-режиме
    // contentEl и DnD настраиваются в onFrameLoaded (после загрузки документа).
    function setupCanvas() {
        if (IFRAME_MODE) {
            if (!contentEl) return;
        } else {
            contentEl = document.getElementById('content');
            if (!contentEl) return;
            bindLibraryDnD();
        }
        applyDevice();
    }
    function applyDevice() {
        pageWidth = DEVICE_WIDTHS[device] || DEVICE_WIDTHS.desktop;
        if (zero.active) rebuildZeroOverlay();
        scheduleScaleUpdate();
        if (IFRAME_MODE && contentFrame) {
            if (!deviceAppliedOnce) {
                // Первое применение — ширина 1200 уже задана в шаблоне обёртки,
                // перезагружать только что загруженный iframe не нужно.
                deviceAppliedOnce = true;
                return;
            }
            if (!frameReady) return;   // перезагрузка по смене устройства уже идёт
            // Скриптовые виджеты сайта (слайдеры/меню) перестраиваются под новую
            // ширину нестабильно (синтетический resize им недостаточен) — поэтому
            // честнее перезагрузить iframe: свежий документ рендерится сразу под
            // реальную ширину устройства, а черновик вписываем после загрузки.
            if (textEdit.active) finishTextEdit(true);
            if (zero.active) closeZero();
            if (dirty) saveDraft();
            frameReady = false;
            applyScale();   // ширина scaleBox — ДО перезагрузки: iframe должен стартовать сразу под новую ширину
            // Высоту сбрасываем ДО перезагрузки: у fixed-панелей сайта
            // (#mobilefilter, #mobilemenu) заданы top:0;bottom:0 — в высоком
            // окне iframe они растягиваются на всю высоту вьюпорта и раздувают
            // scrollable overflow, из-за чего высота застревала на значении
            // предыдущего устройства. В низком вьюпорте панели остаются
            // компактными, а после загрузки высоту пересчитает updateFrameHeight.
            contentFrame.style.height = '600px';
            setTimeout(() => {
                const sep = PE_CONTENT_URL.includes('?') ? '&' : '?';
                contentFrame.src = PE_CONTENT_URL + sep + 'pe_bare=1&ts=' + Date.now();
                setTimeout(() => { if (!frameReady) onFrameLoaded(); }, 8000);
            }, 60);
        }
    }
    let deviceAppliedOnce = false;

    // Высота iframe = высоте документа контента (иначе появится внутренняя
    // прокрутка iframe и scale-обёртка не покажет страницу целиком).
    // ВАЖНО: замеряем НЕ scrollHeight, а нижнюю границу контента: fixed-панели
    // сайта (#mobilefilter, #mobilemenu, ...) растягиваются на высоту окна
    // iframe (top:0;bottom:0) и входят в scrollable overflow — при обратной
    // связи «iframe высоты H → панель высоты H → scrollHeight = H» высота
    // застревала на значении предыдущего устройства.
    function measureContentHeight() {
        let h = 0;
        const win = CW, doc = CD;
        if (!win || !doc || !doc.body) return 0;
        const sy = win.pageYOffset || 0;
        const walk = (el, inFixed) => {
            for (const c of el.children) {
                let skip = inFixed;
                if (!skip && win.getComputedStyle(c).position === 'fixed') skip = true;
                if (!skip) {
                    const r = c.getBoundingClientRect();
                    if (r.height > 0 && r.bottom + sy > h) h = r.bottom + sy;
                }
                walk(c, skip);
            }
        };
        walk(doc.body, false);
        return Math.ceil(h);
    }
    function updateFrameHeight() {
        if (!IFRAME_MODE || !CD || !contentFrame) return;
        let h = 0;
        try { h = measureContentHeight(); } catch (e) { return; }
        if (h <= 0) {
            try {
                h = Math.max(CD.body ? CD.body.scrollHeight : 0,
                             CD.documentElement ? CD.documentElement.scrollHeight : 0);
            } catch (e) { return; }
        }
        if (h > 0) contentFrame.style.height = h + 'px';
    }

    /* ==================== Панели: сворачивание и оверлей (CN-L 1.1/1.5) ==================== */
    // Ширина окна < 1440px — «узкий режим»: правая панель открывается оверлеем
    // поверх холста, левая по умолчанию свёрнута (если пользователь сам не менял).
    function narrowMode() { return window.innerWidth < 1440; }

    function applyPanelState() {
        document.body.classList.toggle('pe-left-collapsed', leftCollapsed);
        document.body.classList.toggle('pe-right-collapsed', rightCollapsed);
        document.body.classList.toggle('pe-right-overlay', narrowMode() && !rightCollapsed);
    }

    function setLeftCollapsed(v, user) {
        leftCollapsed = !!v;
        if (user) localStorage.setItem('pe_left_collapsed', leftCollapsed ? '1' : '0');
        applyPanelState();
        scheduleScaleUpdate();
    }
    function setRightCollapsed(v, user) {
        rightCollapsed = !!v;
        if (user) localStorage.setItem('pe_right_collapsed', rightCollapsed ? '1' : '0');
        applyPanelState();
        scheduleScaleUpdate();
    }

    function restoreLayoutState() {
        const narrow = narrowMode();
        const leftSet = localStorage.getItem('pe_left_collapsed') !== null;
        const rightSet = localStorage.getItem('pe_right_collapsed') !== null;
        leftCollapsed = leftSet ? localStorage.getItem('pe_left_collapsed') === '1' : narrow;
        rightCollapsed = rightSet ? localStorage.getItem('pe_right_collapsed') === '1' : narrow;
        const z = parseFloat(localStorage.getItem('pe_zoom'));
        zoomMode = z >= 0.5 && z <= 1 ? z : 'fit';
        fullwidth = localStorage.getItem('pe_fullwidth') === '1';
        applyPanelState();
        updateFullwidthButton();
    }

    function bindPanelControls() {
        $('pe-left-toggle').addEventListener('click', () => setLeftCollapsed(true, true));
        $('pe-left-open').addEventListener('click', () => setLeftCollapsed(false, true));
        $('pe-right-toggle').addEventListener('click', () => setRightCollapsed(true, true));
        $('pe-right-open').addEventListener('click', () => setRightCollapsed(false, true));
        rightBackdrop.addEventListener('click', () => setRightCollapsed(true));
    }

    /* ==================== Масштаб холста: scale-to-fit, zoom, «во всю ширину» (CN-L 1.2–1.4) ==================== */
    function computeScale() {
        const cw = canvasArea ? Math.max(canvasArea.clientWidth, 1) : window.innerWidth;
        if (fullwidth) return {s: 1, w: cw};
        const fit = Math.min(1, cw / pageWidth);
        const s = zoomMode === 'fit' ? fit : Math.min(1, Math.max(0.5, zoomMode));
        return {s, w: pageWidth};
    }

    function applyScale() {
        if (!canvasArea || !scaleBox) return;
        const cs = computeScale();
        scaleCurrent = cs.s;
        scaleBox.style.width = cs.w + 'px';
        // Центрирование: CSS держит left:50%, компенсируем половиной ширины.
        // Работает и когда страница шире холста (масштаб < 100%).
        scaleBox.style.marginLeft = (-cs.w / 2) + 'px';
        scaleBox.style.transform = cs.s >= 0.999 ? 'none' : 'scale(' + cs.s + ')';
        // Компенсация высоты: layout-высота контента (transform на offsetHeight
        // не влияет) × масштаб = визуальная высота страницы на холсте.
        const h = scaleBox.offsetHeight;
        scaleViewport.style.height = Math.max(1, Math.round(h * cs.s)) + 'px';
        updateZoomButton();
        checkCanvasWidth();
        updateOverlayPositions();
    }
    const scheduleScaleUpdate = debounce(applyScale, 100);

    function checkCanvasWidth() {
        if (!canvasArea || !canvasHint) return;
        canvasHint.style.display = canvasArea.clientWidth < 360 ? 'block' : 'none';
    }

    function updateZoomButton() {
        const b = $('pe-btn-zoom');
        if (b) b.textContent = Math.round(scaleCurrent * 100) + '%';
    }
    function setZoom(z) {
        zoomMode = Math.min(1, Math.max(0.5, Math.round(z * 10) / 10));
        localStorage.setItem('pe_zoom', String(zoomMode));
        fullwidth = false;
        updateFullwidthButton();
        applyScale();
    }
    function resetZoom() {
        zoomMode = 'fit';
        localStorage.removeItem('pe_zoom');
        fullwidth = false;
        updateFullwidthButton();
        applyScale();
    }
    function updateFullwidthButton() {
        const b = $('pe-btn-fullwidth');
        if (b) b.classList.toggle('pe-on', fullwidth);
    }
    function setFullwidth(v) {
        fullwidth = !!v;
        localStorage.setItem('pe_fullwidth', fullwidth ? '1' : '');
        if (fullwidth) { zoomMode = 'fit'; localStorage.removeItem('pe_zoom'); }
        updateFullwidthButton();
        applyScale();
    }

    function bindZoomControls() {
        $('pe-btn-zoom').addEventListener('click', resetZoom);
        $('pe-btn-fullwidth').addEventListener('click', () => setFullwidth(!fullwidth));
        $('pe-btn-hotkeys').addEventListener('click', toggleHotkeysHelp);
        // Ctrl + колесо мыши — зум холста 50–100% (CN-L 1.3)
        document.addEventListener('wheel', e => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            setZoom(scaleCurrent + (e.deltaY < 0 ? 0.1 : -0.1));
        }, {passive: false});
    }

    /* ==================== Подсказка: горячие клавиши и кнопки панели ==================== */
    let hotkeysHelp = null;
    function toggleHotkeysHelp() {
        if (hotkeysHelp) { hotkeysHelp.remove(); hotkeysHelp = null; return; }
        const wrap = el('div', '', {id: 'pe-hotkeys'});
        wrap.innerHTML =
            '<div class="pe-hotkeys-panel"><div class="pe-hotkeys-head">Справка: кнопки и горячие клавиши' +
            '<button type="button" class="pe-hotkeys-close" title="Закрыть (Esc)">✕</button></div>' +
            '<div class="pe-hotkeys-body">' +
            '<div class="pe-hotkeys-section">Горячие клавиши</div>' +
            '<table class="pe-hotkeys-table">' +
            '<tr><td><kbd>Ctrl</kbd>+<kbd>S</kbd></td><td>Сохранить черновик</td></tr>' +
            '<tr><td><kbd>Ctrl</kbd>+<kbd>Z</kbd></td><td>Отменить действие</td></tr>' +
            '<tr><td><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd></td><td>Повторить действие</td></tr>' +
            '<tr><td><kbd>Delete</kbd> / <kbd>Backspace</kbd></td><td>Удалить выбранный блок</td></tr>' +
            '<tr><td><kbd>Esc</kbd></td><td>Закончить правку текста → закрыть панели/окна → снять выделение; в режиме чистого просмотра — вернуть панели</td></tr>' +
            '<tr><td><kbd>?</kbd></td><td>Открыть/закрыть эту справку</td></tr>' +
            '<tr><td><kbd>Ctrl</kbd>+<kbd>колесо</kbd></td><td>Масштаб холста 50–100%</td></tr>' +
            '<tr><td><kbd>←</kbd><kbd>↑</kbd><kbd>→</kbd><kbd>↓</kbd></td><td>Двигать элемент в Zero-блоке</td></tr>' +
            '<tr><td><kbd>Shift</kbd>+<kbd>←↑→↓</kbd></td><td>Изменить размер элемента в Zero-блоке</td></tr>' +
            '<tr><td><kbd>Ctrl</kbd>+<kbd>B</kbd> / <kbd>Ctrl</kbd>+<kbd>I</kbd></td><td>Жирный / курсив при правке текста (или кнопки B/I в мини-панели)</td></tr>' +
            '</table>' +
            '<div class="pe-hotkeys-section">Работа с блоками на холсте</div>' +
            '<table class="pe-hotkeys-table">' +
            '<tr><td>Клик по блоку</td><td>Выбрать блок — настройки появятся в правой панели</td></tr>' +
            '<tr><td>Двойной клик по тексту/картинке</td><td>Редактировать содержимое прямо на странице (Esc — закончить)</td></tr>' +
            '<tr><td>⠿ на плашке блока</td><td>Перетащить, чтобы переместить блок; ↑/↓ — сдвиг на одну позицию</td></tr>' +
            '<tr><td>⧉ на плашке блока</td><td>Дублировать блок</td></tr>' +
            '<tr><td>💾 на плашке блока</td><td>Сохранить в «Мои блоки» — можно вставить на любую страницу</td></tr>' +
            '<tr><td>🚫 на плашке блока</td><td>Скрыть блок на странице (не удаляя)</td></tr>' +
            '<tr><td>🗑 на плашке блока</td><td>Удалить блок (или клавиша Delete)</td></tr>' +
            '<tr><td>Клик по странице мимо блоков</td><td>Снять выделение</td></tr>' +
            '</table>' +
            '<div class="pe-hotkeys-section">Верхняя панель</div>' +
            '<table class="pe-hotkeys-table">' +
            '<tr><td>☰</td><td>Показать/скрыть левую панель «Контент страницы» (список блоков и библиотека)</td></tr>' +
            '<tr><td>↶ / ↷</td><td>Отменить / повторить (Ctrl+Z / Ctrl+Shift+Z)</td></tr>' +
            '<tr><td>Сохранено</td><td>Индикатор состояния: «Сохранено» / «Сохраняю…» / «Не сохранено»</td></tr>' +
            '<tr><td>🖥 / 📱 устройства</td><td>Ширина просмотра страницы (1200/768/360 px). Только для проверки адаптивности — на опубликованную страницу не влияет</td></tr>' +
            '<tr><td>100%</td><td>Масштаб холста. Клик — вернуть 100%. Ctrl+колесо — плавный зум 50–100%</td></tr>' +
            '<tr><td>⇤⇥</td><td>Во всю ширину: растянуть страницу на весь холст без масштабирования</td></tr>' +
            '<tr><td>?</td><td>Эта справка (клавиша ?)</td></tr>' +
            '<tr><td>⚙ Настройки страницы</td><td>SEO (title, description), заголовок H1, адрес (slug) страницы</td></tr>' +
            '<tr><td>⧉ Версии</td><td>Снапшоты страницы: посмотреть или восстановить любую версию</td></tr>' +
            '<tr><td>▶ Предпросмотр</td><td>Открыть страницу так, как её видят посетители</td></tr>' +
            '<tr><td>👁</td><td>Скрыть все панели и посмотреть на страницу целиком. Esc или плавающая кнопка — вернуть панели</td></tr>' +
            '<tr><td>Черновик / Опубликовано</td><td>Бейдж состояния публикации</td></tr>' +
            '<tr><td>💾 Сохранить черновик</td><td>Сохранить изменения без публикации (Ctrl+S). Посетители их не увидят</td></tr>' +
            '<tr><td>🚀 Опубликовать</td><td>Сохранить и опубликовать — изменения станут видны посетителям</td></tr>' +
            '<tr><td>⎋ Выйти</td><td>Выйти из редактора (черновик сохраняется автоматически)</td></tr>' +
            '</table>' +
            '<div class="pe-hotkeys-section">Боковые панели</div>' +
            '<table class="pe-hotkeys-table">' +
            '<tr><td>◀ / ▶ (правый верхний угол панели)</td><td>Свернуть панель — холст станет шире. У свёрнутой панели полоска у края холста: наведите курсор — появится кнопка открытия</td></tr>' +
            '<tr><td>Контент страницы (слева)</td><td>Список блоков страницы: порядок, переименование, скрытие, удаление; ниже — библиотека блоков для добавления</td></tr>' +
            '<tr><td>Настройки блока (справа)</td><td>Поля выбранного блока: тексты, картинки, ссылки, фон. Изменения применяются сразу, отменяются Ctrl+Z</td></tr>' +
            '</table>' +
            '</div></div>';
        wrap.addEventListener('click', e => { if (e.target === wrap) toggleHotkeysHelp(); });
        wrap.querySelector('.pe-hotkeys-close').addEventListener('click', () => toggleHotkeysHelp());
        document.body.appendChild(wrap);
        hotkeysHelp = wrap;
    }
    function closeHotkeysHelp() {
        if (hotkeysHelp) { hotkeysHelp.remove(); hotkeysHelp = null; }
    }

    // Пересчёт масштаба при ресайзе окна/панелей и изменении высоты контента страницы.
    function observeCanvas() {
        if (window.ResizeObserver) {
            const roArea = new ResizeObserver(() => scheduleScaleUpdate());
            roArea.observe(canvasArea);
            const roBox = new ResizeObserver(() => {
                // Высота контента изменилась (рендер блоков, загрузка картинок) —
                // обновляем компенсированную высоту вьюпорта без полного пересчёта.
                if (!scaleBox) return;
                const h = scaleBox.offsetHeight;
                scaleViewport.style.height = Math.max(1, Math.round(h * scaleCurrent)) + 'px';
            });
            roBox.observe(scaleBox);
        }
        window.addEventListener('resize', () => { applyPanelState(); scheduleScaleUpdate(); });
        if (IFRAME_MODE) {
            // Страховка для браузеров без ResizeObserver в iframe: поллинг высоты
            // (рендер блоков, загрузка картинок меняют scrollHeight).
            setInterval(updateFrameHeight, 700);
        }
    }

    /* ============================ Топбар: события ============================ */
    function bindTopbar() {
        $('pe-btn-publish').addEventListener('click', publish);
        $('pe-btn-draft').addEventListener('click', () => saveDraft(true));
        $('pe-btn-exit').addEventListener('click', exitEditor);
        $('pe-btn-undo').addEventListener('click', undo);
        $('pe-btn-redo').addEventListener('click', redo);
        $('pe-btn-menu').addEventListener('click', () => setLeftCollapsed(!leftCollapsed, true));
        $('pe-btn-pagesettings').addEventListener('click', openPageSettings);
        $('pe-btn-versions').addEventListener('click', openVersions);
        $('pe-btn-preview').addEventListener('click', openPreview);
        $('pe-btn-hideui').addEventListener('click', () => setUiHidden(true));
        $('pe-device-switch').addEventListener('click', e => {
            const btn = e.target.closest('button[data-dev]');
            if (!btn) return;
            device = btn.dataset.dev;
            document.querySelectorAll('#pe-device-switch button').forEach(b =>
                b.classList.toggle('pe-dev-active', b === btn));
            applyDevice();
        });
    }

    function setSaveState(state) {
        const ind = $('pe-save-indicator');
        if (!ind) return;
        ind.className = 'pe-save-indicator pe-save-' + state;
        ind.textContent = {saved: 'Сохранено', saving: 'Сохраняется…', error: 'Ошибка сохранения', dirty: 'Есть изменения'}[state] || state;
    }

    function updateStatusBadge() {
        const badge = $('pe-status-badge');
        if (!badge) return;
        const draftNewer = draftSavedAt && (!publishedAt || Date.parse(draftSavedAt) > Date.parse(publishedAt));
        badge.textContent = draftNewer ? 'Черновик' : 'Опубликовано';
        badge.className = 'pe-badge-status ' + (draftNewer ? 'pe-badge-draft' : 'pe-badge-published');
        badge.title = draftNewer
            ? 'Есть изменения, которые ещё не опубликованы'
            : 'Опубликованная версия актуальна';
    }

    function markDirty() {
        dirty = true;
        setSaveState('dirty');
        scheduleDraftSave();
    }

    /* ==================== История: undo/redo (лимит 50) ==================== */
    function recordHistory() {
        clearTimeout(historyTimer);
        history = history.slice(0, historyIdx + 1);
        history.push(JSON.stringify(blocks));
        if (history.length > HISTORY_LIMIT) history.shift();
        historyIdx = history.length - 1;
        updateHistoryButtons();
    }
    function recordHistoryDebounced() {
        clearTimeout(historyTimer);
        historyTimer = setTimeout(recordHistory, 500);
    }
    function updateHistoryButtons() {
        const u = $('pe-btn-undo'), r = $('pe-btn-redo');
        if (u) u.disabled = historyIdx <= 0;
        if (r) r.disabled = historyIdx >= history.length - 1;
    }
    function restoreHistory() {
        blocks = JSON.parse(history[historyIdx]);
        selectedIdx = -1;
        typoElement = null;
        markDirty();
        if (hasLegacy) { saveDraft(); location.reload(); return; }
        closeRightPanel();
        renderStructure();
        refreshPage();
    }
    function undo() {
        if (historyIdx <= 0) { toast('Отменить нечего'); return; }
        historyIdx--;
        updateHistoryButtons();
        restoreHistory();
    }
    function redo() {
        if (historyIdx >= history.length - 1) { toast('Повторить нечего'); return; }
        historyIdx++;
        updateHistoryButtons();
        restoreHistory();
    }

    /* ==================== Автосохранение черновика (CN-T10) ==================== */
    let draftTimer = null;
    function scheduleDraftSave() {
        clearTimeout(draftTimer);
        draftTimer = setTimeout(() => saveDraft(), 5000);
    }
    async function fetchDraft() {
        try {
            const resp = await fetch(API(`/page-draft?slug=${encodeURIComponent(SLUG)}`));
            if (!resp.ok) return null;
            const result = await resp.json();
            return result.success ? result : null;
        } catch (e) { return null; }
    }
    async function saveDraft(manual) {
        if (textEdit.active) return;
        if (!dirty && !manual) return;
        setSaveState('saving');
        try {
            const resp = await apiPost('/page-draft', {slug: SLUG, blocks});
            const result = await resp.json();
            if (result.success) {
                draftSavedAt = result.saved_at || new Date().toISOString();
                setSaveState('saved');
                updateStatusBadge();
            } else {
                setSaveState('error');
                if (manual) toast('Ошибка сохранения черновика: ' + (result.error || ''), 'error');
            }
        } catch (e) {
            setSaveState('error');
            if (manual) toast('Ошибка сети: ' + e.message, 'error');
        }
    }
    function startDraftAutosave() {
        setInterval(() => saveDraft(), 30000);   // раз в 30 секунд при dirty
    }

    /* ==================== Карта блоков на странице (маркеры) ==================== */
    function buildMap() {
        blockMap = [];
        if (contentEl) {
            // Маркеры <!--blk:N--> обычно — прямые дети #content, но в части
            // шаблонов content_html обёрнут в <div class="middle"> (ищем реальный
            // контейнер маркеров, иначе карта блоков пустая и клики/ховер мертвы).
            let container = null;
            const doc = contentEl.ownerDocument || document;
            const NF = (doc.defaultView && doc.defaultView.NodeFilter) ? doc.defaultView.NodeFilter : NodeFilter;
            const tw = doc.createTreeWalker(contentEl, NF.SHOW_COMMENT);
            let cn;
            while ((cn = tw.nextNode())) {
                if (/^\s*blk:\d+\s*$/.test(cn.nodeValue)) { container = cn.parentNode; break; }
            }
            const root = container || contentEl;
            let cur = null;
            [...root.childNodes].forEach(n => {
                if (n.nodeType === Node.COMMENT_NODE) {
                    const m = n.nodeValue.match(/^\s*blk:(\d+)\s*$/);
                    if (m) { cur = {i: +m[1], elements: [], start: n, nodes: []}; blockMap.push(cur); return; }
                    if (n.nodeValue.trim() === '/blk') { cur = null; return; }
                }
                if (cur && n.nodeType === Node.ELEMENT_NODE) { cur.elements.push(n); cur.nodes.push(n); }
            });
            blockMap.sort((a, b) => a.i - b.i);
        }
        emptyState.style.display = (!hasLegacy && blocks.length === 0) ? 'block' : 'none';
        renderBadges();
        renderStructure();
    }

    function entryAt(idx) { return blockMap.find(e => e.i === idx); }

    // Координаты в «документно-визуальной» системе холста: getBoundingClientRect
    // даёт визуальные координаты (с учётом transform: scale), мы переводим их
    // в систему оверлеев #pe-canvas-area (absolute-элементы скроллятся вместе
    // с содержимым: +scrollTop по вертикали, сдвиг на areaRect по горизонтали).
    // clientX/clientY мыши сравниваются с этими же визуальными координатами
    // через canvasArea.getBoundingClientRect() — обе системы визуальные,
    // поэтому scale делить НЕ нужно (сокращается).
    function entryRect(idx) {
        const entry = entryAt(idx);
        if (!entry || !entry.elements.length || !canvasArea) return null;
        const first = contentRect(entry.elements[0]);
        const last = contentRect(entry.elements[entry.elements.length - 1]);
        const ar = canvasArea.getBoundingClientRect();
        const st = canvasArea.scrollTop;
        const cr = contentEl ? contentRect(contentEl) : {left: ar.left, right: ar.right};
        return {
            top: first.top - ar.top + st,
            bottom: last.bottom - ar.top + st,
            left: Math.min(first.left, cr.left) - ar.left,
            right: Math.max(first.right, cr.right) - ar.left,
        };
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

    /* ==================== Плашки блоков (badge + мини-драг) ==================== */
    const badges = new Map();
    function renderBadges() {
        badgesLayer.innerHTML = '';
        badges.clear();
        blockMap.forEach(entry => {
            const b = blocks[entry.i];
            const badge = el('div', 'pe-badge');
            badge.innerHTML = '<span class="pe-badge-num">' + (entry.i + 1) + '</span>' + esc(blockLabel(b));
            badge.title = 'Перетащите, чтобы переместить блок; клик — выделить';
            badge.addEventListener('mousedown', e => { e.preventDefault(); select(entry.i); startDrag(entry.i, e); });
            badge.addEventListener('click', e => { e.stopPropagation(); select(entry.i); });
            badgesLayer.appendChild(badge);
            badges.set(entry.i, badge);
        });
        positionBadges();
    }
    function positionBadges() {
        if (dragState.active) return;
        blockMap.forEach(entry => {
            const badge = badges.get(entry.i);
            if (!badge) return;
            const r = entryRect(entry.i);
            if (!r || r.bottom - r.top < 2) { badge.style.display = 'none'; return; }
            badge.style.display = 'flex';
            badge.style.top = r.top + 'px';
            badge.style.left = r.left + 'px';
            badge.classList.toggle('pe-badge-selected', entry.i === selectedIdx);
        });
    }

    /* CSS-классы секций дизайна → читаемые имена (как SECTION_NAMES в
       migrate_sections.py). Порядок важен: специфичные ключи раньше общих. */
    const SECTION_CLASS_NAMES = [
        ['TOP_BIG_BANNERS', 'Главный баннер'],
        ['BIG_BANNER_INDEX', 'Главный баннер'],
        ['TIZERS', 'Преимущества'],
        ['MIDDLE_ADV', 'Промо-блок'],
        ['FLOAT_BANNERS', 'Баннеры'],
        ['CATALOG_TAB', 'Вкладки каталога'],
        ['BRANDS', 'Бренды'],
        ['REVIEWS', 'Отзывы'],
        ['COMPANY_TEXT', 'О компании'],
        ['INSTAGRAMM', 'Фото из Instagram'],
        ['BOTTOM_BANNERS', 'Нижние баннеры'],
        ['CATALOG_SECTIONS', 'Разделы каталога'],
        ['CATALOG_SECTION_LIST', 'Разделы каталога'],
        ['SALE', 'Акции'],
        ['NEWS', 'Новости'],
        ['BLOG', 'Блог'],
        ['MAPS', 'Карта'],
        ['FAVORIT_ITEM', 'Популярные товары'],
        ['LOOKBOOKS', 'Лукбук'],
        ['STORIES', 'Истории'],
        ['SERVICES', 'Услуги'],
        ['GALLERY', 'Галерея'],
        ['PARTNERS', 'Партнёры'],
        ['PROJECTS', 'Проекты'],
        ['VK_VIDEO', 'Видео VK'],
        ['YOUTUBE', 'Видео YouTube'],
        ['RUTUBE', 'Видео Rutube'],
    ];

    /* Производное название секции: по CSS-классу, затем по первому
       заголовку, затем по текстовому содержимому. */
    function sectionDerivedName(b) {
        const html = b.html || '';
        const classes = html.match(/class="([^"]*)"/gi) || [];
        for (const cls of classes) {
            const tokens = cls.slice(7, -1).toUpperCase().split(/\s+/);
            for (const pair of SECTION_CLASS_NAMES) {
                if (tokens.indexOf(pair[0]) !== -1) return pair[1];
            }
        }
        const h = html.match(/<h[123][^>]*>([\s\S]*?)<\/h[123]>/i);
        if (h) {
            const t = h[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
            if (t) return t.slice(0, 40);
        }
        const t = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
        return t ? t.slice(0, 40) : 'Секция дизайна';
    }

    /* Осмысленное имя секции: заданное вручную, иначе — производное. */
    function sectionDisplayName(b) {
        const name = (b.name || '').trim();
        if (name && !/^секция\s*\d*$/i.test(name)) return name;
        return sectionDerivedName(b);
    }

    function blockLabel(b) {
        if (!b) return 'Блок';
        if (b.legacy) return '🧩 Дизайн страницы';
        if (b.type === 'section') return '🧩 ' + sectionDisplayName(b);
        const cfg = BLOCK_TYPES[b.type];
        if (b.name) return (cfg ? cfg.icon : '▦') + ' ' + b.name;
        return cfg ? cfg.icon + ' ' + cfg.label : '▦ ' + (b.type || 'text');
    }

    /* ==================== Подсветка, тулбар, кнопка «+» ==================== */
    function bindCanvasEvents() {
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('scroll', updateOverlayPositions, {passive: true});
        window.addEventListener('resize', updateOverlayPositions);
        // Скролл теперь живёт внутри холста (#pe-canvas-area), а не в окне.
        canvasArea.addEventListener('scroll', updateOverlayPositions, {passive: true});
        // Ctrl + колесо над холстом — ручной зум 50–100% (шаг 10).
        canvasArea.addEventListener('wheel', onCanvasWheel, {passive: false});
        if (IFRAME_MODE) {
            // Клик по подложке холста мимо iframe — снять выделение (сам iframe
            // клики в родителя не отдаёт, их обрабатывает bindContentDocument).
            canvasArea.addEventListener('mousedown', e => {
                if (e.target === canvasArea || e.target === scaleViewport) select(-1);
            });
        }
    }

    function onCanvasWheel(e) {
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        const cur = zoomMode === 'fit' ? computeScale().s : zoomMode;
        setZoom(cur + (e.deltaY < 0 ? 0.1 : -0.1));
    }

    function onMouseMove(e) {
        // В iframe-режиме геометрия блоков — в системе координат iframe; события
        // родительского документа (панели, подложка) здесь игнорируем.
        if (IFRAME_MODE && (!CW || e.view !== CW)) return;
        if (dragState.active || textEdit.active || zero.active) return;
        if (addBtnHover) return;
        if (e.target && e.target.closest && e.target.closest(
            '#pe-topbar,#pe-left,#pe-right,#pe-left-rail,#pe-right-rail,#pe-canvas-hint,#pe-right-backdrop,' +
            '.pe-toolbar,.pe-addbtn,#pe-addbtn-bridge,.pe-modal-overlay,.pe-zero-layer')) return;
        const idx = blockIndexFromPoint(e.clientX, e.clientY);
        if (idx !== hoverIdx) {
            hoverIdx = idx;
            updateOverlayPositions();
        }
    }

    function updateOverlayPositions() {
        if (dragState.active || textEdit.active || zero.active) return;
        const btnIdx = addBtnHover ? (addBtn.dataset.target != null ? +addBtn.dataset.target : -1) : -1;
        const showIdx = selectedIdx >= 0 ? selectedIdx : (hoverIdx >= 0 ? hoverIdx : (addBtnHover ? btnIdx : -1));
        if (showIdx < 0) { highlight.style.display = 'none'; addBtn.style.display = 'none'; addBtnBridge.style.display = 'none'; return; }
        const r = entryRect(showIdx);
        if (!r || r.bottom - r.top < 2) { highlight.style.display = 'none'; return; }
        highlight.style.display = 'block';
        highlight.style.top = r.top + 'px';
        highlight.style.left = r.left + 'px';
        highlight.style.width = (r.right - r.left) + 'px';
        highlight.style.height = (r.bottom - r.top) + 'px';
        highlight.className = 'pe-highlight' + (selectedIdx === showIdx ? ' pe-selected' : '');

        if (selectedIdx === showIdx) {
            renderToolbar(selectedIdx);
            toolbar.style.display = 'flex';
        } else {
            toolbar.style.display = 'none';
        }

        const showBtnIdx = hoverIdx >= 0 ? hoverIdx : (addBtnHover ? btnIdx : -1);
        if (showBtnIdx >= 0 && !hasLegacyBlockAt(showBtnIdx)) {
            addBtn.dataset.target = showBtnIdx;
            addBtnBridge.dataset.target = showBtnIdx;
            addBtn.style.display = 'flex';
            addBtn.style.left = ((r.left + r.right) / 2) + 'px';
            addBtn.style.top = r.bottom + 'px';
            addBtnBridge.style.display = 'block';
            addBtnBridge.style.left = r.left + 'px';
            addBtnBridge.style.top = (r.bottom - 6) + 'px';
            addBtnBridge.style.width = (r.right - r.left) + 'px';
            addBtnBridge.style.height = '30px';
        } else {
            addBtn.style.display = 'none';
            addBtnBridge.style.display = 'none';
        }
        positionBadges();
    }

    function hasLegacyBlockAt(idx) { return !!(blocks[idx] && blocks[idx].legacy); }

    function renderToolbar(idx) {
        const b = blocks[idx];
        if (!b) return;
        toolbar.innerHTML = '<span class="pe-tb-name">' + esc(blockLabel(b)) + '</span>' +
            '<button type="button" data-act="drag" class="pe-tb-drag" title="Зажмите и перетащите, чтобы переместить блок">⠿</button>' +
            '<button type="button" data-act="up" title="Переместить выше">↑</button>' +
            '<button type="button" data-act="down" title="Переместить ниже">↓</button>' +
            (b.legacy ? '' : '<button type="button" data-act="dup" title="Дублировать блок">⧉</button>') +
            (!b.legacy ? '<button type="button" data-act="save" title="Сохранить в «Мои блоки» — потом можно вставить на любой странице">💾</button>' : '') +
            (!b.legacy && b.type === 'zero' ? '<button type="button" data-act="zero" title="Открыть Zero-редактор">🎯</button>' : '') +
            (!b.legacy ? '<button type="button" data-act="hide" title="Скрыть/показать блок на странице">' + (b.hidden ? '👁' : '🚫') + '</button>' : '') +
            '<button type="button" data-act="del" class="pe-tb-del" title="Удалить блок">🗑</button>';
        toolbar.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const act = btn.dataset.act;
                if (act === 'drag') return;
                if (act === 'up') moveBy(idx, -1);
                if (act === 'down') moveBy(idx, 1);
                if (act === 'dup') duplicate(idx);
                if (act === 'del') removeBlock(idx);
                if (act === 'hide') toggleHidden(idx);
                if (act === 'zero') openZero(idx);
                if (act === 'save') saveToMyBlocks(idx);
            });
        });
        toolbar.querySelector('[data-act="drag"]').addEventListener('mousedown', e => startDrag(idx, e));
    }

    function select(idx) {
        if (selectedIdx !== idx) {
            selectedIdx = idx;
            typoElement = null;
            renderStructure();
            updateOverlayPositions();
            if (idx >= 0) openRightPanel(idx);
            else closeRightPanel();
        } else if (idx >= 0) {
            openRightPanel(idx);
        }
    }

    /* ==================== Перетаскивание блоков (badge-мини-драг) ==================== */
    const dragState = {active: false, from: -1, target: null};

    function startDrag(idx, e) {
        e.preventDefault();
        dragState.active = true;
        dragState.from = idx;
        const badge = badges.get(idx);
        if (badge) badge.classList.add('pe-badge-dragging');
        const entry = entryAt(idx);
        if (entry) entry.elements.forEach(elm => { elm.style.opacity = '0.45'; });
        document.body.style.userSelect = 'none';
        setFramePointerEvents('none');   // mousemove над контентом — в родителя
        document.addEventListener('mousemove', onDragMove);
        document.addEventListener('mouseup', onDragEnd);
    }

    // visualClientY (визуальная система ОКНА редактора) → «документно-визуальная»
    // система холста. События из iframe вызывающий код переводит через
    // frameToParentY — здесь работаем уже в единой системе окна.
    function dropIndexForY(visualClientY) {
        if (!canvasArea) return {index: 0, before: true};
        const ar = canvasArea.getBoundingClientRect();
        const y = visualClientY - ar.top + canvasArea.scrollTop;
        for (const entry of blockMap) {
            const r = entryRect(entry.i);
            if (!r) continue;
            if (y < r.bottom) return {index: entry.i, before: y < (r.top + r.bottom) / 2};
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
        const y = target.before ? r.top : r.bottom;
        const cr = contentEl ? contentRect(contentEl) : {left: 0, width: window.innerWidth};
        const ar = canvasArea ? canvasArea.getBoundingClientRect() : {left: 0};
        dropLine.style.display = 'block';
        dropLine.style.top = y + 'px';
        dropLine.style.left = (cr.left - ar.left) + 'px';
        dropLine.style.width = cr.width + 'px';
        dragState.target = target;
    }

    function onDragEnd() {
        document.removeEventListener('mousemove', onDragMove);
        document.removeEventListener('mouseup', onDragEnd);
        document.body.style.userSelect = '';
        setFramePointerEvents('');
        dropLine.style.display = 'none';
        const badge = badges.get(dragState.from);
        if (badge) badge.classList.remove('pe-badge-dragging');
        const entry = entryAt(dragState.from);
        if (entry) entry.elements.forEach(elm => { elm.style.opacity = ''; });
        if (dragState.active && dragState.target) {
            const {index, before} = dragState.target;
            moveBlockTo(dragState.from, index, before);
        }
        dragState.active = false;
        dragState.from = -1;
        dragState.target = null;
        positionBadges();
    }

    /* ==================== Структурные операции ==================== */
    function moveBlockTo(from, targetIndex, before) {
        if (from === targetIndex) return;
        const [moved] = blocks.splice(from, 1);
        let pos;
        if (from < targetIndex) pos = before ? targetIndex - 1 : targetIndex;
        else pos = before ? targetIndex : targetIndex + 1;
        blocks.splice(pos, 0, moved);
        afterStructuralChange(pos);
    }
    function moveBy(idx, dir) {
        const j = idx + dir;
        if (j < 0 || j >= blocks.length) return;
        const [moved] = blocks.splice(idx, 1);
        blocks.splice(j, 0, moved);
        afterStructuralChange(j);
    }
    function duplicate(idx) {
        const copy = JSON.parse(JSON.stringify(blocks[idx]));
        delete copy.legacy;
        blocks.splice(idx + 1, 0, copy);
        afterStructuralChange(idx + 1);
    }
    function removeBlock(idx) {
        const b = blocks[idx];
        if (!b) return;
        if (textEdit.active && textEdit.idx === idx) finishTextEdit(true);
        const name = b.legacy ? 'перенесённый дизайн страницы' : 'блок «' + blockLabel(b) + '»';
        if (!confirm('Удалить ' + name + '?')) return;
        blocks.splice(idx, 1);
        selectedIdx = -1;
        afterStructuralChange();
    }
    function toggleHidden(idx) {
        const b = blocks[idx];
        if (!b || b.legacy) return;
        b.hidden = !b.hidden;
        markDirty();
        recordHistory();
        renderStructure();
        refreshPage();
    }
    function insertIndexAfter(idx) {
        return idx === undefined || idx === null || idx === '' ? 0 : +idx + 1;
    }
    function insertBlock(block, at) {
        const pos = Math.max(0, Math.min(at == null ? blocks.length : at, blocks.length));
        blocks.splice(pos, 0, block);
        afterStructuralChange(pos);
    }

    function afterStructuralChange(selectIdx) {
        markDirty();
        recordHistory();
        if (hasLegacy) {
            // Перенесённый дизайн содержит скрипты — корректно рендерится только целиком
            saveDraft();
            location.reload();
            return;
        }
        selectedIdx = (selectIdx === undefined) ? -1 : selectIdx;
        renderStructure();
        refreshPage();
        if (selectedIdx >= 0) openRightPanel(selectedIdx);
        else closeRightPanel();
    }

    /* ==================== Обновление холста (debounce 400 мс) ==================== */
    let refreshTimer = null;
    let refreshSeq = 0;
    function scheduleRefresh() {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refreshPage, 400);
    }

    async function refreshPage() {
        if (hasLegacy) { updateOverlayPositions(); return; }
        if (!contentEl) return;
        // Защита от гонки: при быстрой серии вставок/правок параллельно летит
        // несколько запросов — DOM обновляет только ответ ПОСЛЕДНЕГО вызова.
        const seq = ++refreshSeq;
        try {
            const result = await apiPost('/preview-marked', {blocks}).then(r => r.json());
            if (seq !== refreshSeq) return;
            if (!result.success) { setSaveState('error'); toast('Ошибка рендера: ' + (result.error || ''), 'error'); return; }
            replaceContentFromHtml(result.content_html);
        } catch (e) {
            if (seq !== refreshSeq) return;
            setSaveState('error');
            toast('Ошибка сети: ' + e.message, 'error');
        }
    }

    // Разбор ответа сервера по маркерам и ТОЧЕЧНАЯ замена блоков на холсте.
    // Если границы блока не находятся (число/порядок маркеров изменился) —
    // перерендер всего контент-контейнера.
    function replaceContentFromHtml(html) {
        const segs = parseMarkedSegments(html);
        const markers = collectMarkers();
        let ok = segs.length > 0;
        for (const seg of segs) {
            if (!markers[seg.i]) { ok = false; break; }
        }
        if (!ok) {
            contentEl.innerHTML = html;
            reviveScripts(contentEl);
            buildMap();
            updateOverlayPositions();
            return;
        }
        for (const seg of segs) {
            const m = markers[seg.i];
            const tpl = cel('template');
            tpl.innerHTML = seg.html;
            const nodes = [...tpl.content.childNodes];
            m.nodes.forEach(n => n.remove());
            nodes.forEach(n => m.end.parentNode.insertBefore(n, m.end));
        }
        reviveScripts(contentEl);
        buildMap();
        updateOverlayPositions();
    }

    function parseMarkedSegments(html) {
        const segs = [];
        const re = /<!--blk:(\d+)-->([\s\S]*?)<!--\/blk-->/g;
        let m;
        while ((m = re.exec(html)) !== null) segs.push({i: +m[1], html: m[2]});
        return segs;
    }

    function collectMarkers() {
        const map = {};
        let cur = null;
        [...contentEl.childNodes].forEach(n => {
            if (n.nodeType === Node.COMMENT_NODE) {
                const m = n.nodeValue.match(/^\s*blk:(\d+)\s*$/);
                if (m) { cur = {start: n, end: null, nodes: []}; map[+m[1]] = cur; return; }
                if (n.nodeValue.trim() === '/blk' && cur) { cur.end = n; cur = null; return; }
            }
            if (cur && n.nodeType === Node.ELEMENT_NODE) cur.nodes.push(n);
        });
        return map;
    }

    function reviveScripts(root) {
        [...root.querySelectorAll('script')].forEach(old => {
            const s = cel('script');
            if (old.src) s.src = old.src;
            else s.textContent = old.textContent;
            old.replaceWith(s);
        });
    }

    /* ==================== Библиотека блоков (левая панель) ==================== */
    function renderLibrary() {
        const lib = $('pe-lib');
        if (!lib) return;
        lib.innerHTML = '';
        const q = ($('pe-lib-search').value || '').trim().toLowerCase();
        // «Мои блоки» — всегда первой секцией (CN-T9.1)
        const visibleMy = myBlocks.filter(t => !q || (t.name + ' ' + (t.category || '')).toLowerCase().includes(q));
        if (visibleMy.length) {
            const myDet = el('details', 'pe-lib-cat pe-lib-my');
            myDet.open = true;
            myDet.innerHTML = '<summary>⭐ Мои блоки<span class="pe-lib-count">' + visibleMy.length + '</span></summary>';
            visibleMy.forEach(t => myDet.appendChild(renderMyBlockCard(t)));
            lib.appendChild(myDet);
        }
        CATEGORIES.forEach(cat => {
            const visible = cat.types.filter(t => {
                const cfg = BLOCK_TYPES[t];
                if (!cfg || cfg.hidden) return false;
                if (!q) return true;
                return (cfg.label + ' ' + cfg.desc + ' ' + cat.name).toLowerCase().includes(q);
            });
            if (!visible.length) return;
            const det = el('details', 'pe-lib-cat');
            if (q) det.open = true;
            det.innerHTML = '<summary>' + esc(cat.name) + '<span class="pe-lib-count">' + visible.length + '</span></summary>';
            visible.forEach(type => {
                det.appendChild(renderBlockCard(type));
            });
            lib.appendChild(det);
        });
        if (!lib.children.length) {
            lib.innerHTML = '<div class="pe-lib-empty">Ничего не найдено. Попробуйте другой запрос.</div>';
        }
    }

    function renderBlockCard(type) {
        const cfg = BLOCK_TYPES[type];
        const card = el('div', 'pe-lib-card');
        card.draggable = true;
        card.title = cfg.desc + ' — нажмите, чтобы добавить после выбранного блока; или перетащите на холст';
        card.innerHTML =
            '<div class="pe-lib-mock">' + (cfg.mock || '<div class="gm-bar"></div>') + '</div>' +
            '<div class="pe-lib-name">' + esc(cfg.label) + '</div>';
        card.addEventListener('click', () => addFromLibrary(type, 0));
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('text/pe-block', JSON.stringify({type, preset: 0}));
            e.dataTransfer.effectAllowed = 'copy';
        });
        const presets = el('div', 'pe-lib-presets');
        (cfg.presets || []).forEach((p, k) => {
            const btn = el('button', 'pe-lib-preset', {type: 'button', title: 'Добавить: ' + esc(p.name)});
            btn.textContent = p.name;
            btn.addEventListener('click', e => { e.stopPropagation(); addFromLibrary(type, k); });
            btn.draggable = true;
            btn.addEventListener('dragstart', e => {
                e.dataTransfer.setData('text/pe-block', JSON.stringify({type, preset: k}));
                e.dataTransfer.effectAllowed = 'copy';
            });
            presets.appendChild(btn);
        });
        card.appendChild(presets);
        return card;
    }

    function addFromLibrary(type, presetIdx) {
        const cfg = BLOCK_TYPES[type];
        if (!cfg) return;
        const preset = (cfg.presets || [])[presetIdx];
        const block = preset ? preset.make() : cfg.make();
        let at;
        if (pendingInsert != null) at = pendingInsert;
        else if (selectedIdx >= 0) at = selectedIdx + 1;
        else at = blocks.length;
        disarmInsert();
        insertBlock(block, at);
    }

    /* ==================== «Мои блоки» (CN-T9.1): сохранение и вставка ==================== */
    let myBlocks = [];
    async function loadMyBlocks() {
        try {
            const d = await apiGet('/block-templates');
            if (d && d.success && Array.isArray(d.templates)) myBlocks = d.templates;
        } catch (e) { /* секция просто не покажется */ }
    }
    /* Пресеты секций дизайна из /admin/api/section-presets: заполняем
       BLOCK_TYPES.section.presets и перерендериваем библиотеку. При ошибке
       сети или отсутствии файла — пустой список, редактор не ломается. */
    async function loadSectionPresets() {
        try {
            const d = await apiGet('/section-presets');
            if (!d || !d.success || !Array.isArray(d.presets)) return;
            BLOCK_TYPES.section.presets = d.presets.map(p => ({
                name: p.name || 'Секция',
                make: () => ({type: 'section', name: p.name || 'Секция', html: p.html || ''}),
            }));
        } catch (e) { /* пресетов не будет — останется make() по умолчанию */ }
    }
    async function saveToMyBlocks(idx) {
        const b = blocks[idx];
        if (!b) return;
        const name = prompt('Название блока в «Мои блоки»:', (b.name || blockLabel(b)).replace(/^[^\s]+\s/, '').slice(0, 100));
        if (name == null) return;
        const trimmed = name.trim();
        if (!trimmed) { toast('Название не может быть пустым', 'error'); return; }
        try {
            const resp = await apiPost('/block-templates', {name: trimmed, category: b.type || '', block: b});
            const r = await resp.json();
            if (r.success) {
                toast('Блок сохранён в «Мои блоки» ✓');
                await loadMyBlocks();
                renderLibrary();
            } else {
                toast('Ошибка: ' + (r.error || 'не удалось сохранить'), 'error');
            }
        } catch (e) { toast('Ошибка сети: ' + e.message, 'error'); }
    }
    function addSavedBlock(t) {
        if (!t || typeof t.block !== 'object' || !t.block) { toast('В сохранённом блоке нет данных', 'error'); return; }
        let at;
        if (pendingInsert != null) at = pendingInsert;
        else if (selectedIdx >= 0) at = selectedIdx + 1;
        else at = blocks.length;
        disarmInsert();
        insertBlock(JSON.parse(JSON.stringify(t.block)), at);
    }
    function renderMyBlockCard(t) {
        const card = el('div', 'pe-lib-card pe-lib-my-card');
        card.draggable = true;
        card.title = '«' + (t.name || 'Блок') + '» — нажмите, чтобы добавить после выбранного блока; или перетащите на холст';
        card.innerHTML =
            '<div class="pe-lib-mock"><div class="gm-bar"></div><div class="gm-bar gm-bar-s"></div></div>' +
            '<div class="pe-lib-name">⭐ ' + esc(t.name || 'Блок') + '</div>';
        card.addEventListener('click', () => addSavedBlock(t));
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('text/pe-block', JSON.stringify({saved: true, block: t.block}));
            e.dataTransfer.effectAllowed = 'copy';
        });
        return card;
    }

    // Кнопка «+ Добавить блок»: не модалка, а подсветка левой панели + фокус в поиск.
    function armInsert(targetIdx) {
        pendingInsert = insertIndexAfter(targetIdx);
        setLeftCollapsed(false);
        document.body.classList.add('pe-lib-armed');
        const search = $('pe-lib-search');
        if (search) { search.focus(); search.select(); }
        toast('Выберите блок в библиотеке слева — он будет добавлен на это место');
    }
    function disarmInsert() {
        pendingInsert = null;
        document.body.classList.remove('pe-lib-armed');
    }
    addBtn.addEventListener('click', () => armInsert(addBtn.dataset.target));

    // Drag-and-drop карточек библиотеки на холст: индикатор-линия между блоками
    function bindLibraryDnD() {
        if (!contentEl) return;
        contentEl.addEventListener('dragover', e => {
            if (![...(e.dataTransfer.types || [])].includes('text/pe-block')) return;
            e.preventDefault();
            // Координаты события из iframe — приводим к системе окна редактора.
            const target = dropIndexForY(frameToParentY(e.clientY));
            if (!target) { dropLine.style.display = 'none'; return; }
            const r = entryRect(target.index);
            const cr = contentRect(contentEl);
            const ar = canvasArea ? canvasArea.getBoundingClientRect() : {left: 0, top: 0};
            const y = r ? (target.before ? r.top : r.bottom)
                        : (cr.top - ar.top + (canvasArea ? canvasArea.scrollTop : 0) + cr.height / 2);
            dropLine.style.display = 'block';
            dropLine.style.top = y + 'px';
            dropLine.style.left = (cr.left - ar.left) + 'px';
            dropLine.style.width = cr.width + 'px';
            contentEl.dataset.dropAt = JSON.stringify(target);
        });
        contentEl.addEventListener('dragleave', e => {
            if (e.target === contentEl) { dropLine.style.display = 'none'; delete contentEl.dataset.dropAt; }
        });
        contentEl.addEventListener('drop', e => {
            const raw = e.dataTransfer.getData('text/pe-block');
            if (!raw) return;
            e.preventDefault();
            dropLine.style.display = 'none';
            let target = null;
            try { target = JSON.parse(contentEl.dataset.dropAt || 'null'); } catch (err) { target = null; }
            delete contentEl.dataset.dropAt;
            let block;
            try {
                const spec = JSON.parse(raw);
                if (spec.saved) {
                    block = JSON.parse(JSON.stringify(spec.block || {}));
                } else {
                    const cfg = BLOCK_TYPES[spec.type];
                    if (!cfg) return;
                    const preset = (cfg.presets || [])[spec.preset || 0];
                    block = preset ? preset.make() : cfg.make();
                }
            } catch (err) { return; }
            let at;
            if (target) at = target.before ? target.index : target.index + 1;
            else at = blocks.length;
            if (target && target.index < blocks.length) {
                // dropIndexForY оперирует индексами блоков на странице (== индексам blocks)
                at = target.before ? target.index : target.index + 1;
            }
            insertBlock(block, at);
        });
    }

    /* ==================== Структура страницы (левая панель, низ) ==================== */
    function renderStructure() {
        const list = $('pe-structure-list');
        if (!list) return;
        list.innerHTML = '';
        if (!blocks.length) {
            list.innerHTML = '<div class="pe-lib-empty">На странице пока нет блоков.</div>';
            return;
        }
        blocks.forEach((b, idx) => {
            const cfg = b.legacy || b.type === 'section' ? null : BLOCK_TYPES[b.type];
            const item = el('div', 'pe-str-item' + (idx === selectedIdx ? ' pe-str-active' : '') + (b.hidden ? ' pe-str-hidden' : ''));
            item.draggable = !b.legacy;
            item.dataset.idx = idx;
            item.innerHTML =
                '<span class="pe-str-grip" title="Перетащите для сортировки">⠿</span>' +
                '<span class="pe-str-icon">' + (b.legacy ? '🧩' : b.type === 'section' ? '🧩' : (cfg ? cfg.icon : '▦')) + '</span>' +
                '<span class="pe-str-name" title="Двойной клик — переименовать">' + esc(blockLabel(b)) + '</span>' +
                '<span class="pe-str-actions">' +
                '<button type="button" data-act="dup" title="Дублировать блок">⧉</button>' +
                '<button type="button" data-act="hide" title="Скрыть/показать блок на странице">' + (b.hidden ? '👁' : '🚫') + '</button>' +
                '<button type="button" data-act="del" class="pe-str-del" title="Удалить блок">🗑</button>' +
                '</span>';
            item.addEventListener('click', e => {
                const act = e.target && e.target.dataset ? e.target.dataset.act : null;
                if (act === 'dup') { duplicate(idx); return; }
                if (act === 'del') { removeBlock(idx); return; }
                if (act === 'hide') { toggleHidden(idx); return; }
                select(idx);
                const entry = entryAt(idx);
                if (entry && entry.elements.length && canvasArea) {
                    const ar = canvasArea.getBoundingClientRect();
                    const top = contentRect(entry.elements[0]).top - ar.top + canvasArea.scrollTop - 70;
                    canvasArea.scrollTo({top, behavior: 'smooth'});
                }
            });
            item.addEventListener('dblclick', e => {
                if (e.target.closest('.pe-str-actions')) return;
                startRename(item, b);
            });
            // Drag-сортировка (HTML5 DnD)
            item.addEventListener('dragstart', e => {
                e.dataTransfer.setData('text/pe-struct', String(idx));
                e.dataTransfer.effectAllowed = 'move';
                item.classList.add('pe-str-dragging');
            });
            item.addEventListener('dragend', () => {
                item.classList.remove('pe-str-dragging');
                list.querySelectorAll('.pe-str-dropbefore,.pe-str-dropafter').forEach(x =>
                    x.classList.remove('pe-str-dropbefore', 'pe-str-dropafter'));
            });
            item.addEventListener('dragover', e => {
                if (![...(e.dataTransfer.types || [])].includes('text/pe-struct')) return;
                e.preventDefault();
                const rect = item.getBoundingClientRect();
                const before = e.clientY < rect.top + rect.height / 2;
                list.querySelectorAll('.pe-str-dropbefore,.pe-str-dropafter').forEach(x =>
                    x.classList.remove('pe-str-dropbefore', 'pe-str-dropafter'));
                item.classList.add(before ? 'pe-str-dropbefore' : 'pe-str-dropafter');
            });
            item.addEventListener('drop', e => {
                const raw = e.dataTransfer.getData('text/pe-struct');
                if (raw === '') return;
                e.preventDefault();
                e.stopPropagation();
                const from = +raw;
                const rect = item.getBoundingClientRect();
                const before = e.clientY < rect.top + rect.height / 2;
                const targetIndex = idx;
                if (from === targetIndex) return;
                moveBlockTo(from, targetIndex, before);
            });
            list.appendChild(item);
        });
    }

    function startRename(item, b) {
        const nameEl = item.querySelector('.pe-str-name');
        if (!nameEl) return;
        const input = el('input', 'pe-str-rename', {type: 'text'});
        input.value = b.name || '';
        input.placeholder = 'Название блока (только для списка)';
        nameEl.replaceWith(input);
        input.focus();
        input.select();
        const done = () => {
            const v = input.value.trim();
            if (v) b.name = v.slice(0, 60);
            else delete b.name;
            markDirty();
            recordHistoryDebounced();
            renderStructure();
            renderBadges();
        };
        input.addEventListener('blur', done);
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') input.blur();
            if (e.key === 'Escape') { input.value = b.name || ''; input.blur(); }
        });
    }

    /* ==================== Правая панель: каркас и поля ==================== */
    const TABS = [
        ['content', 'Контент'],
        ['bg', 'Фон'],
        ['layout', 'Расположение'],
        ['typo', 'Типографика'],
        ['button', 'Кнопка'],
        ['fx', 'Эффекты'],
        ['extra', 'Дополнительно'],
    ];
    const BUTTON_TAB_TYPES = ['button', 'banner', 'timer', 'form'];
    const TYPO_TAB_TYPES = ['heading', 'text', 'text_image', 'products', 'reviews', 'features', 'form', 'faq', 'banner', 'timer', 'gallery', 'text_columns', 'contacts', 'table', 'image'];

    function openRightPanel(idx) {
        const b = blocks[idx];
        if (!b) { closeRightPanel(); return; }
        $('pe-right-title').textContent = blockLabel(b);
        renderRightTabs(b);
        renderRightBody(b, idx);
    }
    function closeRightPanel() {
        $('pe-right-title').textContent = 'Настройки блока';
        $('pe-right-tabs').innerHTML = '';
        $('pe-right-body').innerHTML = '<div class="pe-right-hint">Выберите блок на холсте — здесь появятся его настройки.</div>';
    }
    function renderRightTabs(b) {
        const type = b.legacy ? 'html' : (b.type || 'text');
        const tabs = TABS.filter(([id]) => {
            if (id === 'content') return true;
            if (id === 'button') return BUTTON_TAB_TYPES.includes(type);
            if (id === 'typo') return TYPO_TAB_TYPES.includes(type);
            return true;
        });
        if (!tabs.some(([id]) => id === activeTab)) activeTab = 'content';
        const box = $('pe-right-tabs');
        box.innerHTML = '';
        tabs.forEach(([id, name]) => {
            const btn = el('button', 'pe-tab' + (id === activeTab ? ' pe-tab-active' : ''), {type: 'button'});
            btn.textContent = name;
            btn.addEventListener('click', () => { activeTab = id; openRightPanel(selectedIdx); });
            box.appendChild(btn);
        });
    }
    function renderRightBody(b, idx) {
        const body = $('pe-right-body');
        body.innerHTML = '';
        const type = b.legacy ? 'html' : (b.type || 'text');
        const set = (k, v) => {
            b[k] = v;
            markDirty();
            recordHistoryDebounced();
            scheduleRefresh();
        };
        if (activeTab === 'content') renderContentTab(b, idx, type, set, body);
        else if (activeTab === 'bg') renderBgTab(b, set, body);
        else if (activeTab === 'layout') renderLayoutTab(b, set, body);
        else if (activeTab === 'typo') renderTypoTab(b, set, body);
        else if (activeTab === 'button') renderButtonTab(b, idx, type, set, body);
        else if (activeTab === 'fx') renderFxTab(b, set, body);
        else if (activeTab === 'extra') renderExtraTab(b, set, body);
    }

    /* -------- Билдеры полей -------- */
    function field(labelText, inputEl, help) {
        const f = el('div', 'pe-field');
        const l = document.createElement('label');
        l.textContent = labelText;
        f.appendChild(l);
        f.appendChild(inputEl);
        if (help) { const d = el('div', 'pe-help'); d.textContent = help; f.appendChild(d); }
        return f;
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
    function richTextInput(value, onchange, rows) {
        const wrap = el('div', 'pe-rich-editor');
        wrap.style.cssText = 'border:1px solid #d0d0d0;border-radius:4px;overflow:hidden;';
        const toolbar = el('div', 'pe-rich-toolbar');
        toolbar.style.cssText = 'display:flex;gap:4px;padding:4px;border-bottom:1px solid #e0e0e0;background:#f7f7f7;';
        const ed = el('div', 'pe-rich-area', {contenteditable: 'true'});
        ed.style.cssText = 'min-height:' + (rows || 4) + 'rem;padding:.5rem;outline:none;background:#fff;';
        let html = value || '';
        if (html && !/^\s*</.test(html)) {
            html = '<p>' + String(html).split(/\n\s*\n/).filter(Boolean).map(p => p.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')).join('</p><p>') + '</p>';
        }
        ed.innerHTML = html || '<p><br></p>';
        const btns = [
            ['bold','B'],['italic','I'],['underline','U'],
            ['insertUnorderedList','•'],['insertOrderedList','1.']
        ];
        btns.forEach(([cmd, label]) => {
            const btn = el('button','pe-rich-btn',{type:'button'});
            btn.textContent = label;
            btn.style.cssText = 'min-width:28px;padding:2px 6px;border:1px solid #ccc;background:#fff;border-radius:3px;cursor:pointer;';
            btn.addEventListener('click', (e) => { e.preventDefault(); ed.focus(); document.execCommand(cmd, false, null); });
            toolbar.appendChild(btn);
        });
        const linkBtn = el('button','pe-rich-btn',{type:'button'});
        linkBtn.textContent = 'Ссылка';
        linkBtn.style.cssText = 'padding:2px 6px;border:1px solid #ccc;background:#fff;border-radius:3px;cursor:pointer;';
        linkBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const url = prompt('URL (http://, https://, /, #, mailto:, tel:)');
            if (url) { ed.focus(); document.execCommand('createLink', false, url); }
        });
        toolbar.appendChild(linkBtn);
        ed.addEventListener('input', () => onchange(ed.innerHTML));
        wrap.appendChild(toolbar);
        wrap.appendChild(ed);
        return wrap;
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
    function colorInput(value, onchange) {
        const c = document.createElement('input');
        c.type = 'color';
        c.value = normalizeHex(value) || '#b49d84';
        c.addEventListener('input', () => onchange(c.value));
        return c;
    }
    function normalizeHex(v) {
        return /^#[0-9a-fA-F]{6}$/.test(v || '') ? v : (/^#[0-9a-fA-F]{3}$/.test(v || '') ? '#' + v.slice(1).split('').map(c => c + c).join('') : '');
    }
    function colorField(labelText, value, onchange) {
        const wrap = el('div', 'pe-colorrow');
        const c = colorInput(value, onchange);
        const t = textInput(value, onchange, '#b49d84');
        t.className = 'pe-hex';
        t.addEventListener('input', () => {
            if (normalizeHex(t.value)) c.value = normalizeHex(t.value);
        });
        c.addEventListener('input', () => { t.value = c.value; });
        wrap.appendChild(c);
        wrap.appendChild(t);
        return field(labelText, wrap);
    }
    function numberInput(value, onchange, min, max) {
        const n = document.createElement('input');
        n.type = 'number';
        n.min = min; n.max = max;
        n.value = value;
        n.addEventListener('input', () => onchange(parseInt(n.value || min, 10)));
        return n;
    }
    function stepper(value, onchange, opts) {
        const o = Object.assign({min: 0, max: 200, step: 10, suffix: ' px'}, opts || {});
        const wrap = el('div', 'pe-stepper');
        const minus = el('button', '', {type: 'button', title: 'Уменьшить'});
        minus.textContent = '−';
        const plus = el('button', '', {type: 'button', title: 'Увеличить'});
        plus.textContent = '+';
        const val = el('span', 'pe-stepper-val');
        let cur = value == null || isNaN(+value) ? 0 : +value;
        const render = () => { val.textContent = cur + o.suffix; };
        minus.addEventListener('click', () => { cur = Math.max(o.min, cur - o.step); render(); onchange(cur); });
        plus.addEventListener('click', () => { cur = Math.min(o.max, cur + o.step); render(); onchange(cur); });
        render();
        wrap.append(minus, val, plus);
        return wrap;
    }
    function toggleInput(value, onchange, labelText) {
        const btn = el('button', 'pe-toggle' + (value ? ' pe-toggle-on' : ''), {type: 'button'});
        btn.innerHTML = '<span class="pe-toggle-dot"></span>' + esc(labelText || '');
        btn.addEventListener('click', () => {
            const on = !btn.classList.contains('pe-toggle-on');
            btn.classList.toggle('pe-toggle-on', on);
            onchange(on);
        });
        return btn;
    }
    function rangeInput(value, onchange, opts) {
        const o = Object.assign({min: 0, max: 100, step: 1, suffix: ''}, opts || {});
        const wrap = el('div', 'pe-range');
        const r = document.createElement('input');
        r.type = 'range';
        r.min = o.min; r.max = o.max; r.step = o.step;
        r.value = value == null || value === '' ? o.min : value;
        const out = el('span', 'pe-range-val');
        const render = () => { out.textContent = r.value + o.suffix; };
        r.addEventListener('input', () => { render(); onchange(+r.value); });
        render();
        wrap.append(r, out);
        return wrap;
    }

    /* Визуальный редактор списка пунктов: строки с полями, удаление, «+ Добавить» */
    function itemsEditor(items, defs, onchange) {
        items = Array.isArray(items) ? items : [];
        const wrap = el('div', 'pe-items');
        function render() {
            wrap.innerHTML = '';
            items.forEach((item, k) => {
                const row = el('div', 'pe-item');
                defs.forEach(d => {
                    const f = el('div', 'pe-item-field');
                    const l = document.createElement('label');
                    l.textContent = d.label;
                    let inp;
                    if (d.type === 'textarea') { inp = document.createElement('textarea'); inp.rows = 2; }
                    else if (d.type === 'select') {
                        inp = document.createElement('select');
                        (d.options || []).forEach(([val, label]) => {
                            const o = document.createElement('option');
                            o.value = val; o.textContent = label;
                            inp.appendChild(o);
                        });
                    } else { inp = document.createElement('input'); inp.type = 'text'; }
                    inp.value = item[d.key] || (d.def != null ? d.def : '');
                    if (d.placeholder) inp.placeholder = d.placeholder;
                    const commit = () => { item[d.key] = inp.value; onchange(items); };
                    inp.addEventListener('input', commit);
                    inp.addEventListener('change', commit);
                    f.appendChild(l);
                    f.appendChild(inp);
                    row.appendChild(f);
                });
                const del = el('button', 'pe-item-del', {type: 'button', title: 'Удалить пункт'});
                del.textContent = '🗑';
                del.addEventListener('click', () => { items.splice(k, 1); onchange(items); render(); });
                row.appendChild(del);
                wrap.appendChild(row);
            });
            const add = el('button', 'pe-item-add', {type: 'button'});
            add.textContent = '+ Добавить пункт';
            add.addEventListener('click', () => {
                const ni = {};
                defs.forEach(d => { ni[d.key] = d.def || ''; });
                items.push(ni);
                onchange(items);
                render();
            });
            wrap.appendChild(add);
        }
        render();
        return wrap;
    }

    // Поле картинки: медиатека / загрузка / URL + превью
    function imageField(labelText, block, key, set) {
        const wrap = el('div', 'pe-field');
        const l = document.createElement('label');
        l.textContent = labelText;
        wrap.appendChild(l);
        const row = el('div', 'pe-imgrow');
        const pick = el('button', 'pe-btn-mini', {type: 'button', title: 'Выбрать из медиатеки'});
        pick.textContent = '🖼 Медиатека';
        pick.addEventListener('click', async () => {
            const url = await openMediaPicker(block[key]);
            if (url != null) { set(key, url); refreshImagePreview(); }
        });
        const up = el('button', 'pe-btn-mini', {type: 'button', title: 'Загрузить с компьютера'});
        up.textContent = '⬆ Загрузить';
        const file = document.createElement('input');
        file.type = 'file';
        file.accept = 'image/*';
        file.style.display = 'none';
        up.addEventListener('click', () => file.click());
        file.addEventListener('change', async () => {
            if (!file.files.length) return;
            const url = await uploadFile(file.files[0]);
            if (url) { set(key, url); refreshImagePreview(); }
        });
        const urlInp = textInput(block[key], v => { set(key, v); refreshImagePreview(); }, '/static/uploads/… или https://…');
        row.append(pick, up, file);
        wrap.appendChild(row);
        wrap.appendChild(urlInp);
        const pv = el('div', 'pe-preview-img');
        const img = document.createElement('img');
        img.alt = '';
        img.src = block[key] || '';
        img.style.display = block[key] ? 'block' : 'none';
        pv.appendChild(img);
        wrap.appendChild(pv);
        function refreshImagePreview() {
            img.src = block[key] || '';
            img.style.display = block[key] ? 'block' : 'none';
        }
        return wrap;
    }

    async function uploadFile(file) {
        try {
            const fd = new FormData();
            fd.append('file', file);
            const resp = await fetch(API('/upload'), {method: 'POST', body: fd, headers: {'X-CSRF-Token': CSRF()}});
            const result = await resp.json();
            if (result.success) { toast('Файл загружен'); return result.url; }
            toast('Ошибка загрузки: ' + (result.error || ''), 'error');
        } catch (e) { toast('Ошибка сети: ' + e.message, 'error'); }
        return null;
    }

    /* -------- Состав секции: список элементов + добавление/удаление --------
       Список строится из b.html (DOMParser) — так панель всегда отражает
       данные блока; индекс k в списке соответствует k-му элементу живого DOM
       (секция рендерится на холсте как есть, порядок сохраняется). */
    // Листья дерева: блочные тексты, ячейки, картинки, ссылки, кнопки.
    // td/th — контейнеры: спускаемся внутрь (пустые ячейки с одним текстом
    // фолбэком попадают сами). a с img внутри (thumb-ссылки) раскрываем,
    // чтобы картинку можно было заменить.
    const SEC_LEAF = 'h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,figcaption,button,img';
    const SEC_INLINE = 'span,strong,em,b,i,small,label';
    function secWalk(n, out) {
        if (n.nodeType !== 1) return;
        const tag = n.tagName.toLowerCase();
        if (n.matches(SEC_LEAF)) {
            if (tag === 'td' || tag === 'th') {
                const before = out.length;
                for (const c of n.children) secWalk(c, out);
                if (out.length === before && (n.textContent || '').trim()) out.push(n);
                return;
            }
            out.push(n);
            return;
        }
        if (n.matches('a')) {
            if (n.querySelector('img')) { for (const c of n.children) secWalk(c, out); return; }
            out.push(n);
            return;
        }
        if (n.matches(SEC_INLINE) && !n.children.length && (n.textContent || '').trim()) {
            out.push(n);
            return;
        }
        for (const c of n.children) secWalk(c, out);
    }
    function secCollect(roots) {
        const out = [];
        [...roots].forEach(r => {
            if (r.matches && (r.matches(SEC_LEAF) || r.matches('a'))) secWalk(r, out);
            else for (const c of r.children) secWalk(c, out);
        });
        return out;
    }

    // Живые элементы секции на холсте (тот же порядок, что и в b.html)
    function sectionLiveItems(idx) {
        const entry = entryAt(idx);
        if (!entry || !entry.elements.length) return [];
        return secCollect(entry.elements);
    }

    // Записать текущий DOM секции обратно в b.html (как finishTextEdit для секций)
    function syncSectionHtml(idx, set) {
        const entry = entryAt(idx);
        if (!entry) return;
        set('html', entry.elements.map(n => n.outerHTML).join(''));
    }

    // Элементы списком из данных блока: [{tag, icon, name}]
    function sectionDataItems(b) {
        const tpl = document.createElement('template');
        tpl.innerHTML = b.html || '';
        return secCollect(tpl.content.children).map(n => {
            const tag = n.tagName.toLowerCase();
            let icon = '¶', name = (n.textContent || '').trim().replace(/\s+/g, ' ');
            if (tag === 'img') {
                icon = '🖼';
                const real = n.getAttribute('data-src') || n.getAttribute('src') || '';
                name = (n.getAttribute('alt') || n.getAttribute('title') || real.split('/').pop() || 'без картинки');
            } else if (tag === 'a') {
                icon = '🔗';
                if (!name) name = n.getAttribute('href') || 'ссылка';
            } else if (/^h[1-6]$/.test(tag)) {
                icon = 'H';
            } else if (tag === 'li') {
                icon = '•';
            } else if (tag === 'button') {
                icon = '🔘';
            }
            return {tag, icon, name: name.slice(0, 60) || '(пусто)'};
        });
    }

    // Шаблоны новых элементов для «Добавить в секцию»
    const SECTION_NEW = {
        h2: ['Заголовок', () => '<h2>Новый заголовок</h2>'],
        p: ['Текст', () => '<p>Новый текст. Замените его своим.</p>'],
        ul: ['Список', () => '<ul><li>Первый пункт списка</li><li>Второй пункт списка</li></ul>'],
        a: ['Кнопка-ссылка', () => '<a class="btn btn-default" href="#">Новая кнопка</a>'],
        img: ['Картинка', src => '<img src="' + esc(src) + '" alt="">'],
    };

    function sectionAddElement(b, kind, src) {
        const tpl = document.createElement('template');
        tpl.innerHTML = b.html || '';
        const html = SECTION_NEW[kind][1](src);
        const nodeTpl = document.createElement('template');
        nodeTpl.innerHTML = html;
        const node = nodeTpl.content.firstElementChild;
        if (!node) return b.html || '';
        const kids = [...tpl.content.children];
        const last = kids[kids.length - 1];
        if (last && last.matches('div,section,article,main')) last.appendChild(node);
        else if (last) last.after(node);          // сосед — внутрь заголовков/абзацев не вставляем
        else tpl.content.appendChild(node);
        return [...tpl.content.children].map(n => n.outerHTML).join('');
    }

    function sectionElementsPanel(b, idx, set) {
        const wrap = el('div', 'pe-sec-el');
        const head = el('button', 'pe-sec-el-head', {type: 'button', title: 'Состав секции: клик — развернуть/свернуть'});
        const list = el('div', 'pe-sec-el-list');
        const collapsed = localStorage.getItem('pe_sec_el_collapsed') === '1';
        if (collapsed) wrap.classList.add('pe-sec-el-closed');

        function refresh() {
            const items = sectionDataItems(b);
            head.innerHTML = '<span class="pe-sec-el-arrow">' + (wrap.classList.contains('pe-sec-el-closed') ? '▸' : '▾') +
                '</span> Элементы секции: ' + esc(sectionDisplayName(b)) + ' (' + items.length + ')';
            list.innerHTML = '';
            if (!items.length) {
                const empty = el('div', 'pe-sec-el-empty');
                empty.textContent = 'В секции нет редактируемых элементов — добавьте их кнопками ниже.';
                list.appendChild(empty);
                return;
            }
            items.forEach((it, k) => {
                const row = el('div', 'pe-sec-el-row', {title: it.tag + ' — клик: выделить и править на холсте'});
                const chip = el('span', 'pe-sec-el-tag');
                chip.textContent = it.tag.toUpperCase();
                const nm = el('span', 'pe-sec-el-name');
                nm.innerHTML = esc(it.icon + ' ' + it.name);
                const act = el('span', 'pe-sec-el-act');
                const del = el('button', '', {type: 'button', title: 'Удалить элемент из секции'});
                del.textContent = '🗑';
                del.addEventListener('click', e => {
                    e.stopPropagation();
                    const live = sectionLiveItems(idx)[k];
                    if (live) { live.remove(); syncSectionHtml(idx, set); updateOverlayPositions(); refresh(); }
                });
                act.appendChild(del);
                row.append(chip, nm, act);
                row.addEventListener('click', () => {
                    const live = sectionLiveItems(idx)[k];
                    if (!live) return;
                    live.scrollIntoView({block: 'center', behavior: 'smooth'});
                    if (it.tag === 'img') {
                        openMediaPicker(live.getAttribute('src') || '').then(url => {
                            if (url == null) return;
                            live.setAttribute('src', url);
                            syncSectionHtml(idx, set);
                            refresh();
                        });
                    } else {
                        select(idx);
                        startTextEdit(idx, live);
                    }
                });
                list.appendChild(row);
            });
        }

        head.addEventListener('click', () => {
            wrap.classList.toggle('pe-sec-el-closed');
            localStorage.setItem('pe_sec_el_collapsed', wrap.classList.contains('pe-sec-el-closed') ? '1' : '0');
            refresh();
        });

        // Панель добавления новых элементов
        const addBar = el('div', 'pe-sec-el-add');
        Object.keys(SECTION_NEW).forEach(kind => {
            const btn = el('button', '', {type: 'button', title: 'Добавить в конец секции: ' + SECTION_NEW[kind][0]});
            btn.textContent = '+' + SECTION_NEW[kind][0];
            btn.addEventListener('click', async () => {
                if (kind === 'img') {
                    const url = await openMediaPicker('');
                    if (url == null) return;
                    set('html', sectionAddElement(b, 'img', url));
                } else {
                    set('html', sectionAddElement(b, kind));
                }
                refresh();
            });
            addBar.appendChild(btn);
        });

        wrap.append(head, list, addBar);
        refresh();
        return wrap;
    }

    /* ==================== Вкладка «Контент» ==================== */
    function renderContentTab(b, idx, type, set, container) {
        if (b.legacy) {
            container.appendChild(el('div', 'pe-help',
                {})).textContent = 'Это содержимое перенесённой страницы. Добавляйте свои блоки выше или ниже, либо удалите этот блок и соберите страницу заново.';
            container.appendChild(field('HTML-код', textareaInput(b.html, v => set('html', v), 14)));
            return;
        }
        if (type === 'heading') {
            container.appendChild(field('Текст заголовка', textInput(b.text, v => set('text', v))));
            container.appendChild(field('Размер', selectInput([
                ['h1', 'Самый крупный (H1)'], ['h2', 'Крупный (H2)'], ['h3', 'Средний (H3)'],
            ], b.level || 'h2', v => set('level', v))));
            container.appendChild(field('Положение', selectInput([
                ['left', 'Слева'], ['center', 'По центру'], ['right', 'Справа'],
            ], b.align || 'left', v => set('align', v))));
            container.appendChild(field('Цвет текста (необязательно)', colorInput(b.text_color, v => set('text_color', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Совет: текст можно править прямо на холсте — двойной клик по заголовку.';
        } else if (type === 'text') {
            container.appendChild(field('Текст', richTextInput(b.text, v => { set('text', v); if (!b.rich) set('rich', true); }, 8)));
            container.appendChild(field('Положение', selectInput([
                ['left', 'Слева'], ['center', 'По центру'], ['right', 'Справа'],
            ], b.align || 'left', v => set('align', v))));
            container.appendChild(field('Цвет текста (необязательно)', colorInput(b.text_color, v => set('text_color', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Пустая строка — новый абзац. Двойной клик по абзацу на холсте — правка на месте.';
        } else if (type === 'image') {
            container.appendChild(imageField('Картинка', b, 'src', set));
            container.appendChild(field('Подпись для поисковиков (alt)', textInput(b.alt, v => set('alt', v))));
            container.appendChild(field('Подпись под картинкой', textInput(b.caption, v => set('caption', v))));
            container.appendChild(field('Положение', selectInput([
                ['center', 'По центру'], ['left', 'Слева'], ['right', 'Справа'],
            ], b.align || 'center', v => set('align', v))));
            container.appendChild(field('Ширина (необязательно, % или px)', textInput(b.width, v => set('width', v), 'например 60% или 400px')));
        } else if (type === 'button') {
            container.appendChild(field('Текст на кнопке', textInput(b.text, v => set('text', v))));
            container.appendChild(linkField('Куда ведёт', b.url, v => set('url', v)));
            container.appendChild(field('Положение', selectInput([
                ['left', 'Слева'], ['center', 'По центру'], ['right', 'Справа'],
            ], b.align || 'center', v => set('align', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Цвета, скругление и эффекты кнопки — во вкладке «Кнопка».';
        } else if (type === 'video') {
            container.appendChild(field('Ссылка на видео YouTube или Vimeo', textInput(b.url, v => set('url', v), 'https://youtube.com/watch?v=…')));
            container.appendChild(el('div', 'pe-help')).textContent = 'Достаточно обычной ссылки из адресной строки.';
        } else if (type === 'products') {
            container.appendChild(field('Заголовок над товарами (можно пусто)', textInput(b.title, v => set('title', v), 'Наши товары')));
            container.appendChild(field('Режим', selectInput([
                ['source', 'По источнику'], ['category', 'По категории'], ['recommendations', 'Рекомендации (популярное)'],
            ], b.mode || 'source', v => set('mode', v))));
            container.appendChild(field('Источник товаров', selectInput([
                ['all', 'Все источники'], ['wb', 'Только Wildberries'], ['ozon', 'Только Ozon'],
            ], b.source || 'all', v => set('source', v))));
            if ((b.mode || 'source') === 'category') {
                container.appendChild(field('Категория (как в каталоге)', textInput(b.category, v => set('category', v), 'namatrasniki')));
            }
            container.appendChild(field('Сколько карточек показать', numberInput(b.limit || 8, v => set('limit', v), 1, 48)));
            container.appendChild(el('div', 'pe-help')).textContent = 'Цены и скидки подтягиваются из раздела WB/Ozon админки автоматически.';
        } else if (type === 'divider') {
            container.appendChild(el('div', 'pe-help')).textContent = 'Горизонтальная линия-разделитель. Настроек нет — используйте отступы и декоративные линии в других вкладках.';
        } else if (type === 'reviews') {
            container.appendChild(field('Заголовок секции', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Отзывы', itemsEditor(b.items, [
                {key: 'name', label: 'Имя покупателя', placeholder: 'Ольга'},
                {key: 'rating', label: 'Оценка', type: 'select', options: [['5', '★★★★★ (5)'], ['4', '★★★★ (4)'], ['3', '★★★ (3)'], ['2', '★★ (2)'], ['1', '★ (1)']], def: '5'},
                {key: 'text', label: 'Текст отзыва', type: 'textarea', placeholder: 'Наматрасник сел идеально…'},
            ], v => set('items', v))));
        } else if (type === 'form') {
            container.appendChild(field('Заголовок над формой', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Подпись на кнопке', textInput(b.button, v => set('button', v), 'Отправить')));
            container.appendChild(field('Кому передать заявку (телефон/email, необязательно)', textInput(b.recipient, v => set('recipient', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Заявки попадают в раздел «Клиенты и заявки» админки.';
        } else if (type === 'map') {
            container.appendChild(field('Заголовок над картой', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Адрес или координаты', textInput(b.address, v => set('address', v), 'Москва, Красная площадь, 1')));
            container.appendChild(field('Масштаб карты', numberInput(b.zoom || 16, v => set('zoom', v), 9, 18)));
            container.appendChild(el('div', 'pe-help')).textContent = 'Карта Яндекса построится автоматически по адресу: 9 — весь город, 18 — отдельное здание.';
        } else if (type === 'faq') {
            container.appendChild(field('Заголовок секции', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Вопросы и ответы', itemsEditor(b.items, [
                {key: 'q', label: 'Вопрос', placeholder: 'Как стирать непромокаемый наматрасник?'},
                {key: 'a', label: 'Ответ', type: 'textarea', placeholder: 'Режим деликатной стирки при 30°…'},
            ], v => set('items', v))));
        } else if (type === 'banner') {
            container.appendChild(field('Заголовок', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Подзаголовок', textInput(b.subtitle, v => set('subtitle', v))));
            container.appendChild(field('Текст на кнопке', textInput(b.button, v => set('button', v), 'Подробнее')));
            container.appendChild(linkField('Ссылка кнопки', b.url, v => set('url', v)));
            container.appendChild(field('Цвет фона', colorInput(b.bg || '#b49d84', v => set('bg', v))));
            container.appendChild(field('Цвет текста', colorInput(b.text_color || '#ffffff', v => set('text_color', v))));
            container.appendChild(imageField('Фоновая картинка', b, 'image', set));
            container.appendChild(field('Высота баннера, px (необязательно)', numberInput(b.height || 0, v => set('height', v), 0, 800)));
            container.appendChild(el('div', 'pe-help')).textContent = 'Если задана фоновая картинка, она покроет баннер; цвет фона останется под ней.';
        } else if (type === 'html') {
            container.appendChild(field('HTML-код', textareaInput(b.html, v => set('html', v), 14)));
            container.appendChild(el('div', 'pe-help')).textContent = 'Блок рендерится только для суперадмина; при сохранении не-суперадмином сервер вырезает такие блоки.';
        } else if (type === 'section') {
            container.appendChild(field('Название секции (для списка блоков)', textInput(b.name, v => set('name', v))));
            container.appendChild(field('Фон (необязательно)', colorInput(b.bg, v => set('bg', v))));
            container.appendChild(field('Отступ сверху и снизу, px', numberInput(b.padding || 0, v => set('padding', v), 0, 200)));
            container.appendChild(field('Цвет текста (необязательно)', colorInput(b.text_color, v => set('text_color', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Текст внутри секции правится прямо на холсте: выделите секцию одним кликом, затем двойной клик по нужному заголовку или абзацу — откроется правка на месте.';
            container.appendChild(sectionElementsPanel(b, idx, set));
        } else if (type === 'text_image') {
            container.appendChild(field('Заголовок', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Текст', textareaInput(b.text, v => set('text', v), 7)));
            container.appendChild(imageField('Картинка', b, 'src', set));
            container.appendChild(field('Подпись для поисковиков (alt)', textInput(b.alt, v => set('alt', v))));
            container.appendChild(field('Картинка', selectInput([
                ['left', 'Слева от текста'], ['right', 'Справа от текста'],
            ], b.image_pos || 'left', v => set('image_pos', v))));
        } else if (type === 'gallery') {
            container.appendChild(field('Заголовок (можно пусто)', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Фото в ряд', selectInput([
                ['2', '2'], ['3', '3'], ['4', '4'],
            ], String(b.cols || '3'), v => set('cols', v))));
            const addWrap = el('div', 'pe-imgrow');
            const pick = el('button', 'pe-btn-mini', {type: 'button'});
            pick.textContent = '🖼 Добавить фото из медиатеки';
            pick.addEventListener('click', async () => {
                const url = await openMediaPicker('');
                if (url != null) {
                    const items = Array.isArray(b.items) ? b.items : [];
                    items.push({src: url, alt: ''});
                    set('items', items);
                }
            });
            addWrap.appendChild(pick);
            container.appendChild(addWrap);
            container.appendChild(field('Фотографии', itemsEditor(b.items, [
                {key: 'src', label: 'Ссылка на фото', placeholder: '/static/img/…'},
                {key: 'alt', label: 'Подпись (для поисковиков)', placeholder: 'Необязательно'},
            ], v => set('items', v))));
        } else if (type === 'features') {
            container.appendChild(field('Заголовок секции', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Карточек в ряд', selectInput([
                ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5'],
            ], String(b.cols || '4'), v => set('cols', v))));
            container.appendChild(field('Преимущества', itemsEditor(b.items, [
                {key: 'title', label: 'Заголовок', placeholder: 'Гарантия качества'},
                {key: 'text', label: 'Пояснение', type: 'textarea', placeholder: 'Гарантия на всю продукцию 1 год'},
                {key: 'icon', label: 'Иконка: набор (icon:truck), эмодзи (🏆) или ссылка', placeholder: 'icon:shield или 🚚 или /static/upload/icon.svg'},
            ], v => set('items', v))));
            container.appendChild(el('div', 'pe-help')).textContent =
                'Иконки: встроенный набор — icon:truck (доставка), icon:shield (гарантия), icon:percent (скидки), icon:chat (консультация), icon:star, icon:heart, icon:phone, icon:mail, icon:check, icon:arrow-right, icon:box, icon:gift, icon:clock, icon:map-pin, icon:wallet, icon:award, icon:refresh, icon:factory, icon:leaf — или эмодзи, или ссылка на свой SVG/картинку (загрузите файл в медиатеке).';
        } else if (type === 'table') {
            container.appendChild(field('Заголовок (можно пусто)', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Первая строка — шапка таблицы', selectInput([
                ['1', 'Да, выделить жирным'], ['0', 'Нет, обычная строка'],
            ], b.header_row ? '1' : '0', v => set('header_row', v === '1'))));
            container.appendChild(field('Строки таблицы', itemsEditor(b.items, [
                {key: 'cells', label: 'Ячейки через вертикальную черту', placeholder: 'Наматрасник 160×200 | 2 601 ₽'},
            ], v => set('items', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Колонки разделяйте символом |. Например: Название | Размер | Цена.';
        } else if (type === 'spacer') {
            container.appendChild(field('Высота отступа, px', numberInput(b.height || 40, v => set('height', v), 5, 300)));
        } else if (type === 'text_columns') {
            container.appendChild(field('Заголовок (можно пусто)', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Колонки текста', itemsEditor(b.items, [
                {key: 'text', label: 'Текст колонки', type: 'textarea', placeholder: 'Пустая строка — новый абзац'},
            ], v => set('items', v))));
            container.appendChild(el('div', 'pe-help')).textContent = 'Колонок может быть от 2 до 4 — лишние не отображаются.';
        } else if (type === 'contacts') {
            container.appendChild(field('Заголовок', textInput(b.title, v => set('title', v))));
            container.appendChild(field('Телефон', textInput(b.phone, v => set('phone', v), '8 800 000-00-00')));
            container.appendChild(field('Email', textInput(b.email, v => set('email', v), 'info@site.ru')));
            container.appendChild(field('Адрес', textInput(b.address, v => set('address', v))));
            container.appendChild(field('Режим работы', textInput(b.worktime, v => set('worktime', v), 'Пн–Пт, 9:00–18:00')));
        } else if (type === 'timer') {
            container.appendChild(field('Заголовок', textInput(b.title, v => set('title', v), 'До конца акции')));
            container.appendChild(field('Дата окончания акции', textInput(b.date, v => set('date', v), '2026-12-31')));
            container.appendChild(field('Текст на кнопке', textInput(b.button_text, v => set('button_text', v), 'Успеть купить')));
            container.appendChild(linkField('Ссылка кнопки', b.button_link, v => set('button_link', v)));
            container.appendChild(el('div', 'pe-help')).textContent = 'Формат даты: ГГГГ-ММ-ДД. Когда дата наступит, посетители увидят «Акция завершена».';
        } else if (type === 'zero') {
            container.appendChild(field('Высота холста, px', numberInput(b.height || 500, v => set('height', v), 100, 2000)));
            const open = el('button', 'pe-btn-action', {type: 'button'});
            open.textContent = '🎯 Открыть Zero-редактор';
            open.addEventListener('click', () => openZero(idx));
            container.appendChild(open);
            container.appendChild(el('div', 'pe-help')).textContent = 'В Zero-редакторе элементы перетаскиваются мышкой, стрелки двигают выбранный элемент (Shift — размер, Alt — точность 0.1%).';
        }
    }

    // Поле ссылки: тип (внутр./внешн./якорь/tel/mailto) + значение
    function linkField(labelText, value, onchange) {
        value = value || '';
        let kind = 'internal';
        if (value.startsWith('tel:')) kind = 'tel';
        else if (value.startsWith('mailto:')) kind = 'mailto';
        else if (value.startsWith('#')) kind = 'anchor';
        else if (/^https?:\/\//.test(value)) kind = 'external';
        const wrap = el('div', 'pe-field');
        const l = document.createElement('label');
        l.textContent = labelText;
        const row = el('div', 'pe-linkrow');
        const kindSel = selectInput([
            ['internal', 'Страница сайта'],
            ['external', 'Внешняя ссылка'],
            ['anchor', 'Якорь на странице'],
            ['tel', 'Телефон'],
            ['mailto', 'Email'],
        ], kind, () => { renderVal(); commit(); });
        const valWrap = el('div', 'pe-linkval');
        row.appendChild(kindSel);
        row.appendChild(valWrap);
        wrap.appendChild(l);
        wrap.appendChild(row);
        let inp;
        function currentVal() {
            const v = inp ? inp.value.trim() : '';
            if (kind === 'tel') return v ? 'tel:' + v.replace(/[^+\d]/g, '') : '';
            if (kind === 'mailto') return v ? 'mailto:' + v.replace(/^mailto:/, '') : '';
            if (kind === 'anchor') return v ? '#' + v.replace(/^#/, '') : '';
            if (kind === 'internal') return v && !v.startsWith('/') ? '/' + v : v;
            return v;
        }
        function commit() { onchange(currentVal()); }
        function renderVal() {
            valWrap.innerHTML = '';
            const raw = kind === 'tel' ? value.replace(/^tel:/, '')
                : kind === 'mailto' ? value.replace(/^mailto:/, '')
                : kind === 'anchor' ? value.replace(/^#/, '')
                : kind === 'internal' ? value.replace(/^\//, '') : value;
            inp = textInput(raw, () => commit(),
                {internal: 'catalog/ или akcii/', external: 'https://…',
                    anchor: 'contacts', tel: '+7 900 000-00-00', mailto: 'info@site.ru'}[kind]);
            valWrap.appendChild(inp);
        }
        renderVal();
        return wrap;
    }

    /* ==================== Вкладка «Фон» (CN-T4) ==================== */
    const BG_SWATCHES = ['#ffffff', '#f7f4ef', '#f0ece6', '#b49d84', '#9a8269', '#333333', '#2a2c33', '#137333', '#8a2be2', '#c00000'];

    function renderBgTab(b, set, container) {
        const bgType = b.bg_type || (b.bg ? 'color' : 'none');
        container.appendChild(field('Тип фона', selectInput([
            ['none', 'Нет (фон темы)'],
            ['color', 'Цвет'],
            ['image', 'Картинка'],
            ['gradient', 'Градиент'],
            ['video', 'Видео'],
        ], bgType === 'none' ? 'none' : bgType, v => {
            if (v === 'none') { delete b.bg_type; delete b.bg; }
            else b.bg_type = v;
            markDirty();
            recordHistoryDebounced();
            scheduleRefresh();
            activeTab = 'bg';
            openRightPanel(selectedIdx);
        })));

        if (bgType === 'color') {
            container.appendChild(colorField('Цвет фона', b.bg, v => set('bg', v)));
            container.appendChild(swatchRow(v => set('bg', v)));
        } else if (bgType === 'image') {
            container.appendChild(imageField('Фоновая картинка', b, 'bg_image', set));
            container.appendChild(field('Положение', positionGrid(b.bg_image_position || 'center', v => set('bg_image_position', v))));
            container.appendChild(field('Размер', selectInput([
                ['cover', 'Заполнить (cover)'], ['contain', 'Вписать (contain)'], ['auto', 'Оригинальный'],
            ], b.bg_image_size || 'cover', v => set('bg_image_size', v))));
            container.appendChild(field('Повтор', selectInput([
                ['no-repeat', 'Не повторять'], ['repeat', 'Замостить'],
            ], b.bg_image_repeat || 'no-repeat', v => set('bg_image_repeat', v))));
            container.appendChild(field('Параллакс при прокрутке', toggleInput(!!b.bg_parallax, v => set('bg_parallax', v), 'Включить')));
        } else if (bgType === 'gradient') {
            container.appendChild(colorField('Цвет 1 (от)', b.bg_gradient_from, v => set('bg_gradient_from', v)));
            container.appendChild(colorField('Цвет 2 (до)', b.bg_gradient_to, v => set('bg_gradient_to', v)));
            container.appendChild(field('Угол', rangeInput(b.bg_gradient_angle == null ? 180 : b.bg_gradient_angle, v => set('bg_gradient_angle', v),
                {min: 0, max: 360, step: 1, suffix: '°'})));
        } else if (bgType === 'video') {
            container.appendChild(field('Ссылка на видео (YouTube/Vimeo/RuTube)', textInput(b.bg_video, v => set('bg_video', v), 'https://youtube.com/watch?v=…')));
            container.appendChild(field('Затемнение поверх видео', rangeInput(b.bg_opacity == null ? 40 : b.bg_opacity, v => set('bg_opacity', v),
                {min: 0, max: 100, step: 5, suffix: '%'})));
        }
    }

    function swatchRow(onpick) {
        const row = el('div', 'pe-swatches');
        BG_SWATCHES.forEach(c => {
            const s = el('button', 'pe-swatch', {type: 'button', title: c});
            s.style.background = c;
            s.addEventListener('click', () => onpick(c));
            row.appendChild(s);
        });
        return row;
    }

    function positionGrid(value, onchange) {
        const POS = [
            ['left top', '←↑'], ['center top', '↑'], ['right top', '→↑'],
            ['left center', '←'], ['center', '•'], ['right center', '→'],
            ['left bottom', '←↓'], ['center bottom', '↓'], ['right bottom', '→↓'],
        ];
        const grid = el('div', 'pe-posgrid');
        POS.forEach(([val, label]) => {
            const btn = el('button', 'pe-posbtn' + (value === val ? ' pe-posbtn-active' : ''), {type: 'button', title: val});
            btn.textContent = label;
            btn.addEventListener('click', () => {
                grid.querySelectorAll('.pe-posbtn').forEach(x => x.classList.remove('pe-posbtn-active'));
                btn.classList.add('pe-posbtn-active');
                onchange(val);
            });
            grid.appendChild(btn);
        });
        return grid;
    }

    /* ==================== Вкладка «Расположение» (CN-T4) ==================== */
    function renderLayoutTab(b, set, container) {
        container.appendChild(field('Отступ сверху', stepper(b.padding_top == null ? (b.padding || 0) : b.padding_top,
            v => { delete b.padding; set('padding_top', v); }, {min: 0, max: 200, step: 10})));
        container.appendChild(field('Отступ снизу', stepper(b.padding_bottom == null ? (b.padding || 0) : b.padding_bottom,
            v => { delete b.padding; set('padding_bottom', v); }, {min: 0, max: 200, step: 10})));
        container.appendChild(field('Ширина контента', selectInput([
            ['theme', 'По ширине темы'], ['full', 'На всю ширину'],
        ], b.container || 'theme', v => set('container', v))));
        container.appendChild(field('Минимальная высота, px (0 — не задана)', numberInput(b.min_height || 0, v => set('min_height', v), 0, 1200)));
        // content_align читается сервером только если у блока нет собственного align
        if (!('align' in b)) {
            container.appendChild(field('Выравнивание содержимого', selectInput([
                ['', 'По умолчанию'], ['left', 'Слева'], ['center', 'По центру'], ['right', 'Справа'],
            ], b.content_align || '', v => set('content_align', v))));
        }
        container.appendChild(el('div', 'pe-help')).textContent = 'Отступы и высота применяются к блоку целиком; «На всю ширину» растягивает контент за пределы ширины темы.';
    }

    /* ==================== Вкладка «Типографика» (CN-T4) ==================== */
    // Ключи — СТРОГО те, что читает рендерер blocks.py (_typo(block, element))
    const TYPO_ELEMENTS = {
        heading: [['title', 'Заголовок']],
        text: [['text', 'Текст']],
        text_image: [['title', 'Заголовок'], ['text', 'Текст']],
        products: [['title', 'Заголовок']],
        reviews: [['title', 'Заголовок'], ['item_text', 'Текст отзыва']],
        features: [['title', 'Заголовок'], ['item_title', 'Заголовок карточки'], ['item_text', 'Текст карточки']],
        form: [['title', 'Заголовок']],
        faq: [['title', 'Заголовок']],
        banner: [['title', 'Заголовок'], ['subtitle', 'Подзаголовок']],
        timer: [['title', 'Заголовок']],
        gallery: [['title', 'Заголовок']],
        text_columns: [['title', 'Заголовок'], ['text', 'Текст']],
        contacts: [['title', 'Заголовок']],
        table: [['title', 'Заголовок']],
        image: [['caption', 'Подпись под картинкой']],
    };

    function ensureTypo(block) {
        if (!block.typography || typeof block.typography !== 'object') block.typography = {};
        return block.typography;
    }

    function renderTypoTab(b, set, container) {
        const type = b.type || 'text';
        const elements = TYPO_ELEMENTS[type];
        if (!elements || !elements.length) {
            container.appendChild(el('div', 'pe-help')).textContent = 'Для этого типа блока типографика не настраивается.';
            return;
        }
        if (!typoElement || !elements.some(([k]) => k === typoElement)) typoElement = elements[0][0];
        container.appendChild(field('Элемент', selectInput(elements, typoElement, v => {
            typoElement = v;
            activeTab = 'typo';
            openRightPanel(selectedIdx);
        })));

        const typo = ensureTypo(b);
        if (!typo[typoElement] || typeof typo[typoElement] !== 'object') typo[typoElement] = {};
        const cfg = typo[typoElement];
        const setT = (k, v) => {
            if (v === '' || v == null) delete cfg[k];
            else cfg[k] = v;
            // пустой конфиг не храним
            if (!Object.keys(cfg).length) delete typo[typoElement];
            markDirty();
            recordHistoryDebounced();
            scheduleRefresh();
            renderTypoPreview(cfg);
        };

        container.appendChild(field('Шрифт Google', fontSelect(cfg.font_google, v => setT('font_google', v))));
        const preview = el('div', 'pe-typo-preview');
        preview.textContent = 'Пример текста элементА 123';
        container.appendChild(preview);
        const renderTypoPreview = c => {
            preview.style.cssText = typoToCss(c);
        };
        renderTypoPreview(cfg);

        container.appendChild(field('Размер', textInput(cfg.size, v => setT('size', v), '18px или 1.2rem')));
        container.appendChild(colorField('Цвет', cfg.color, v => setT('color', v)));
        container.appendChild(field('Межстрочный интервал', textInput(cfg.line_height, v => setT('line_height', v), '1.4')));
        container.appendChild(field('Трекинг (межбуквенный)', textInput(cfg.letter_spacing, v => setT('letter_spacing', v), '0.5px или 0.05em')));
        container.appendChild(field('Начертание', selectInput([
            ['', 'Как в теме'], ['normal', 'Обычный'], ['medium', 'Средний (500)'], ['bold', 'Жирный'],
        ], cfg.weight || '', v => setT('weight', v))));
        container.appendChild(field('Курсив', toggleInput(!!cfg.italic, v => setT('italic', v ? true : ''), 'Курсив')));
        container.appendChild(field('КАПС (все заглавные)', toggleInput(!!cfg.uppercase, v => setT('uppercase', v ? true : ''), 'КАПС')));
        container.appendChild(field('Выравнивание', selectInput([
            ['', 'Как у блока'], ['left', 'Слева'], ['center', 'По центру'], ['right', 'Справа'], ['justify', 'По ширине'],
        ], cfg.align || '', v => setT('align', v))));
        container.appendChild(el('div', 'pe-help')).textContent = 'Пустые поля — значения из темы сайта.';
    }

    function typoToCss(cfg) {
        const parts = [];
        if (cfg.font_google) parts.push("font-family:'" + cfg.font_google + "',sans-serif");
        if (cfg.size) parts.push('font-size:' + cfg.size);
        if (cfg.color) parts.push('color:' + cfg.color);
        if (cfg.line_height) parts.push('line-height:' + cfg.line_height);
        if (cfg.letter_spacing) parts.push('letter-spacing:' + cfg.letter_spacing);
        if (cfg.weight) parts.push('font-weight:' + ({normal: 400, medium: 500, bold: 700}[cfg.weight] || cfg.weight));
        if (cfg.italic) parts.push('font-style:italic');
        if (cfg.uppercase) parts.push('text-transform:uppercase');
        if (cfg.align) parts.push('text-align:' + cfg.align);
        return parts.join(';');
    }

    function fontSelect(value, onchange) {
        const sel = document.createElement('select');
        const load = fonts => {
            sel.innerHTML = '';
            const def = document.createElement('option');
            def.value = '';
            def.textContent = 'Шрифт темы (по умолчанию)';
            sel.appendChild(def);
            const groups = {sans: 'Без засечек', serif: 'С засечками', display: 'Декоративные'};
            Object.keys(groups).forEach(cat => {
                const g = fonts.filter(f => f.category === cat);
                if (!g.length) return;
                const og = document.createElement('optgroup');
                og.label = groups[cat];
                og.dataset.cat = cat;
                g.forEach(f => {
                    const o = document.createElement('option');
                    o.value = f.family;
                    o.textContent = f.family + (f.weights && f.weights.length ? ' (' + f.weights.join('/') + ')' : '');
                    og.appendChild(o);
                });
                sel.appendChild(og);
            });
            sel.value = value || '';
            // превью начертания в самом списке
            sel.querySelectorAll('option[value]').forEach(o => {
                o.style.fontFamily = "'" + o.value + "',sans-serif";
            });
        };
        if (fontsCache) load(fontsCache);
        else {
            const def = document.createElement('option');
            def.value = '';
            def.textContent = 'Загрузка шрифтов…';
            sel.appendChild(def);
            apiGet('/fonts').then(d => {
                if (d.success && Array.isArray(d.fonts)) {
                    fontsCache = d.fonts;
                    load(fontsCache);
                }
            }).catch(() => {
                sel.innerHTML = '';
                const o = document.createElement('option');
                o.value = '';
                o.textContent = 'Список шрифтов недоступен';
                sel.appendChild(o);
            });
        }
        sel.addEventListener('change', () => {
            if (sel.value) loadGoogleFont(sel.value);
            onchange(sel.value);
        });
        return sel;
    }
    function loadGoogleFont(family) {
        if (document.querySelector('link[data-pe-font="' + family + '"]')) return;
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.dataset.peFont = family;
        link.href = 'https://fonts.googleapis.com/css2?family=' +
            family.replace(/ /g, '+') + ':wght@300;400;500;600;700;900&display=swap';
        document.head.appendChild(link);
    }

    /* ==================== Вкладка «Кнопка» (CN-T4) ==================== */
    // Ключи — как в blocks.py _button_style: variant/bg/color/border_color/radius/shadow/hover/size
    function renderButtonTab(b, idx, type, set, container) {
        const TEXT_KEY = {button: 'text', banner: 'button', timer: 'button_text', form: 'button'}[type];
        const LINK_KEY = {button: 'url', banner: 'url', timer: 'button_link'}[type];
        if (TEXT_KEY) container.appendChild(field('Текст на кнопке', textInput(b[TEXT_KEY], v => set(TEXT_KEY, v))));
        if (type === 'button') {
            // Действие кнопки (цикл 4 / этап 5): ссылка / якорь / tel / mailto /
            // форма заявки / подписка / текстовый попап. Рендер в blocks.py.
            const act = b.action || 'link';
            const setAct = v => {
                if (v === 'link') delete b.action; else b.action = v;
                markDirty();
                recordHistory();
                openRightPanel(idx);
            };
            container.appendChild(field('Действие по клику', selectInput([
                ['link', 'Переход по ссылке'],
                ['anchor', 'Прокрутка к якорю на странице'],
                ['tel', 'Звонок (телефон)'],
                ['mailto', 'Письмо (email)'],
                ['form', 'Форма заявки (всплывающее окно)'],
                ['subscribe', 'Подписка на рассылку (всплывающее окно)'],
                ['popup', 'Текстовое всплывающее окно'],
            ], act, setAct)));
            if (act === 'link') {
                container.appendChild(linkField('Ссылка кнопки', b.url, v => set('url', v)));
            } else if (act === 'anchor') {
                container.appendChild(field('ID якоря', textInput(b.anchor, v => set('anchor', v), 'например: about'),
                    'Укажите тот же CSS-класс блока, к которому вести (вкладка «Дополнительно» целевого блока).'));
            } else if (act === 'tel') {
                container.appendChild(field('Телефон', textInput(b.phone, v => set('phone', v), '+7 900 000-00-00')));
            } else if (act === 'mailto') {
                container.appendChild(field('Email', textInput(b.email, v => set('email', v), 'info@example.com')));
            } else if (act === 'popup') {
                container.appendChild(field('Заголовок окна', textInput(b.popup_title, v => set('popup_title', v))));
                container.appendChild(field('Текст окна', textareaInput(b.popup_text, v => set('popup_text', v), 5)));
            }
            container.appendChild(el('div', 'pe-help')).textContent =
                '«Форма заявки» — посетитель оставляет имя и телефон, заявка попадает в админку (раздел «Заявки»).';
        } else if (LINK_KEY) {
            container.appendChild(linkField('Ссылка кнопки', b[LINK_KEY], v => set(LINK_KEY, v)));
        }

        if (!b.button_style || typeof b.button_style !== 'object') b.button_style = {};
        const cfg = b.button_style;
        const setB = (k, v) => {
            if (v === '' || v == null) delete cfg[k];
            else cfg[k] = v;
            if (!Object.keys(cfg).length) delete b.button_style;
            markDirty();
            recordHistoryDebounced();
            scheduleRefresh();
        };
        container.appendChild(field('Стиль', selectInput([
            ['fill', 'Заливка'], ['outline', 'Обводка'], ['none', 'Без фона (текст-ссылка)'],
        ], cfg.variant || 'fill', v => setB('variant', v === 'fill' ? '' : v))));
        container.appendChild(colorField('Цвет фона / заливки', cfg.bg, v => setB('bg', v)));
        container.appendChild(colorField('Цвет текста', cfg.color, v => setB('color', v)));
        container.appendChild(colorField('Цвет обводки', cfg.border_color, v => setB('border_color', v)));
        container.appendChild(field('Скругление углов', rangeInput(cfg.radius == null ? 6 : cfg.radius, v => setB('radius', v),
            {min: 0, max: 60, step: 1, suffix: 'px'})));
        container.appendChild(field('Тень', selectInput([
            ['0', 'Без тени'], ['1', 'Слабая'], ['2', 'Сильная'],
        ], String(cfg.shadow == null ? 0 : cfg.shadow), v => setB('shadow', v === '0' ? '' : +v))));
        container.appendChild(field('При наведении', selectInput([
            ['none', 'Нет эффекта'], ['darken', 'Потемнение'], ['lift', 'Подъём'],
        ], cfg.hover || 'none', v => setB('hover', v === 'none' ? '' : v))));
        container.appendChild(field('Размер', selectInput([
            ['s', 'S (маленькая)'], ['m', 'M (средняя)'], ['l', 'L (крупная)'],
        ], cfg.size || 'm', v => setB('size', v === 'm' ? '' : v))));
        container.appendChild(el('div', 'pe-help')).textContent = 'Пустые поля — стандартный вид кнопки темы (акцент #b49d84).';
    }

    /* ==================== Вкладка «Эффекты» (CN-T4) ==================== */
    // Ключи — как в blocks.py: effects.{anim,anim_duration,anim_delay,sticky,decor_top,decor_bottom,hide_on}
    function renderFxTab(b, set, container) {
        if (!b.effects || typeof b.effects !== 'object') b.effects = {};
        const fx = b.effects;
        const setF = (k, v) => {
            if (v === '' || v == null || v === 'none' || v === false) delete fx[k];
            else fx[k] = v;
            if (!Object.keys(fx).length) delete b.effects;
            markDirty();
            recordHistoryDebounced();
            scheduleRefresh();
        };
        const setDecor = (key, prop, v) => {
            if (!fx[key] || typeof fx[key] !== 'object') fx[key] = {};
            if (v === '' || v == null) delete fx[key][prop];
            else fx[key][prop] = v;
            if (!Object.keys(fx[key]).length) delete fx[key];
            markDirty();
            recordHistoryDebounced();
            scheduleRefresh();
        };

        container.appendChild(field('Анимация появления', selectInput([
            ['none', 'Нет'], ['fade', 'Плавное появление'], ['slide-up', 'Выезд снизу'],
            ['slide-left', 'Выезд справа'], ['slide-right', 'Выезд слева'], ['zoom', 'Увеличение'],
        ], fx.anim || 'none', v => setF('anim', v))));
        if (fx.anim) {
            container.appendChild(field('Длительность', rangeInput(fx.anim_duration == null ? 600 : fx.anim_duration,
                v => setF('anim_duration', v === 600 ? '' : v), {min: 200, max: 2000, step: 50, suffix: ' мс'})));
            container.appendChild(field('Задержка', rangeInput(fx.anim_delay == null ? 0 : fx.anim_delay,
                v => setF('anim_delay', v === 0 ? '' : v), {min: 0, max: 2000, step: 50, suffix: ' мс'})));
        }
        container.appendChild(field('Прилипание (sticky)', toggleInput(!!fx.sticky, v => setF('sticky', v), 'Блок прилипает к верху при прокрутке')));
        container.appendChild(el('div', 'pe-fx-decor-head')).textContent = 'Декоративная линия сверху';
        container.appendChild(colorField('Цвет линии', fx.decor_top && fx.decor_top.color, v => setDecor('decor_top', 'color', v)));
        container.appendChild(field('Толщина', rangeInput(fx.decor_top && fx.decor_top.thickness != null ? fx.decor_top.thickness : 1,
            v => setDecor('decor_top', 'thickness', v), {min: 1, max: 20, suffix: 'px'})));
        container.appendChild(field('Ширина', rangeInput(fx.decor_top && fx.decor_top.width != null ? fx.decor_top.width : 100,
            v => setDecor('decor_top', 'width', v), {min: 1, max: 100, suffix: '%'})));
        container.appendChild(el('div', 'pe-fx-decor-head')).textContent = 'Декоративная линия снизу';
        container.appendChild(colorField('Цвет линии', fx.decor_bottom && fx.decor_bottom.color, v => setDecor('decor_bottom', 'color', v)));
        container.appendChild(field('Толщина', rangeInput(fx.decor_bottom && fx.decor_bottom.thickness != null ? fx.decor_bottom.thickness : 1,
            v => setDecor('decor_bottom', 'thickness', v), {min: 1, max: 20, suffix: 'px'})));
        container.appendChild(field('Ширина', rangeInput(fx.decor_bottom && fx.decor_bottom.width != null ? fx.decor_bottom.width : 100,
            v => setDecor('decor_bottom', 'width', v), {min: 1, max: 100, suffix: '%'})));
        container.appendChild(field('Скрыть блок', selectInput([
            ['none', 'Везде показывать'], ['mobile', 'На мобильных'], ['desktop', 'На десктопе'],
        ], fx.hide_on || 'none', v => setF('hide_on', v))));
        container.appendChild(el('div', 'pe-help')).textContent = 'Анимация срабатывает при появлении блока в зоне видимости посетителя.';
    }

    /* ==================== Вкладка «Дополнительно» ==================== */
    function renderExtraTab(b, set, container) {
        container.appendChild(field('CSS-класс блока', textInput(b.css_class, v => set('css_class', v), 'my-block-class'),
            'Добавится к классам обёртки блока: латиница, цифры, дефис, подчёркивание.'));
        const ta = textareaInput(b.custom_css, v => set('custom_css', v), 8);
        container.appendChild(field('Произвольный CSS', ta,
            'Применяется к обёртке блока. Рендерится только для суперадмина — у остальных блок выглядит стандартно.'));
        container.appendChild(field('Скрыть блок целиком', toggleInput(!!b.hidden, v => {
            b.hidden = v || undefined;
            if (!b.hidden) delete b.hidden;
            markDirty();
            recordHistory();
            renderStructure();
            refreshPage();
        }, 'Блок останется в редакторе, но не покажется посетителям')));
    }

    /* ==================== Медиатека (модалка выбора файла) ==================== */
    const mediaOverlay = el('div', 'pe-modal-overlay');
    mediaOverlay.id = 'pe-media-overlay';
    mediaOverlay.style.display = 'none';
    let mediaResolve = null;

    function openMediaPicker(currentUrl) {
        mediaOverlay.innerHTML =
            '<div class="pe-modal pe-media-modal">' +
            '<div class="pe-modal-head"><span>Медиатека</span>' +
            '<button type="button" class="pe-modal-close" data-close title="Закрыть">✕</button></div>' +
            '<div class="pe-media-tabs">' +
            '<button type="button" data-tab="lib" class="pe-tab pe-tab-active">Библиотека файлов</button>' +
            '<button type="button" data-tab="upload" class="pe-tab">Загрузка</button>' +
            '<button type="button" data-tab="url" class="pe-tab">По ссылке</button>' +
            '</div>' +
            '<div class="pe-media-body">' +
            '<div class="pe-media-pane" data-pane="lib">' +
            '<input type="text" id="pe-media-search" placeholder="Поиск по имени файла…">' +
            '<div id="pe-media-grid" class="pe-media-grid"></div>' +
            '</div>' +
            '<div class="pe-media-pane" data-pane="upload" style="display:none">' +
            '<label class="pe-upload-drop"><input type="file" id="pe-media-file" accept="image/*">⬆ Выберите файл с компьютера</label>' +
            '<div id="pe-media-upload-status" class="pe-help"></div>' +
            '</div>' +
            '<div class="pe-media-pane" data-pane="url" style="display:none">' +
            '<input type="text" id="pe-media-url" placeholder="/static/uploads/… или https://…">' +
            '<button type="button" id="pe-media-url-ok" class="pe-btn-action">Использовать эту ссылку</button>' +
            '</div>' +
            '</div>' +
            '<div class="pe-modal-foot"><button type="button" class="pe-btn-mini" data-close>Отмена</button></div>' +
            '</div>';
        mediaOverlay.style.display = 'flex';
        mediaResolve = null;
        const close = result => {
            mediaOverlay.style.display = 'none';
            if (mediaResolve) { mediaResolve(result); mediaResolve = null; }
        };
        mediaOverlay.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => close(null)));
        mediaOverlay.onmousedown = e => { if (e.target === mediaOverlay) close(null); };
        mediaOverlay.querySelectorAll('.pe-media-tabs .pe-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                mediaOverlay.querySelectorAll('.pe-media-tabs .pe-tab').forEach(x => x.classList.remove('pe-tab-active'));
                tab.classList.add('pe-tab-active');
                mediaOverlay.querySelectorAll('.pe-media-pane').forEach(p =>
                    p.style.display = p.dataset.pane === tab.dataset.tab ? 'block' : 'none');
            });
        });
        const grid = mediaOverlay.querySelector('#pe-media-grid');
        const search = mediaOverlay.querySelector('#pe-media-search');
        let selected = currentUrl || '';
        async function loadMedia() {
            grid.innerHTML = '<div class="pe-help">Загрузка…</div>';
            try {
                const d = await apiGet(`/media?limit=200&q=${encodeURIComponent(search.value || '')}`);
                const files = (d.files || []).filter(f => /\.(png|jpe?g|gif|webp|svg)$/i.test(f.name));
                if (!files.length) { grid.innerHTML = '<div class="pe-help">Файлов не найдено. Загрузите первый — вкладка «Загрузка».</div>'; return; }
                grid.innerHTML = '';
                files.forEach(f => {
                    const card = el('div', 'pe-media-card' + (f.url === selected ? ' pe-media-sel' : ''));
                    card.innerHTML = '<img src="' + esc(f.url) + '" alt="" loading="lazy"><div class="pe-media-name" title="' + esc(f.name) + '">' + esc(f.name) + '</div>';
                    card.addEventListener('click', () => {
                        grid.querySelectorAll('.pe-media-card').forEach(c => c.classList.remove('pe-media-sel'));
                        card.classList.add('pe-media-sel');
                        selected = f.url;
                        close(f.url);
                    });
                    grid.appendChild(card);
                });
            } catch (e) {
                grid.innerHTML = '<div class="pe-help">Ошибка загрузки медиатеки: ' + esc(e.message) + '</div>';
            }
        }
        search.addEventListener('input', debounce(loadMedia, 300));
        loadMedia();
        const file = mediaOverlay.querySelector('#pe-media-file');
        const st = mediaOverlay.querySelector('#pe-media-upload-status');
        file.addEventListener('change', async () => {
            if (!file.files.length) return;
            st.textContent = 'Загрузка…';
            const url = await uploadFile(file.files[0]);
            if (url) close(url);
            else st.textContent = 'Не удалось загрузить файл.';
        });
        mediaOverlay.querySelector('#pe-media-url-ok').addEventListener('click', () => {
            const v = mediaOverlay.querySelector('#pe-media-url').value.trim();
            if (v) close(v);
        });
        return new Promise(resolve => { mediaResolve = resolve; });
    }

    /* ==================== Модалка картинки (клик по img на холсте) ==================== */
    function openImageModal(idx, itemIdx) {
        const b = blocks[idx];
        if (!b) return;
        const isGallery = b.type === 'gallery' && itemIdx != null;
        const item = isGallery ? (b.items || [])[itemIdx] : null;
        const ov = el('div', 'pe-modal-overlay');
        ov.innerHTML =
            '<div class="pe-modal">' +
            '<div class="pe-modal-head"><span>' + (isGallery ? 'Фото галереи' : 'Картинка') + '</span>' +
            '<button type="button" class="pe-modal-close" data-close title="Закрыть">✕</button></div>' +
            '<div class="pe-modal-body">' +
            '<div class="pe-imgrow">' +
            '<button type="button" class="pe-btn-mini" id="pe-im-media">🖼 Медиатека</button>' +
            '<button type="button" class="pe-btn-mini" id="pe-im-upload">⬆ Загрузить</button>' +
            '<input type="file" id="pe-im-file" accept="image/*" style="display:none">' +
            '</div>' +
            '<div class="pe-field"><label>Ссылка на картинку</label><input type="text" id="pe-im-src"></div>' +
            '<div class="pe-preview-img"><img id="pe-im-preview" alt=""></div>' +
            '<div class="pe-field"><label>Подпись для поисковиков (alt)</label><input type="text" id="pe-im-alt"></div>' +
            (isGallery || b.type !== 'image' ? '' : '<div class="pe-field"><label>Подпись под картинкой</label><input type="text" id="pe-im-caption"></div>') +
            '</div>' +
            '<div class="pe-modal-foot"><button type="button" class="pe-btn-mini" data-close>Отмена</button>' +
            '<button type="button" class="pe-btn-action" id="pe-im-ok">Применить</button></div>' +
            '</div>';
        const srcInp = ov.querySelector('#pe-im-src');
        const altInp = ov.querySelector('#pe-im-alt');
        const capInp = ov.querySelector('#pe-im-caption');
        const preview = ov.querySelector('#pe-im-preview');
        srcInp.value = isGallery ? (item && item.src || '') : (b.src || '');
        altInp.value = isGallery ? (item && item.alt || '') : (b.alt || '');
        if (capInp) capInp.value = b.caption || '';
        preview.src = srcInp.value;
        srcInp.addEventListener('input', () => { preview.src = srcInp.value; });
        const close = () => ov.remove();
        ov.querySelectorAll('[data-close]').forEach(x => x.addEventListener('click', close));
        ov.addEventListener('mousedown', e => { if (e.target === ov) close(); });
        ov.querySelector('#pe-im-media').addEventListener('click', async () => {
            const url = await openMediaPicker(srcInp.value);
            if (url != null) { srcInp.value = url; preview.src = url; }
        });
        const file = ov.querySelector('#pe-im-file');
        ov.querySelector('#pe-im-upload').addEventListener('click', () => file.click());
        file.addEventListener('change', async () => {
            if (!file.files.length) return;
            const url = await uploadFile(file.files[0]);
            if (url) { srcInp.value = url; preview.src = url; }
        });
        ov.querySelector('#pe-im-ok').addEventListener('click', () => {
            if (isGallery) {
                if (item) { item.src = srcInp.value.trim(); item.alt = altInp.value.trim(); }
            } else {
                b.src = srcInp.value.trim();
                b.alt = altInp.value.trim();
                if (capInp) b.caption = capInp.value.trim();
            }
            markDirty();
            recordHistory();
            scheduleRefresh();
            close();
        });
        document.body.appendChild(ov);
        srcInp.focus();
    }

    /* ==================== Инлайн-редактирование текста (CN-T3) ==================== */
    // Сериализация — plain text: серверный рендерер экранирует текст блоков
    // (esc() в blocks.py), поэтому разметка <b>/<i>/<a> не дошла бы до публичной
    // страницы. Мини-тулбар применяет форматирование визуально в contenteditable,
    // при сохранении остаётся чистый текст (как в редакторе цикла 1).
    // Текстовые «листья» внутри секции перенесённого дизайна: двойной клик по
    // такому элементу правит ТОЛЬКО его (а не всю секцию — иначе браузер
    // вешает contenteditable на гигантский wrapper и страница подвисает).
    const SECTION_TEXT_SEL = 'h1,h2,h3,h4,h5,h6,p,li,td,th,blockquote,figcaption,' +
        'a,button,span,strong,em,b,i,small,label';
    const EDITABLES = {
        heading: [{sel: 'h1,h2,h3,h4', field: 'text'}],
        text: [{sel: 'p', field: 'text', multi: true}],
        text_image: [{sel: '.cb-ti-text h2', field: 'title'}, {sel: '.cb-ti-text p', field: 'text', multi: true}],
        banner: [{sel: '.cb-banner-title', field: 'title'}, {sel: '.cb-banner-sub', field: 'subtitle'}, {sel: '.cb-banner-btn', field: 'button', btn: true}],
        timer: [{sel: 'h2', field: 'title'}, {sel: '.cb-timer-btn', field: 'button_text', btn: true}],
        products: [{sel: 'h2', field: 'title'}],
        reviews: [{sel: 'h2', field: 'title'}],
        features: [{sel: 'h2', field: 'title'}],
        faq: [{sel: 'h2', field: 'title'}],
        gallery: [{sel: 'h2', field: 'title'}],
        table: [{sel: 'h2', field: 'title'}],
        text_columns: [{sel: 'h2', field: 'title'}],
        contacts: [{sel: 'h2', field: 'title'}],
        form: [{sel: 'h2', field: 'title'}, {sel: 'form button[type="submit"]', field: 'button', btn: true}],
        button: [{sel: 'a', field: 'text', btn: true}],
        map: [{sel: 'h2', field: 'title'}],
        section: [{sel: SECTION_TEXT_SEL, field: 'html', section: true}],
    };

    // Ближайший к клику текстовый элемент внутри секции (не выходя за её корни).
    // Корень может САМ быть текстовым листом (секция из одного голого h2/p) —
    // тогда правим его, а не возвращаем null.
    function sectionTextTarget(entry, node) {
        let n = node && node.nodeType === 1 ? node : (node && node.parentElement);
        while (n && n !== document.body) {
            const isRoot = entry.elements.indexOf(n) >= 0;
            if (n.matches && n.matches(SECTION_TEXT_SEL)) return n;
            if (isRoot) return null;   // корень-обёртка — выше текстовых листьев
            n = n.parentElement;
        }
        return null;
    }

    const textEdit = {active: false, idx: -1, targets: [], cfg: null, original: [], bar: null, target: null};

    function startTextEdit(idx, targetEl) {
        const b = blocks[idx];
        const entry = entryAt(idx);
        if (!b || !entry || !entry.elements.length) return;
        if (b.legacy) return;
        const cfgs = EDITABLES[b.type];
        if (!cfgs) return;
        let cfg = null;
        if (b.type === 'section') {
            cfg = cfgs[0];
        } else {
            cfg = cfgs.find(c => {
                if (c.whole) return true;
                const ts = c.multi ? entryTargets(entry, c.sel) : [entryMatch(entry, c.sel)].filter(Boolean);
                return ts.some(t => t === targetEl || t.contains(targetEl));
            });
            if (!cfg) cfg = cfgs.find(c => c.whole || entryMatch(entry, c.sel));
        }
        if (!cfg) return;
        if (textEdit.active) finishTextEdit(true);
        textEdit.active = true;
        textEdit.idx = idx;
        textEdit.cfg = cfg;
        if (cfg.section) {
            const t = sectionTextTarget(entry, targetEl);
            if (!t) return;
            textEdit.targets = [t];
        } else if (cfg.whole) {
            textEdit.targets = entry.elements.slice();
        } else if (cfg.multi) {
            textEdit.targets = entryTargets(entry, cfg.sel);
        } else {
            textEdit.targets = [entryMatch(entry, cfg.sel)].filter(Boolean);
        }
        if (!textEdit.targets.length) { textEdit.active = false; return; }
        textEdit.original = textEdit.targets.map(t => t.innerHTML);
        textEdit.target = targetEl;
        textEdit.targets.forEach(t => {
            t.contentEditable = 'true';
            t.classList.add('pe-editable-on');
        });
        textEdit.targets[0].focus();
        if (!cfg.multi && !cfg.whole) selectAllText(textEdit.targets[0]);
        highlight.style.display = 'none';
        addBtn.style.display = 'none';
        showTextToolbar();
    }

    function entryMatch(entry, sel) {
        for (const elm of entry.elements) {
            if (elm.matches && elm.matches(sel)) return elm;
            const q = elm.querySelector && elm.querySelector(sel);
            if (q) return q;
        }
        return null;
    }
    function entryTargets(entry, sel) {
        const out = [];
        entry.elements.forEach(elm => {
            if (elm.matches && elm.matches(sel)) out.push(elm);
            out.push(...(elm.querySelectorAll ? [...elm.querySelectorAll(sel)] : []));
        });
        return out;
    }
    function selectAllText(node) {
        const doc = node.ownerDocument || document;
        const range = doc.createRange();
        range.selectNodeContents(node);
        const sel = (doc.defaultView || window).getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }

    function showTextToolbar() {
        hideTextToolbar();
        const bar = el('div', 'pe-textbar');
        bar.innerHTML =
            '<button type="button" data-act="bold" title="Жирный (Ctrl+B)"><b>B</b></button>' +
            '<button type="button" data-act="italic" title="Курсив (Ctrl+I)"><i>I</i></button>' +
            '<button type="button" data-act="link" title="Вставить ссылку на выделенный текст">🔗</button>' +
            '<button type="button" data-act="done" class="pe-textbar-ok" title="Готово (Esc)">✓ Готово</button>';
        bar.addEventListener('mousedown', e => e.preventDefault());   // не сбрасывать фокус
        bar.addEventListener('click', e => {
            const act = e.target.closest('button') && e.target.closest('button').dataset.act;
            if (!act) return;
            // contenteditable живёт в документе контента — команды форматирования
            // выполняем там, иначе они ничего не изменят.
            const edDoc = (IFRAME_MODE && CD) ? CD : document;
            if (act === 'bold') edDoc.execCommand('bold');
            if (act === 'italic') edDoc.execCommand('italic');
            if (act === 'link') {
                const url = prompt('Адрес ссылки (https://, mailto:, tel: или #якорь):', 'https://');
                if (url && /^(https?:\/\/|mailto:|tel:|#)/i.test(url)) edDoc.execCommand('createLink', false, url);
                else if (url) toast('Ссылка не применена: разрешены http(s), mailto:, tel: и #якорь', 'error');
            }
            if (act === 'done') finishTextEdit(true);
        });
        document.body.appendChild(bar);
        textEdit.bar = bar;
        positionTextToolbar();
    }
    function positionTextToolbar() {
        if (!textEdit.bar || !textEdit.targets.length) return;
        const r = contentRect(textEdit.targets[0]);
        const bar = textEdit.bar;
        bar.style.left = Math.max(8, Math.min(window.innerWidth - bar.offsetWidth - 8, r.left)) + 'px';
        bar.style.top = Math.max(52, r.top - bar.offsetHeight - 8) + 'px';
    }
    function hideTextToolbar() {
        if (textEdit.bar) { textEdit.bar.remove(); textEdit.bar = null; }
    }

    function finishTextEdit(apply) {
        if (!textEdit.active) return;
        const b = blocks[textEdit.idx];
        const cfg = textEdit.cfg;
        textEdit.targets.forEach((t, k) => {
            t.contentEditable = 'false';
            t.classList.remove('pe-editable-on');
            if (!apply) t.innerHTML = textEdit.original[k];
        });
        if (apply && b && cfg) {
            if (cfg.section) {
                // Правили один лист внутри секции — сохраняем HTML всей секции
                // с корневых элементов (как whole, но источник — корни, а не targets)
                const entry = entryAt(textEdit.idx);
                if (entry) {
                    b.html = entry.elements.map(t =>
                        t instanceof Element ? t.outerHTML : (t.textContent || '')).join('');
                }
            } else if (cfg.whole) {
                // ВАЖНО: сохраняем outerHTML, а не innerHTML — иначе у секции
                // при первой же правке срезается корневой элемент (wrapper)
                // и блок теряет классы/стили (drag-block и т.п.) навсегда.
                b.html = textEdit.targets.map(t =>
                    t instanceof Element ? t.outerHTML : (t.textContent || '')).join('');
            } else if (cfg.multi) {
                if (b.type === 'text' && b.rich) {
                    b[cfg.field] = textEdit.targets.map(t => t.innerHTML).join('');
                } else {
                    b[cfg.field] = textEdit.targets.map(t => t.innerText.trim()).filter(Boolean).join('\n\n');
                }
            } else if (textEdit.targets[0]) {
                if (b.type === 'text' && b.rich) {
                    b[cfg.field] = textEdit.targets[0].innerHTML;
                } else {
                    b[cfg.field] = textEdit.targets[0].innerText.trim();
                }
            }
            markDirty();
            recordHistory();
            scheduleRefresh();
        }
        textEdit.active = false;
        textEdit.targets = [];
        textEdit.cfg = null;
        hideTextToolbar();
        updateOverlayPositions();
    }

    /* ==================== Клики по холсту: выделение / инлайн / картинки ====================
       Обычный клик — ВЫБОР блока (подсветка + правая панель).
       Двойной клик — ПРАВКА: текстового элемента (contenteditable) или картинки (медиатека). */
    document.addEventListener('click', onDocClick, true);
    function onDocClick(e) {
        // В iframe-режиме геометрия блоков — в системе координат iframe: обрабатываем
        // только события из документа контента (их же вешает bindContentDocument).
        if (IFRAME_MODE && (!CW || e.view !== CW)) return;
        const inEditorUI = e.target.closest &&
            e.target.closest('#pe-topbar,#pe-left,#pe-right,#pe-left-rail,#pe-right-rail,#pe-canvas-hint,#pe-right-backdrop,' +
                '.pe-toolbar,.pe-addbtn,#pe-addbtn-bridge,#pe-ui-restore,' +
                '.pe-modal-overlay,.pe-textbar,#pe-legacy-banner,.pe-toasts,.pe-zero-float,.pe-zero-handles');
        if (inEditorUI) return;
        if (zero.active) return;   // Zero-оверлей обрабатывает клики сам
        // Режим чистого просмотра: страница живёт своей жизнью (ссылки кликабельны)
        if (document.body.classList.contains('pe-ui-hidden')) return;
        // ссылки на холсте не должны уводить с редактора (в iframe-режиме
        // контент целиком внутри iframe, поэтому блокируем любые ссылки;
        // в режиме чистого просмотра UI скрыт и здесь мы не доходим).
        if (e.target.closest && e.target.closest(IFRAME_MODE ? 'a' : '#content a')) e.preventDefault();

        if (textEdit.active) {
            const inside = textEdit.targets.some(t => t.contains(e.target));
            if (inside) { e.stopPropagation(); return; }
            finishTextEdit(true);
        }
        const idx = blockIndexFromPoint(e.clientX, e.clientY);
        if (idx < 0) { select(-1); return; }
        select(idx);
    }

    document.addEventListener('dblclick', onDocDblClick, true);
    function onDocDblClick(e) {
        if (IFRAME_MODE && (!CW || e.view !== CW)) return;
        const inEditorUI = e.target.closest &&
            e.target.closest('#pe-topbar,#pe-left,#pe-right,#pe-left-rail,#pe-right-rail,#pe-canvas-hint,#pe-right-backdrop,' +
                '.pe-toolbar,.pe-addbtn,#pe-addbtn-bridge,#pe-ui-restore,' +
                '.pe-modal-overlay,.pe-textbar,#pe-legacy-banner,.pe-toasts,.pe-zero-float,.pe-zero-handles');
        if (inEditorUI) return;
        if (zero.active) return;
        if (document.body.classList.contains('pe-ui-hidden')) return;
        if (textEdit.active) return;   // уже правим — второй клик часть выделения
        if (e.target.closest && e.target.closest('#content a')) e.preventDefault();
        const idx = blockIndexFromPoint(e.clientX, e.clientY);
        if (idx < 0) return;
        const b = blocks[idx];
        if (!b || b.legacy) return;
        e.stopPropagation();
        e.preventDefault();
        select(idx);

        // двойной клик по картинке → модалка медиатеки
        const img = e.target.closest ? e.target.closest('img') : null;
        if (img && contentContains(idx, img)) {
            if (b.type === 'image') { openImageModal(idx, null); return; }
            if (b.type === 'gallery') {
                const entry = entryAt(idx);
                const items = entry ? entry.elements.flatMap(elm => [...elm.querySelectorAll('.cb-gallery-item img')]) : [];
                const k = items.indexOf(img);
                if (k >= 0) { openImageModal(idx, k); return; }
            }
            if (b.type === 'text_image') { openImageModal(idx, null); return; }
        }
        // двойной клик по текстовому элементу → contenteditable
        const cfgs = EDITABLES[b.type];
        if (!cfgs) return;
        const entry = entryAt(idx);
        if (!entry) return;
        if (b.type === 'section') {
            // Секция: правим один конкретный текстовый элемент, а не всю секцию целиком
            const t = sectionTextTarget(entry, e.target);
            if (t) startTextEdit(idx, t);
            return;
        }
        for (const c of cfgs) {
            const ts = c.multi ? entryTargets(entry, c.sel)
                : c.whole ? entry.elements
                : [entryMatch(entry, c.sel)].filter(Boolean);
            const m = ts.find(t => t === e.target || t.contains(e.target));
            if (m) { startTextEdit(idx, e.target); return; }
        }
    }
    function contentContains(idx, node) {
        const entry = entryAt(idx);
        return !!(entry && entry.elements.some(elm => elm.contains(node)));
    }

    /* ==================== Диалог «Настройки страницы» (CN-T5 UI) ==================== */
    const pageSettingsOverlay = el('div', 'pe-modal-overlay');
    pageSettingsOverlay.style.display = 'none';

    function psField(label, id, value, isTextarea, placeholder) {
        return '<div class="pe-field"><label>' + esc(label) + '</label>' +
            (isTextarea
                ? '<textarea id="' + id + '" rows="3"></textarea>'
                : '<input type="text" id="' + id + '">') + '</div>';
    }

    async function openPageSettings() {
        let data = null;
        try { data = await apiGet(`/page-settings?slug=${encodeURIComponent(SLUG)}`); }
        catch (e) { /* ниже покажем форму с локальными данными */ }
        const meta = (data && data.meta) || pageMeta;
        const s = (data && data.settings) || {};
        pageSettingsOverlay.innerHTML =
            '<div class="pe-modal pe-ps-modal">' +
            '<div class="pe-modal-head"><span>Настройки страницы</span>' +
            '<button type="button" class="pe-modal-close" data-close title="Закрыть">✕</button></div>' +
            '<div class="pe-modal-body">' +
            psField('Название вкладки браузера (title)', 'pe-ps-title', meta.title) +
            psField('Заголовок страницы (H1)', 'pe-ps-h1', meta.h1) +
            psField('Описание для поисковиков (meta description)', 'pe-ps-desc', meta.meta_description, true) +
            psField('Ключевые слова (meta keywords)', 'pe-ps-keywords', meta.meta_keywords) +
            psField('Canonical URL (если отличается)', 'pe-ps-canonical', meta.canonical) +
            '<div class="pe-fx-decor-head">Open Graph (соцсети)</div>' +
            psField('OG заголовок', 'pe-ps-ogtitle', s.og_title) +
            psField('OG описание', 'pe-ps-ogdesc', s.og_description, true) +
            '<div class="pe-field"><label>OG картинка</label><div class="pe-imgrow">' +
            '<button type="button" class="pe-btn-mini" id="pe-ps-ogimg-pick">🖼 Медиатека</button></div>' +
            '<input type="text" id="pe-ps-ogimage" placeholder="/static/uploads/… или https://…"></div>' +
            '<div class="pe-field"><label>Favicon страницы</label><div class="pe-imgrow">' +
            '<button type="button" class="pe-btn-mini" id="pe-ps-fav-pick">🖼 Медиатека</button></div>' +
            '<input type="text" id="pe-ps-favicon" placeholder="/static/uploads/…"></div>' +
            '<div class="pe-fx-decor-head">Оформление и поведение</div>' +
            '<div class="pe-field"><label>Цвет фона страницы</label><input type="color" id="pe-ps-pagebg"> ' +
            '<input type="text" id="pe-ps-pagebg-hex" class="pe-hex" placeholder="#ffffff"></div>' +
            '<div class="pe-field"><label>Цвет ссылок на странице</label><input type="color" id="pe-ps-linkcolor"> ' +
            '<input type="text" id="pe-ps-linkcolor-hex" class="pe-hex" placeholder="#b49d84"></div>' +
            '<div class="pe-field"><label><input type="checkbox" id="pe-ps-ovh"> Своя шапка вместо шапки сайта</label>' +
            '<label><input type="checkbox" id="pe-ps-ovf"> Свой подвал вместо подвала сайта</label></div>' +
            '<div class="pe-help">Конфигурации шапки/подвала редактируются в разделе админки «Глобальные зоны».</div>' +
            '<div class="pe-fx-decor-head">Код (только суперадмин)</div>' +
            psField('Произвольный CSS страницы', 'pe-ps-customcss', s.custom_css, true) +
            psField('Произвольный JS страницы', 'pe-ps-customjs', s.custom_js, true) +
            '<div class="pe-help">Сервер применит эти поля только для суперадмина; для остальных — 403 с пояснением.</div>' +
            '</div>' +
            '<div class="pe-modal-foot">' +
            '<button type="button" class="pe-btn-mini" data-close>Отмена</button>' +
            '<button type="button" class="pe-btn-action" id="pe-ps-save">Сохранить настройки</button>' +
            '</div></div>';
        pageSettingsOverlay.style.display = 'flex';
        const $ps = id => pageSettingsOverlay.querySelector('#' + id);
        const setv = (id, v) => { const n = $ps(id); if (n) n.value = v || ''; };
        setv('pe-ps-title', meta.title); setv('pe-ps-h1', meta.h1);
        setv('pe-ps-desc', meta.meta_description); setv('pe-ps-keywords', meta.meta_keywords);
        setv('pe-ps-canonical', meta.canonical);
        setv('pe-ps-ogtitle', s.og_title); setv('pe-ps-ogdesc', s.og_description);
        setv('pe-ps-ogimage', s.og_image); setv('pe-ps-favicon', s.favicon);
        if (s.page_bg) { $ps('pe-ps-pagebg').value = normalizeHex(s.page_bg) || '#ffffff'; $ps('pe-ps-pagebg-hex').value = s.page_bg; }
        if (s.page_link_color) { $ps('pe-ps-linkcolor').value = normalizeHex(s.page_link_color) || '#b49d84'; $ps('pe-ps-linkcolor-hex').value = s.page_link_color; }
        $ps('pe-ps-ovh').checked = !!s.override_header;
        $ps('pe-ps-ovf').checked = !!s.override_footer;
        setv('pe-ps-customcss', s.custom_css); setv('pe-ps-customjs', s.custom_js);
        const close = () => { pageSettingsOverlay.style.display = 'none'; };
        pageSettingsOverlay.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', close));
        pageSettingsOverlay.onmousedown = e => { if (e.target === pageSettingsOverlay) close(); };
        $ps('pe-ps-ogimg-pick').addEventListener('click', async () => {
            const url = await openMediaPicker($ps('pe-ps-ogimage').value);
            if (url != null) $ps('pe-ps-ogimage').value = url;
        });
        $ps('pe-ps-fav-pick').addEventListener('click', async () => {
            const url = await openMediaPicker($ps('pe-ps-favicon').value);
            if (url != null) $ps('pe-ps-favicon').value = url;
        });
        $ps('pe-ps-save').addEventListener('click', async () => {
            const gv = id => $ps(id).value;
            const payload = {
                slug: SLUG,
                meta: {
                    title: gv('pe-ps-title'), h1: gv('pe-ps-h1'),
                    meta_description: gv('pe-ps-desc'), meta_keywords: gv('pe-ps-keywords'),
                    canonical: gv('pe-ps-canonical'),
                },
                og_title: gv('pe-ps-ogtitle'), og_description: gv('pe-ps-ogdesc'),
                og_image: gv('pe-ps-ogimage').trim(), favicon: gv('pe-ps-favicon').trim(),
                page_bg: gv('pe-ps-pagebg-hex').trim(), page_link_color: gv('pe-ps-linkcolor-hex').trim(),
                override_header: $ps('pe-ps-ovh').checked, override_footer: $ps('pe-ps-ovf').checked,
                custom_css: gv('pe-ps-customcss'), custom_js: gv('pe-ps-customjs'),
            };
            try {
                const resp = await apiPost('/page-settings', payload);
                const r = await resp.json();
                if (r.success) {
                    Object.assign(pageMeta, payload.meta);
                    close();
                    toast('Настройки страницы сохранены');
                } else {
                    toast('Ошибка: ' + (r.error || ''), 'error');
                }
            } catch (e2) { toast('Ошибка сети: ' + e2.message, 'error'); }
        });
    }

    /* ==================== Диалог «Версии» (CN-T10 UI) ==================== */
    const versionsOverlay = el('div', 'pe-modal-overlay');
    versionsOverlay.style.display = 'none';

    async function openVersions() {
        versionsOverlay.innerHTML =
            '<div class="pe-modal">' +
            '<div class="pe-modal-head"><span>Версии страницы</span>' +
            '<button type="button" class="pe-modal-close" data-close title="Закрыть">✕</button></div>' +
            '<div class="pe-modal-body">' +
            '<div class="pe-imgrow" style="margin-bottom:0.7rem">' +
            '<button type="button" class="pe-btn-action" id="pe-ver-save">＋ Сохранить версию</button>' +
            '<span class="pe-help" style="margin:0">Снапшот текущего черновика (максимум 10 на страницу).</span></div>' +
            '<div id="pe-ver-list"></div>' +
            '<div class="pe-help">«Просмотр» версии недоступен: API списка версий не отдаёт блоки версии — для просмотра откатите версию в черновик.</div>' +
            '</div>' +
            '<div class="pe-modal-foot"><button type="button" class="pe-btn-mini" data-close>Закрыть</button></div></div>';
        versionsOverlay.style.display = 'flex';
        const close = () => { versionsOverlay.style.display = 'none'; };
        versionsOverlay.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', close));
        versionsOverlay.onmousedown = e => { if (e.target === versionsOverlay) close(); };
        versionsOverlay.querySelector('#pe-ver-save').addEventListener('click', async () => {
            const label = prompt('Название версии (например: «Перед сменой баннера»):', '');
            if (label === null) return;
            await saveDraft(true);
            try {
                const r = await apiPost('/page-version-save', {slug: SLUG, label}).then(x => x.json());
                if (r.success) { toast('Версия сохранена'); loadVersions(); }
                else toast('Ошибка: ' + (r.error || ''), 'error');
            } catch (e) { toast('Ошибка сети: ' + e.message, 'error'); }
        });
        async function loadVersions() {
            const list = versionsOverlay.querySelector('#pe-ver-list');
            list.innerHTML = '<div class="pe-help">Загрузка…</div>';
            try {
                const d = await apiGet(`/page-versions?slug=${encodeURIComponent(SLUG)}`);
                if (!d.success) { list.innerHTML = '<div class="pe-help">' + esc(d.error || 'Ошибка') + '</div>'; return; }
                if (!d.versions.length) { list.innerHTML = '<div class="pe-help">Версий пока нет. Сохраните первую кнопкой выше.</div>'; return; }
                list.innerHTML = '';
                d.versions.forEach(v => {
                    const row = el('div', 'pe-ver-row');
                    row.innerHTML =
                        '<div class="pe-ver-info"><div class="pe-ver-label">' + esc(v.label || 'Без названия') + '</div>' +
                        '<div class="pe-ver-meta">' + esc(fmtDateTime(v.created_at)) +
                        (v.created_by ? ' · ' + esc(v.created_by) : '') + ' · ' + v.blocks_count + ' блок(ов)</div></div>';
                    const restore = el('button', 'pe-btn-mini', {type: 'button', title: 'Откатить черновик к этой версии'});
                    restore.textContent = '↩ Откатить';
                    restore.addEventListener('click', async () => {
                        if (!confirm('Откатить черновик страницы к версии «' + (v.label || 'Без названия') + '»? Опубликованная версия не изменится.')) return;
                        try {
                            const r = await apiPost('/page-version-restore', {slug: SLUG, version_id: v.id}).then(x => x.json());
                            if (r.success) {
                                close();
                                toast('Версия восстановлена в черновик');
                                await reloadFromServer();
                            } else toast('Ошибка: ' + (r.error || ''), 'error');
                        } catch (e) { toast('Ошибка сети: ' + e.message, 'error'); }
                    });
                    row.appendChild(restore);
                    list.appendChild(row);
                });
            } catch (e) {
                list.innerHTML = '<div class="pe-help">Ошибка загрузки версий: ' + esc(e.message) + '</div>';
            }
        }
        loadVersions();
    }

    // Перезагрузка состояния редактора из серверного черновика (после отката версии)
    async function reloadFromServer() {
        const d = await apiGet(`/page-blocks?slug=${encodeURIComponent(SLUG)}`);
        if (d.success) {
            pageMeta = d.page || pageMeta;
            serverUpdatedAt = d.updated_at || serverUpdatedAt;
            hasLegacy = !!d.legacy;
        }
        const draft = await fetchDraft();
        if (draft && Array.isArray(draft.blocks)) blocks = draft.blocks;
        else if (d.success) blocks = d.blocks;
        history = [JSON.stringify(blocks)];
        historyIdx = 0;
        updateHistoryButtons();
        selectedIdx = -1;
        typoElement = null;
        renderStructure();
        renderBadges();
        if (hasLegacy) { location.reload(); return; }
        markDirty();
        await refreshPage();
        closeRightPanel();
    }

    /* ==================== Предпросмотр (CN-T11) ==================== */
    async function openPreview() {
        await saveDraft(true);
        const win = window.open('', '_blank');
        try {
            const r = await apiPost('/preview', {slug: SLUG, page: pageMeta, blocks}).then(x => x.json());
            if (!r.success) {
                if (win) win.close();
                toast('Ошибка предпросмотра: ' + (r.error || ''), 'error');
                return;
            }
            if (!win) { toast('Браузер заблокировал окно предпросмотра', 'error'); return; }
            win.document.open();
            win.document.write(r.html);
            win.document.close();
        } catch (e) {
            if (win) win.close();
            toast('Ошибка сети: ' + e.message, 'error');
        }
    }

    /* ==================== Публикация (CN-T10) ==================== */
    // Поток: черновик → page-save с expected_updated_at (конфликт 409 — как в цикле 1:
    // при расхождении updated_at спрашиваем подтверждение и повторяем без проверки)
    // → page-publish (draft_blocks → published_blocks + пересборка content_html).
    async function publish() {
        if (!confirm('Опубликовать страницу? Изменения станут видны посетителям сайта.')) return;
        if (textEdit.active) finishTextEdit(true);
        await saveDraft(true);
        try {
            let resp = await apiPost('/page-save', {
                slug: SLUG, page: pageMeta, blocks,
                expected_updated_at: serverUpdatedAt || undefined,
            });
            if (resp.status === 409) {
                if (!confirm('Страница была изменена другим администратором. Перезаписать его изменения вашей версией?')) {
                    toast('Публикация отменена: страница изменена другим админом', 'error');
                    return;
                }
                resp = await apiPost('/page-save', {slug: SLUG, page: pageMeta, blocks});
            }
            let r = await resp.json();
            if (!r.success) { toast('Ошибка сохранения: ' + (r.error || ''), 'error'); return; }
            serverUpdatedAt = r.updated_at || serverUpdatedAt;
            const pr = await apiPost('/page-publish', {slug: SLUG}).then(x => x.json());
            if (!pr.success) { toast('Ошибка публикации: ' + (pr.error || ''), 'error'); return; }
            serverUpdatedAt = pr.updated_at || serverUpdatedAt;
            publishedAt = pr.updated_at || publishedAt;
            dirty = false;
            updateStatusBadge();
            setSaveState('saved');
            toast('Страница опубликована ✓');
        } catch (e) {
            toast('Ошибка сети: ' + e.message, 'error');
        }
    }

    function exitEditor() {
        if (textEdit.active) finishTextEdit(true);
        if (zero.active) closeZero();
        if (dirty && !confirm('Есть несохранённые изменения. Выйти без сохранения?')) return;
        location.href = '/admin';
    }

    /* ==================== Клавиатура (CN-T11.2) ==================== */
    function onKeyDown(e) {
        const typing = e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT' || e.target.isContentEditable);
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            e.preventDefault();
            saveDraft(true);
            return;
        }
        // В режиме чистого просмотра остальные клавиши не работают; Esc возвращает панели
        if (document.body.classList.contains('pe-ui-hidden')) {
            if (e.key === 'Escape') setUiHidden(false);
            return;
        }
        if (zero.active && zero.onKeyDown(e)) return;
        if (e.key === 'Escape') {
            if (textEdit.active) { finishTextEdit(true); return; }
            if (zero.active) { closeZero(); return; }
            if (hotkeysHelp) { closeHotkeysHelp(); return; }
            if (document.body.classList.contains('pe-right-overlay')) { setRightCollapsed(true); return; }
            if (mediaOverlay.style.display !== 'none') { mediaOverlay.style.display = 'none'; if (mediaResolve) { mediaResolve(null); mediaResolve = null; } return; }
            if (versionsOverlay.style.display !== 'none') { versionsOverlay.style.display = 'none'; return; }
            if (pageSettingsOverlay.style.display !== 'none') { pageSettingsOverlay.style.display = 'none'; return; }
            document.querySelectorAll('.pe-modal-overlay').forEach(m => { if (!m.id) m.remove(); });
            disarmInsert();
            select(-1);
            return;
        }
        if (typing) return;
        if (e.key === '?') { toggleHotkeysHelp(); return; }
        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (selectedIdx >= 0) { e.preventDefault(); removeBlock(selectedIdx); }
            return;
        }
        if (e.ctrlKey || e.metaKey) {
            if (e.key.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
            else if ((e.key.toLowerCase() === 'z' && e.shiftKey) || e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
        }
    }

    /* Поиск по библиотеке — биндим в boot(): поле живёт в leftPanel,
       которая попадает в DOM только там. */

    /* ==================== Zero-редактор (CN-T8 UI) ==================== */
    // Формат данных — как в blocks.py zero-рендерере:
    //   block.elements: [{id, type: text|button|image|shape|video|icon,
    //                     x,y,w,h (проценты), rotate, z, opacity, hidden, link, props:{...}}]
    //   block.height — высота холста px; block.responsive: {tablet|mobile: {eid: {x,y,w,h,hide}}}
    const zero = {
        active: false, idx: -1, layer: null, float: null, handles: null,
        sel: null, container: null, locked: new Set(),
        drag: null,
    };

    function zeroBlock() { return blocks[zero.idx]; }
    function zeroElements() {
        const b = zeroBlock();
        return b && Array.isArray(b.elements) ? b.elements : [];
    }
    function zeroDevice() { return device === 'desktop' ? 'desktop' : device; }

    function effCoords(b, elm) {
        const base = {x: +elm.x || 0, y: +elm.y || 0, w: +elm.w || 20, h: +elm.h || 10};
        const dev = zeroDevice();
        if (dev !== 'desktop' && b.responsive && b.responsive[dev] && b.responsive[dev][elm.id]) {
            const ov = b.responsive[dev][elm.id];
            ['x', 'y', 'w', 'h'].forEach(k => { if (ov[k] != null) base[k] = +ov[k]; });
            if (ov.hide) base.hide = true;
        }
        return base;
    }

    function writeCoords(elm, patch) {
        const b = zeroBlock();
        const dev = zeroDevice();
        if (dev === 'desktop') {
            Object.assign(elm, patch);
            return;
        }
        // tablet/mobile: правим переопределение, посеянное текущими эффективными координатами
        if (!b.responsive || typeof b.responsive !== 'object') b.responsive = {};
        if (!b.responsive[dev] || typeof b.responsive[dev] !== 'object') b.responsive[dev] = {};
        if (!b.responsive[dev][elm.id] || typeof b.responsive[dev][elm.id] !== 'object') {
            b.responsive[dev][elm.id] = effCoords(b, elm);
        }
        Object.assign(b.responsive[dev][elm.id], patch);
    }

    function hasOverride(elm) {
        const b = zeroBlock();
        const dev = zeroDevice();
        return dev !== 'desktop' && !!(b.responsive && b.responsive[dev] && b.responsive[dev][elm.id]);
    }
    function clearOverride(elm) {
        const b = zeroBlock();
        const dev = zeroDevice();
        if (b.responsive && b.responsive[dev]) delete b.responsive[dev][elm.id];
    }

    function openZero(idx) {
        const b = blocks[idx];
        if (!b || b.type !== 'zero') return;
        const entry = entryAt(idx);
        const container = entry ? entryMatch(entry, '.cb-zero') : null;
        if (!container) { toast('Zero-холст не найден на странице — обновите блок', 'error'); return; }
        if (textEdit.active) finishTextEdit(true);
        closeZero();
        zero.active = true;
        zero.idx = idx;
        zero.container = container;
        select(idx);
        buildZeroLayer();
        highlight.style.display = 'none';
        addBtn.style.display = 'none';
        toast('Zero-редактор: перетаскивайте элементы, стрелки двигают выбранный (Shift — размер, Alt — точность)');
    }

    function closeZero() {
        if (zero.layer) zero.layer.remove();
        zero.active = false;
        zero.idx = -1;
        zero.layer = null;
        zero.float = null;
        zero.handles = null;
        zero.sel = null;
        zero.container = null;
        zero.drag = null;
        updateOverlayPositions();
    }

    function rebuildZeroOverlay() {
        if (!zero.active) return;
        const entry = entryAt(zero.idx);
        const container = entry ? entryMatch(entry, '.cb-zero') : null;
        if (!container) { closeZero(); return; }
        zero.container = container;
        buildZeroLayer();
    }

    function buildZeroLayer() {
        if (zero.layer) zero.layer.remove();
        const layer = el('div', 'pe-zero-layer');
        zero.layer = layer;
        zero.container.appendChild(layer);

        // Плавающая панель: слои, добавление элементов, закрытие
        const float = el('div', 'pe-zero-float');
        zero.float = float;
        float.innerHTML =
            '<div class="pe-zero-float-head"><span>🎯 Zero-редактор</span>' +
            '<span class="pe-zero-dev">' + (zeroDevice() === 'desktop' ? 'Desktop 1200' : zeroDevice() === 'tablet' ? 'Tablet 768' : 'Mobile 360') + '</span>' +
            '<button type="button" data-z="close" title="Закрыть (Esc)">✕</button></div>' +
            '<div class="pe-zero-add">' +
            '<span class="pe-zero-add-label">+ Элемент:</span>' +
            '<button type="button" data-add="text">Текст</button>' +
            '<button type="button" data-add="button">Кнопка</button>' +
            '<button type="button" data-add="image">Картинка</button>' +
            '<button type="button" data-add="shape">Фигура</button>' +
            '<button type="button" data-add="video">Видео</button>' +
            '<button type="button" data-add="icon">Иконка</button>' +
            '</div>' +
            '<div class="pe-zero-layers"></div>' +
            '<div class="pe-zero-props"></div>' +
            '<div class="pe-zero-hint">Двигать: drag или стрелки · Resize: углы (Shift — пропорции) · Поворот: ручка над элементом</div>';
        layer.appendChild(float);
        float.querySelector('[data-z="close"]').addEventListener('click', closeZero);
        float.querySelectorAll('[data-add]').forEach(btn => {
            btn.addEventListener('click', () => zeroAddElement(btn.dataset.add));
        });

        // Контейнер рамки выделения и ручек (pointer-events включаются на ручках)
        const handles = el('div', 'pe-zero-handles');
        zero.handles = handles;
        layer.appendChild(handles);

        bindZeroElements();
        renderZeroLayers();
        renderZeroProps();
        updateZeroSelection();
    }

    function bindZeroElements() {
        if (!zero.container) return;
        [...zero.container.querySelectorAll('[data-el]')].forEach(nodeEl => {
            nodeEl.addEventListener('pointerdown', e => {
                if (e.button !== 0) return;
                const eid = nodeEl.dataset.el;
                const elm = zeroElements().find(x => x.id === eid);
                if (!elm) return;
                e.preventDefault();
                e.stopPropagation();
                zero.sel = eid;
                renderZeroLayers();
                renderZeroProps();
                updateZeroSelection();
                if (!zero.locked.has(eid)) startZeroDrag(e, elm, nodeEl, 'move');
            });
        });
    }

    function startZeroDrag(e, elm, nodeEl, mode, handle) {
        const b = zeroBlock();
        const rect = zero.container.getBoundingClientRect();
        const start = effCoords(b, elm);
        const ratio = start.w / Math.max(1, start.h);
        zero.drag = {
            mode, handle, elm, nodeEl, rect,
            startX: e.clientX, startY: e.clientY, start,
            ratio,
            centerX: rect.left + (start.x + start.w / 2) / 100 * rect.width,
            centerY: rect.top + (start.y + start.h / 2) / 100 * rect.height,
            startAngle: Math.atan2(e.clientY - (rect.top + start.y / 100 * rect.height + start.h / 100 * rect.height / 2),
                e.clientX - (rect.left + start.x / 100 * rect.width + start.w / 100 * rect.width / 2)) * 180 / Math.PI,
        };
        // Ручки живут в документе контента (iframe) — координаты событий там же,
        // в несжатой системе iframe; document — страховка на случай отпускания
        // кнопки за пределами iframe (координаты up не используются).
        const moveDoc = (IFRAME_MODE && CD) ? CD : document;
        moveDoc.addEventListener('pointermove', onZeroDragMove);
        moveDoc.addEventListener('pointerup', onZeroDragEnd, {once: true});
        document.addEventListener('pointerup', onZeroDragEnd, {once: true});
    }

    function onZeroDragMove(e) {
        const d = zero.drag;
        if (!d) return;
        // События из родительского документа (мышь ушла за край iframe) — пропускаем:
        // координатные системы разные, промежуточные позиции не важны.
        if (IFRAME_MODE && (!CW || e.view !== CW)) return;
        e.preventDefault();
        const dx = (e.clientX - d.startX) / d.rect.width * 100;
        const dy = (e.clientY - d.startY) / d.rect.height * 100;
        const cur = Object.assign({}, d.start);
        let patch = {};
        if (d.mode === 'move') {
            patch = {x: round1(clampPct(cur.x + dx)), y: round1(clampPct(cur.y + dy))};
        } else if (d.mode === 'resize') {
            const h = d.handle;
            let {x, y, w, hh} = {x: cur.x, y: cur.y, w: cur.w, hh: cur.h};
            if (h.includes('e')) w = cur.w + dx;
            if (h.includes('s')) hh = cur.h + dy;
            if (h.includes('w')) { w = cur.w - dx; x = cur.x + dx; }
            if (h.includes('n')) { hh = cur.h - dy; y = cur.y + dy; }
            w = Math.max(1, w); hh = Math.max(1, hh);
            if (e.shiftKey && d.ratio > 0) {
                // пропорции: тянем по большей оси
                if (Math.abs(w - cur.w) > Math.abs(hh - cur.h)) hh = w / d.ratio;
                else w = hh * d.ratio;
            }
            patch = {x: round1(clampPct(x)), y: round1(clampPct(y)), w: round1(clampPct(w)), h: round1(clampPct(hh))};
        } else if (d.mode === 'rotate') {
            const ang = Math.atan2(e.clientY - d.centerY, e.clientX - d.centerX) * 180 / Math.PI;
            const rot = Math.round((d.elm.rotate || 0) + (ang - d.startAngle));
            patch = {rotate: ((rot % 360) + 360) % 360 <= 180 ? rot : rot};
        }
        // живое обновление DOM + JSON
        if (d.mode === 'rotate') {
            d.elm.rotate = patch.rotate;
            applyElStyle(d.nodeEl, effCoords(zeroBlock(), d.elm), patch.rotate);
        } else {
            writeCoords(d.elm, patch);
            applyElStyle(d.nodeEl, Object.assign({}, d.start, patch), d.elm.rotate);
        }
        updateZeroSelectionFrame();
    }

    function onZeroDragEnd() {
        const moveDoc = (IFRAME_MODE && CD) ? CD : document;
        moveDoc.removeEventListener('pointermove', onZeroDragMove);
        moveDoc.removeEventListener('pointerup', onZeroDragEnd);
        document.removeEventListener('pointerup', onZeroDragEnd);
        zero.drag = null;
        markDirty();
        recordHistoryDebounced();
        scheduleRefresh();   // после ответа rebuildZeroOverlay восстановит оверлей
    }

    function clampPct(v) { return Math.max(-50, Math.min(150, v)); }
    function round1(v) { return Math.round(v * 10) / 10; }

    function applyElStyle(nodeEl, c, rotate) {
        nodeEl.style.left = c.x + '%';
        nodeEl.style.top = c.y + '%';
        nodeEl.style.width = c.w + '%';
        nodeEl.style.height = c.h + '%';
        nodeEl.style.transform = rotate ? 'rotate(' + rotate + 'deg)' : '';
    }

    function updateZeroSelection() {
        if (!zero.handles) return;
        zero.handles.innerHTML = '';
        const b = zeroBlock();
        const elm = zeroElements().find(x => x.id === zero.sel);
        if (!elm || !zero.container) return;
        const c = effCoords(b, elm);
        const frame = el('div', 'pe-zero-frame');
        zero.handles.appendChild(frame);
        positionFrame(frame, c, elm.rotate);
        // угловые ручки resize
        ['nw', 'ne', 'sw', 'se'].forEach(h => {
            const hd = el('div', 'pe-zero-handle pe-zero-h-' + h, {title: 'Изменить размер (Shift — пропорции)'});
            hd.addEventListener('pointerdown', e => {
                e.preventDefault(); e.stopPropagation();
                startZeroDrag(e, elm, findElNode(elm.id), 'resize', h);
            });
            zero.handles.appendChild(hd);
            positionHandle(hd, c, h);
        });
        // ручка поворота
        const rot = el('div', 'pe-zero-handle pe-zero-h-rot', {title: 'Повернуть'});
        rot.textContent = '⟳';
        rot.addEventListener('pointerdown', e => {
            e.preventDefault(); e.stopPropagation();
            const rect = zero.container.getBoundingClientRect();
            zero.drag = null;
            const centerX = rect.left + (c.x + c.w / 2) / 100 * rect.width;
            const centerY = rect.top + (c.y + c.h / 2) / 100 * rect.height;
            const startAngle = Math.atan2(e.clientY - centerY, e.clientX - centerX) * 180 / Math.PI;
            const nodeEl = findElNode(elm.id);
            const startRot = +elm.rotate || 0;
            const moveDoc = (IFRAME_MODE && CD) ? CD : document;
            const move = ev => {
                if (IFRAME_MODE && (!CW || ev.view !== CW)) return;
                const ang = Math.atan2(ev.clientY - centerY, ev.clientX - centerX) * 180 / Math.PI;
                elm.rotate = Math.round(startRot + (ang - startAngle));
                applyElStyle(nodeEl, effCoords(zeroBlock(), elm), elm.rotate);
                updateZeroSelectionFrame();
            };
            const up = () => {
                moveDoc.removeEventListener('pointermove', move);
                document.removeEventListener('pointerup', up);
                markDirty(); recordHistoryDebounced(); scheduleRefresh();
            };
            moveDoc.addEventListener('pointermove', move);
            moveDoc.addEventListener('pointerup', up, {once: true});
            document.addEventListener('pointerup', up, {once: true});
        });
        zero.handles.appendChild(rot);
        positionRotHandle(rot, c, elm.rotate);
    }
    function updateZeroSelectionFrame() {
        const b = zeroBlock();
        const elm = zeroElements().find(x => x.id === zero.sel);
        if (!elm || !zero.handles) return;
        const c = effCoords(b, elm);
        const frame = zero.handles.querySelector('.pe-zero-frame');
        if (frame) positionFrame(frame, c, elm.rotate);
        ['nw', 'ne', 'sw', 'se'].forEach(h => {
            const hd = zero.handles.querySelector('.pe-zero-h-' + h);
            if (hd) positionHandle(hd, c, h);
        });
        const rot = zero.handles.querySelector('.pe-zero-h-rot');
        if (rot) positionRotHandle(rot, c, elm.rotate);
    }
    function positionFrame(frame, c, rotate) {
        frame.style.left = c.x + '%';
        frame.style.top = c.y + '%';
        frame.style.width = c.w + '%';
        frame.style.height = c.h + '%';
        frame.style.transform = rotate ? 'rotate(' + rotate + 'deg)' : '';
    }
    function positionHandle(hd, c, h) {
        const x = h.includes('w') ? c.x : c.x + c.w;
        const y = h.includes('n') ? c.y : c.y + c.h;
        hd.style.left = x + '%';
        hd.style.top = y + '%';
    }
    function positionRotHandle(hd, c, rotate) {
        hd.style.left = (c.x + c.w / 2) + '%';
        hd.style.top = c.y + '%';
    }
    function findElNode(eid) {
        return zero.container ? zero.container.querySelector('[data-el="' + eid + '"]') : null;
    }

    function zeroAddElement(type) {
        const b = zeroBlock();
        if (!Array.isArray(b.elements)) b.elements = [];
        const id = 'el' + Date.now().toString(36);
        const base = {id, type, x: 10, y: 10, w: 30, h: 15};
        const defaults = {
            text: {props: {text: 'Новый текст'}},
            button: {props: {text: 'Кнопка', url: '/'}},
            image: {props: {src: '', alt: ''}},
            shape: {props: {color: '#b49d84', shape: 'rect'}},
            video: {props: {url: ''}},
            icon: {props: {icon: '★', color: '#333333', size: 32}},
        };
        const elm = Object.assign(base, defaults[type] || {});
        b.elements.push(elm);
        zero.sel = id;
        markDirty();
        recordHistory();
        scheduleRefresh();
    }

    function renderZeroLayers() {
        if (!zero.float) return;
        const box = zero.float.querySelector('.pe-zero-layers');
        box.innerHTML = '';
        const els = zeroElements().slice().reverse();   // верхние слои первыми
        if (!els.length) { box.innerHTML = '<div class="pe-help">Пусто — добавьте элемент кнопками выше.</div>'; return; }
        els.forEach(elm => {
            const row = el('div', 'pe-zero-layer-row' + (elm.id === zero.sel ? ' pe-zero-layer-sel' : '') + (elm.hidden ? ' pe-zero-layer-hidden' : ''));
            const typeName = {text: 'Текст', button: 'Кнопка', image: 'Картинка', shape: 'Фигура', video: 'Видео', icon: 'Иконка'}[elm.type] || elm.type;
            row.innerHTML = '<span class="pe-zero-layer-name">' + esc(typeName) + ' <em>' + esc(elm.id) + '</em></span>' +
                '<span class="pe-zero-layer-actions">' +
                '<button type="button" data-la="up" title="Выше слоем (z-index +1)">▲</button>' +
                '<button type="button" data-la="down" title="Ниже слоем (z-index −1)">▼</button>' +
                '<button type="button" data-la="lock" title="Заблокировать от перетаскивания">' + (zero.locked.has(elm.id) ? '🔒' : '🔓') + '</button>' +
                '<button type="button" data-la="hide" title="Скрыть/показать элемент">' + (elm.hidden ? '👁' : '🚫') + '</button>' +
                '<button type="button" data-la="del" title="Удалить элемент">🗑</button>' +
                '</span>';
            row.querySelector('.pe-zero-layer-name').addEventListener('click', () => {
                zero.sel = elm.id;
                renderZeroLayers();
                renderZeroProps();
                updateZeroSelection();
            });
            row.querySelectorAll('[data-la]').forEach(btn => btn.addEventListener('click', e => {
                e.stopPropagation();
                const act = btn.dataset.la;
                if (act === 'up') elm.z = (+elm.z || 0) + 1;
                if (act === 'down') elm.z = (+elm.z || 0) - 1;
                if (act === 'lock') {
                    if (zero.locked.has(elm.id)) zero.locked.delete(elm.id);
                    else zero.locked.add(elm.id);
                }
                if (act === 'hide') elm.hidden = !elm.hidden;
                if (act === 'del') {
                    if (!confirm('Удалить элемент «' + elm.id + '»?')) return;
                    const arr = zeroBlock().elements;
                    arr.splice(arr.indexOf(elm), 1);
                    if (zero.sel === elm.id) zero.sel = null;
                }
                markDirty();
                recordHistoryDebounced();
                scheduleRefresh();
            }));
            box.appendChild(row);
        });
    }

    function renderZeroProps() {
        if (!zero.float) return;
        const box = zero.float.querySelector('.pe-zero-props');
        box.innerHTML = '';
        const b = zeroBlock();
        const elm = zeroElements().find(x => x.id === zero.sel);
        if (!elm) return;
        const commit = () => { markDirty(); recordHistoryDebounced(); scheduleRefresh(); };
        const props = elm.props || (elm.props = {});
        const setP = (k, v) => { if (v === '' || v == null) delete props[k]; else props[k] = v; commit(); };

        // Наследование координат от desktop для tablet/mobile
        if (zeroDevice() !== 'desktop') {
            if (hasOverride(elm)) {
                const inh = el('button', 'pe-btn-mini', {type: 'button', title: 'Удалить переопределение координат для ' + zeroDevice()});
                inh.textContent = 'Наследовать от desktop';
                inh.addEventListener('click', () => { clearOverride(elm); commit(); renderZeroProps(); updateZeroSelection(); });
                box.appendChild(inh);
            } else {
                box.appendChild(el('div', 'pe-help')).textContent = 'Координаты наследуются от desktop — сдвиньте элемент, чтобы задать свои для ' + zeroDevice() + '.';
            }
        }

        if (elm.type === 'text') {
            box.appendChild(field('Текст', textareaInput(props.text, v => setP('text', v), 2)));
            const ts = props.text_style || (props.text_style = {});
            const setT = (k, v) => { if (v === '' || v == null) delete ts[k]; else ts[k] = v; commit(); };
            box.appendChild(field('Цвет текста', colorInput(ts.color, v => setT('color', v))));
            box.appendChild(field('Размер шрифта', textInput(ts.size, v => setT('size', v), '32px или 2rem')));
        } else if (elm.type === 'button') {
            box.appendChild(field('Текст кнопки', textInput(props.text, v => setP('text', v))));
            box.appendChild(field('Ссылка', textInput(props.url, v => setP('url', v), '/catalog/')));
            const bs = props.button_style || (props.button_style = {});
            box.appendChild(field('Стиль', selectInput([
                ['fill', 'Заливка'], ['outline', 'Обводка'], ['none', 'Без фона'],
            ], bs.variant || 'fill', v => { if (v === 'fill') delete bs.variant; else bs.variant = v; commit(); })));
            box.appendChild(field('Цвет', colorInput(bs.bg, v => { bs.bg = v; commit(); })));
        } else if (elm.type === 'image') {
            const f = imageField('Картинка', props, 'src', (k, v) => setP(k, v));
            box.appendChild(f);
            box.appendChild(field('Alt', textInput(props.alt, v => setP('alt', v))));
            box.appendChild(field('Скругление углов', rangeInput(props.radius == null ? 0 : props.radius,
                v => setP('radius', v || ''), {min: 0, max: 60, suffix: 'px'})));
        } else if (elm.type === 'shape') {
            box.appendChild(field('Цвет', colorInput(props.color, v => setP('color', v))));
            box.appendChild(field('Форма', selectInput([
                ['rect', 'Прямоугольник'], ['circle', 'Круг'], ['line', 'Линия'],
            ], props.shape || 'rect', v => setP('shape', v))));
        } else if (elm.type === 'video') {
            box.appendChild(field('Ссылка YouTube/Vimeo/RuTube', textInput(props.url, v => setP('url', v), 'https://youtube.com/watch?v=…')));
        } else if (elm.type === 'icon') {
            box.appendChild(field('Иконка: набор (icon:star) или эмодзи', textInput(props.icon, v => setP('icon', v), '★'), 'Встроенный набор: icon:truck, icon:shield, icon:percent, icon:chat, icon:star, icon:heart, icon:phone, icon:mail, icon:check, icon:arrow-right, icon:box, icon:gift, icon:clock, icon:map-pin, icon:wallet, icon:award, icon:refresh, icon:factory, icon:leaf. Иконки набора принимают цвет элемента.'));
            box.appendChild(field('Цвет', colorInput(props.color, v => setP('color', v))));
            box.appendChild(field('Размер, px', numberInput(props.size || 32, v => setP('size', v), 4, 200)));
        }
        // Общие: ссылка на весь элемент, прозрачность, z-index
        box.appendChild(field('Ссылка при клике (необязательно)', textInput(elm.link, v => {
            if (v && !/^(https?:\/\/|mailto:|tel:|\/|#)/i.test(v)) { toast('Ссылка не сохранена: разрешены http(s), mailto:, tel:, / и #'); return; }
            if (v) elm.link = v; else delete elm.link;
            commit();
        }, 'https://… или /catalog/')));
        box.appendChild(field('Прозрачность', rangeInput(elm.opacity == null ? 100 : elm.opacity,
            v => { if (v >= 100) delete elm.opacity; else elm.opacity = v; commit(); }, {min: 10, max: 100, suffix: '%'})));
        box.appendChild(field('Слой (z-index)', numberInput(elm.z || 0, v => { elm.z = v; commit(); }, 0, 100)));
    }

    // Стрелки: двигают выбранный элемент (Shift — размер, Alt — шаг 0.1%)
    zero.onKeyDown = function (e) {
        if (!zero.active || !zero.sel) return false;
        const arrows = {ArrowLeft: ['x', -1], ArrowRight: ['x', 1], ArrowUp: ['y', -1], ArrowDown: ['y', 1]};
        if (!arrows[e.key]) return false;
        const elm = zeroElements().find(x => x.id === zero.sel);
        if (!elm || zero.locked.has(elm.id)) return false;
        e.preventDefault();
        const step = e.altKey ? 0.1 : 1;
        const [axis, dir] = arrows[e.key];
        const cur = effCoords(zeroBlock(), elm);
        const patch = {};
        if (e.shiftKey) {
            if (axis === 'x') patch.w = round1(Math.max(1, cur.w + dir * step));
            else patch.h = round1(Math.max(1, cur.h + dir * step));
        } else {
            patch[axis] = round1(clampPct(cur[axis] + dir * step));
        }
        writeCoords(elm, patch);
        const nodeEl = findElNode(elm.id);
        if (nodeEl) applyElStyle(nodeEl, effCoords(zeroBlock(), elm), elm.rotate);
        updateZeroSelectionFrame();
        markDirty();
        recordHistoryDebounced();
        scheduleRefresh();
        return true;
    };

    // После перерендера холста — восстановить оверлей (вызывается из buildMap-цепочки)
    const origBuildMap = buildMap;
    buildMap = function () {
        origBuildMap();
        if (zero.active) rebuildZeroOverlay();
    };
})();
