/* Действия карточек товаров: избранное и «купить в 1 клик».
 * Обработчики — делегированные (на document), поэтому работают для любых
 * карточек независимо от момента их появления в DOM. Файл может подключаться
 * несколькими шаблонами — глобальный флаг защищает от двойного навешивания.
 *
 * Перехват «В корзину» (.wb-cart-btn и мёртвые aspro .to-cart) здесь НЕ
 * живёт: он вынесен INLINE в HTML (инжект app.py, CART_INTERCEPT_JS) — у
 * части клиентов внешние JS-файлы приходят обрезанными в конце (прокси/DPI
 * портят gzip-поток), обрезанный файл это SyntaxError, и весь файл молча не
 * выполняется. HTML-страницы отдаются без gzip и доходят целиком. Флаг
 * window.__wbCartIntercept выставляет инлайн-скрипт; здесь он не нужен. */
(function() {
    if (window.__wbActions) return;
    window.__wbActions = true;

    /* id товара из статичной aspro-карточки: ссылка на товар оканчивается
     * артикулом вида …-677984451/. Фолбэки: data-id карточки → ссылка
     * /catalog/<источник>/<id>/ → последовательность ≥6 цифр в ссылке
     * (nm_id WB) → любые цифры на конце URL. */
    function staticCard(el) {
        return el.closest('.catalog_item_wrapp, .item_block, .catalog-block__item, .table-view__item, .product-main, .product-container, .lk-product');
    }

    function staticCardProductId(el) {
        var card = staticCard(el) || el.parentElement;
        if (!card) return '';
        var own = card.getAttribute('data-id') || card.getAttribute('data-product-id') || '';
        if (own && /^\d+$/.test(own)) return own;
        var links = card.querySelectorAll('a[href]');
        var i, m;
        for (i = 0; i < links.length; i++) {
            m = (links[i].pathname || '').match(/\/catalog\/[a-z]+\/(\d+)\/?$/i);
            if (m) return m[1];
        }
        for (i = 0; i < links.length; i++) {
            m = (links[i].pathname || '').match(/(\d{6,})\/?$/);
            if (m) return m[1];
        }
        for (i = 0; i < links.length; i++) {
            m = (links[i].pathname || '').match(/(\d+)\/?$/);
            if (m) return m[1];
        }
        return '';
    }

    function staticCardName(el) {
        var card = staticCard(el);
        if (!card) return '';
        var sels = ['.item-title', '.product-title', 'h1', '.lk-product .name'];
        for (var i = 0; i < sels.length; i++) {
            var nodes = card.querySelectorAll(sels[i]);
            for (var j = 0; j < nodes.length; j++) {
                var txt = nodes[j].textContent.trim();
                if (txt) return txt.slice(0, 200);
            }
        }
        var dn = card.querySelector('[data-name]');
        if (!dn) return '';
        return (dn.getAttribute('data-name') || dn.textContent.trim()).slice(0, 200);
    }

    /* Фото статичной карточки (для снапшота лайка). */
    function staticCardPhoto(el) {
        var card = staticCard(el);
        if (!card) return '';
        var img = card.querySelector('.image_wrapper_block img, .product-detail-gallery img, img');
        return img ? (img.getAttribute('src') || '') : '';
    }

    /* Ссылка статичной карточки. */
    function staticCardUrl(el) {
        var card = staticCard(el);
        if (!card) return '';
        var a = card.querySelector('.item-title a, a.item-title, a[href*="/catalog/"]');
        return a ? a.getAttribute('href') : '';
    }

    /* Актуальная цена статичной карточки (data-value на .price, иначе
     * текст «6 000 ₽»). */
    function staticCardPrice(el) {
        var card = staticCard(el);
        if (!card) return 0;
        var p = card.querySelector('.cost .price[data-value]:not(.discount)')
             || card.querySelector('.cost .price[data-value]')
             || card.querySelector('[data-value]');
        if (p) {
            var v = parseFloat(p.getAttribute('data-value'));
            if (!isNaN(v)) return Math.round(v);
        }
        var t = card.querySelector('.lk-product .price, .price');
        if (t) {
            var digits = parseFloat(t.textContent.replace(/[^\d.]/g, ''));
            if (!isNaN(digits)) return Math.round(digits);
        }
        return 0;
    }

    /* Счётчик «Избранные товары» в шапке (aspro-разметка). */
    function currentFavoriteCount() {
        return (typeof window.__likesCount === 'number') ? window.__likesCount : 0;
    }

    function setFavoriteCounter(n) {
        window.__likesCount = n;
        document.querySelectorAll('a.basket-link.delay .js-count').forEach(function(c) {
            c.textContent = String(n);
            c.classList.toggle('empted', n === 0);
        });
    }

    /* Разметить/снять разметку ВСЕХ кнопок избранного товара на странице
     * (у одного товара может быть несколько карточек). Состояние — только
     * CSS-класс .wb-liked-btn (цвета в LIKED_BTN_CSS, инжект app.py):
     * inline-стили затирали цвета темы и не восстанавливались. */
    function markProductLiked(pid, liked, likes) {
        document.querySelectorAll('.wb-like-btn[data-id="' + pid + '"]').forEach(function(b) {
            b.textContent = liked ? '❤ В избранном' + (typeof likes === 'number' ? ' (' + likes + ')' : '') : '❤ В избранное';
            b.classList.toggle('wb-liked-btn', liked);
        });
        document.querySelectorAll('.js-item-action[data-action="favorite"]').forEach(function(b) {
            if (staticCardProductId(b) !== String(pid)) return;
            b.title = liked ? 'В избранном' : 'В избранное';
            b.setAttribute('data-title', liked ? 'В избранном' : 'В избранное');
            b.classList.toggle('added', liked);
            b.classList.toggle('wb-liked', liked);
        });
    }

    /* Лайк — toggle: повторный клик снимает лайк. Состояние меняем
     * оптимистично в момент клика (иначе два быстрых клика по разным
     * товарам затирают __likedIds ответами друг друга), ответ сервера
     * только сверяет итог. snapshot: цена/фото/ссылка товара. */
    function likeProduct(btn, productId, productName, snapshot) {
        var pid = String(productId);
        var likedIds = (window.__likedIds || []).map(String);
        var idx = likedIds.indexOf(pid);
        if (idx === -1) {
            window.__likedIds = likedIds.concat([pid]);
            markProductLiked(pid, true);
            setFavoriteCounter(currentFavoriteCount() + 1);
        } else {
            window.__likedIds = likedIds.filter(function(v) { return v !== pid; });
            markProductLiked(pid, false);
            setFavoriteCounter(currentFavoriteCount() - 1);
        }
        var body = {product_id: productId, product_name: productName || ''};
        if (snapshot) {
            body.price = snapshot.price || 0;
            body.photo = snapshot.photo || '';
            body.url = snapshot.url || '';
            body.source = snapshot.source || '';
        }
        fetch('/api/like', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.success) return;
                /* сверка с сервером (защита от рассинхрона/перекрывшихся запросов) */
                var now = data.liked !== false;
                var cur = (window.__likedIds || []).map(String);
                var has = cur.indexOf(pid) !== -1;
                if (now && !has) {
                    window.__likedIds = cur.concat([pid]);
                    setFavoriteCounter(currentFavoriteCount() + 1);
                } else if (!now && has) {
                    window.__likedIds = cur.filter(function(v) { return v !== pid; });
                    setFavoriteCounter(currentFavoriteCount() - 1);
                }
                markProductLiked(pid, now, data.likes);
            })
            .catch(function(err) { console.error('like error', err); });
    }

    function buyOneClick(productId, source, productName, price) {
        fetch('/api/cart/buy-one-click', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({product_id: productId, source: source || 'wb', product_name: productName || '', price: price || 0})})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) location.href = data.redirect || '/basket/?oneclick=1';
            })
            .catch(function(err) { console.error('buy one click error', err); });
    }

    /* --- собственные .wb-* кнопки (bubble-фаза) --- */
    document.addEventListener('click', function(e) {
        var buyBtn = e.target.closest && e.target.closest('.wb-buy-btn');
        if (buyBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (buyBtn.dataset.stock === '0' || buyBtn.disabled) return;
            /* «Купить в 1 клик»: только этот товар в корзине → оформление. */
            var pid = buyBtn.dataset.id || staticCardProductId(buyBtn);
            if (!pid) return;
            var card = buyBtn.closest('.wb-product-card');
            var nameEl = card && card.querySelector('[data-name]');
            buyOneClick(pid, buyBtn.dataset.source || 'wb',
                buyBtn.dataset.name || (nameEl ? nameEl.dataset.name : '') || staticCardName(buyBtn),
                parseInt(buyBtn.dataset.price, 10) || staticCardPrice(buyBtn));
            return;
        }

        var cartBtn = e.target.closest && e.target.closest('.wb-cart-btn');
        if (cartBtn) {
            /* Корзина обрабатывается инлайн-перехватом (CART_INTERCEPT_JS
             * в app.py): внешний файл может прийти обрезанным и не
             * выполниться, инлайн в HTML отрабатывает всегда. */
            return;
        }

        var likeBtn = e.target.closest && e.target.closest('.wb-like-btn');
        if (likeBtn) {
            e.preventDefault();
            e.stopPropagation();
            likeProduct(likeBtn, likeBtn.dataset.id, likeBtn.dataset.name, {
                price: parseInt(likeBtn.dataset.price, 10) || 0,
                photo: likeBtn.dataset.photo || '',
                url: likeBtn.dataset.url || '',
                source: likeBtn.dataset.source || ''
            });
        }
    });

    /* --- мёртвые aspro-кнопки (capture-фаза, раньше jQuery-обработчиков) --- */
    document.addEventListener('click', function(e) {
        var fav = e.target.closest && e.target.closest('.js-item-action');
        if (fav && fav.getAttribute('data-action') === 'favorite') {
            e.preventDefault();
            e.stopPropagation();
            var pid = staticCardProductId(fav) || fav.getAttribute('data-item');
            if (pid) likeProduct(fav, pid, staticCardName(fav), {
                price: staticCardPrice(fav),
                photo: staticCardPhoto(fav),
                url: staticCardUrl(fav),
                source: ''
            });
            return;
        }
        var oc = e.target.closest && e.target.closest('.one_click');
        if (oc) {
            e.preventDefault();
            e.stopPropagation();
            var pid2 = staticCardProductId(oc) || oc.getAttribute('data-item');
            if (pid2) buyOneClick(pid2, 'wb', staticCardName(oc), staticCardPrice(oc));
            return;
        }
        /* «В корзину» (.to-cart) — см. CART_INTERCEPT_JS (инлайн в app.py):
         * перехват там, этот файл может не выполниться у части клиентов. */
    }, true);

    /* Инлайн-форма заявки .wb-order-form — прежнее поведение. */
    document.addEventListener('submit', function(e) {
        var form = e.target.closest && e.target.closest('.wb-order-form');
        if (!form) return;
        e.preventDefault();
        var result = form.querySelector('.wb-order-result');
        var fd = new FormData(form);
        fd.append('product_id', form.dataset.id);
        var card = form.closest('.wb-product-card');
        if (card) {
            var nameEl = card.querySelector('[data-name]');
            var btn = card.querySelector('.wb-buy-btn');
            if (nameEl) fd.append('product_name', nameEl.dataset.name || '');
            if (btn) fd.append('price', btn.dataset.price || 0);
        }
        var body = Object.fromEntries(fd.entries());
        fetch('/api/order', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                result.textContent = data.success ? 'Заявка принята! Мы свяжемся с вами.' : 'Ошибка: ' + (data.error || '');
                result.style.color = data.success ? '#137333' : '#c00';
                if (data.success) form.reset();
            })
            .catch(function(err) {
                result.textContent = 'Ошибка сети: ' + err.message;
                result.style.color = '#c00';
            });
    });

    /* Совместимость: динамические блоки (например, «Популярные товары»)
     * вызывают initWbActions(container) после вставки разметки.
     * С делегированием ничего привязывать не нужно — функция остаётся
     * как безопасный no-op. */
    window.initWbActions = function() {};

    /* Персистентность избранного: сервер размечает лайкнутые id посетителя
     * (window.__likedIds) и их количество (__likesCount) — восстанавливаем
     * состояние кнопок и счётчика в шапке после перезагрузки страницы. */
    function applyLikedState() {
        var likedRaw = (window.__likedIds || []).map(String);
        window.__likedIds = likedRaw.filter(function(v, i, a) { return a.indexOf(v) === i; });
        likedRaw.forEach(function(pid) { markProductLiked(pid, true); });
        if (typeof window.__likesCount === 'number') {
            setFavoriteCounter(window.__likesCount);
        }
    }

    /* На странице избранного (/personal/favorite/) у карточек нет кнопок
     * покупки — добавляем свою «Купить в 1 клик» в стиле wb-buy-btn. */
    function addFavoriteBuyButtons() {
        document.querySelectorAll('.lk-product').forEach(function(card) {
            if (card.querySelector('.wb-buy-btn, .wb-fav-buy')) return;
            var pid = staticCardProductId(card);
            if (!pid) return;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'wb-fav-buy';
            btn.textContent = 'Купить в 1 клик';
            btn.setAttribute('style', 'margin: 0 0.8rem 0.8rem; width: calc(100% - 1.6rem); background: #b49d84; color: #fff; border: 0; padding: 0.55rem; border-radius: 6px; cursor: pointer; font-weight: 600;');
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                buyOneClick(pid, 'wb', staticCardName(card), staticCardPrice(card));
            });
            card.appendChild(btn);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            applyLikedState();
            addFavoriteBuyButtons();
        });
    } else {
        applyLikedState();
        addFavoriteBuyButtons();
    }
    /* Aspro при загрузке перезаписывает счётчики шапки из захардкоженного
     * arBasketAspro (FAVORITE.COUNT='0') — применяем состояние повторно
     * после полной загрузки и с небольшой задержкой. Функция идемпотентна. */
    window.addEventListener('load', function() {
        applyLikedState();
        addFavoriteBuyButtons();
    });
    setTimeout(function() {
        applyLikedState();
        addFavoriteBuyButtons();
    }, 1200);
})();
