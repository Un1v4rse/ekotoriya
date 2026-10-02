/* Действия WB-карточек: корзина, лайк, заявка.
 * Обработчики — делегированные (на document), поэтому работают для любых
 * карточек независимо от момента их появления в DOM и от того, когда
 * догрузился этот файл (раньше была гонка: клик раньше ~2 с уводил на
 * страницу товара вместо добавления в корзину). */
(function() {
    var delegated = false;

    function delegate() {
        if (delegated) return;
        delegated = true;

        document.addEventListener('click', function(e) {
            var buyBtn = e.target.closest && e.target.closest('.wb-buy-btn');
            if (buyBtn) {
                e.preventDefault();
                e.stopPropagation();
                if (buyBtn.dataset.stock === '0' || buyBtn.disabled) return;
                var pid = buyBtn.dataset.id;
                var form = document.querySelector('.wb-order-form[data-id="' + pid + '"]');
                if (form) form.style.display = form.style.display === 'none' ? 'block' : 'none';
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
                fetch('/api/like', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({product_id: likeBtn.dataset.id, product_name: likeBtn.dataset.name || ''})})
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.success) {
                            likeBtn.textContent = '❤ В избранном (' + data.likes + ')';
                            likeBtn.style.borderColor = '#999';
                            likeBtn.style.color = '#999';
                        }
                    })
                    .catch(function(err) { console.error('like error', err); });
            }
        });

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
    }

    /* Совместимость: динамические блоки (например, «Популярные товары»)
     * вызывают initWbActions(container) после вставки разметки.
     * С делегированием ничего привязывать не нужно — функция остаётся
     * как безопасный no-op. */
    window.initWbActions = function() {};

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', delegate);
    } else {
        delegate();
    }
})();
