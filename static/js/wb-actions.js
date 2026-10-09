/* Действия карточек товаров: корзина, избранное, «купить в 1 клик».
 * Обработчики — делегированные (на document), поэтому работают для любых
 * карточек независимо от момента их появления в DOM. Файл может подключаться
 * несколькими шаблонами — глобальный флаг защищает от двойного навешивания.
 *
 * Помимо собственных .wb-* кнопок перехватываются «мёртвые» кнопки клона
 * aspro-шаблона: сердечко .js-item-action[data-action="favorite» и
 * .one_click («Купить в 1 клик»). Их обработчики шлют ajax на несуществующие
 * bitrix-эндпоинты (405 → тост «Ошибка. Error network connection»), поэтому
 * клик перехватывается в capture-фазе — jQuery-делегированные обработчики
 * aspro живут в bubble-фазе на document и до них событие не доходит. */
(function() {
    if (window.__wbActions) return;
    window.__wbActions = true;

    /* id товара из статичной aspro-карточки: ссылка на товар оканчивается
     * артикулом вида …-677984451/. */
    function staticCard(el) {
        return el.closest('.catalog_item_wrapp, .item_block, .catalog-block__item, .table-view__item, .product-main, .product-container');
    }

    function staticCardProductId(el) {
        var card = staticCard(el) || el.parentElement;
        if (!card) return '';
        var links = card.querySelectorAll('a[href]');
        for (var i = 0; i < links.length; i++) {
            var m = (links[i].pathname || '').match(/(\d+)\/?$/);
            if (m) return m[1];
        }
        return '';
    }

    function staticCardName(el) {
        var card = staticCard(el);
        if (!card) return '';
        var t = card.querySelector('.item-title, .product-title, h1');
        return t ? t.textContent.trim().slice(0, 200) : '';
    }

    /* Актуальная цена статичной aspro-карточки (data-value на .price). */
    function staticCardPrice(el) {
        var card = staticCard(el);
        if (!card) return 0;
        var p = card.querySelector('.cost .price[data-value]:not(.discount)')
             || card.querySelector('.cost .price[data-value]');
        if (!p) return 0;
        var v = parseFloat(p.getAttribute('data-value'));
        return isNaN(v) ? 0 : Math.round(v);
    }

    /* Счётчик «Избранные товары» в шапке (aspro-разметка). */
    function bumpFavoriteCounter() {
        document.querySelectorAll('a.basket-link.delay .js-count').forEach(function(c) {
            c.textContent = String((parseInt(c.textContent, 10) || 0) + 1);
            c.classList.remove('empted');
        });
    }

    function likeProduct(btn, productId, productName) {
        fetch('/api/like', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({product_id: productId, product_name: productName || ''})})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.success) return;
                if (btn.classList.contains('wb-like-btn')) {
                    btn.textContent = '❤ В избранном (' + data.likes + ')';
                    btn.style.borderColor = '#999';
                    btn.style.color = '#999';
                } else {
                    /* aspro-кнопка сердечка */
                    btn.title = 'В избранном';
                    btn.setAttribute('data-title', 'В избранном');
                    btn.classList.add('added', 'wb-liked');
                }
                bumpFavoriteCounter();
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
            buyOneClick(buyBtn.dataset.id, buyBtn.dataset.source);
            return;
        }

        var cartBtn = e.target.closest && e.target.closest('.wb-cart-btn');
        if (cartBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (cartBtn.disabled) return;
            fetch('/api/cart/add', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({product_id: cartBtn.dataset.id, source: cartBtn.dataset.source || 'wb'})})
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.success) {
                        cartBtn.textContent = '✓ В корзине (' + data.count + ')';
                        setTimeout(function() { location.href = '/basket/'; }, 500);
                    }
                })
                .catch(function(err) { console.error('cart error', err); });
            return;
        }

        var likeBtn = e.target.closest && e.target.closest('.wb-like-btn');
        if (likeBtn) {
            e.preventDefault();
            e.stopPropagation();
            likeProduct(likeBtn, likeBtn.dataset.id, likeBtn.dataset.name);
        }
    });

    /* --- мёртвые aspro-кнопки (capture-фаза, раньше jQuery-обработчиков) --- */
    document.addEventListener('click', function(e) {
        var fav = e.target.closest && e.target.closest('.js-item-action');
        if (fav && fav.getAttribute('data-action') === 'favorite') {
            e.preventDefault();
            e.stopPropagation();
            var pid = staticCardProductId(fav);
            if (pid) likeProduct(fav, pid, staticCardName(fav));
            return;
        }
        var oc = e.target.closest && e.target.closest('.one_click');
        if (oc) {
            e.preventDefault();
            e.stopPropagation();
            var pid2 = staticCardProductId(oc);
            if (pid2) buyOneClick(pid2, 'wb', staticCardName(oc), staticCardPrice(oc));
        }
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
})();
