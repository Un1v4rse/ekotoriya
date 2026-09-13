(function() {
    function initWbActions(container) {
        if (!container) container = document;
        container.querySelectorAll('.wb-buy-btn').forEach(btn => {
            if (btn.dataset.wbBound) return;
            btn.dataset.wbBound = '1';
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                const pid = this.dataset.id;
                const form = container.querySelector('.wb-order-form[data-id="' + pid + '"]');
                if (form) {
                    form.style.display = form.style.display === 'none' ? 'block' : 'none';
                }
            });
        });
        container.querySelectorAll('.wb-order-form').forEach(form => {
            if (form.dataset.wbBound) return;
            form.dataset.wbBound = '1';
            form.addEventListener('submit', async function(e) {
                e.preventDefault();
                const result = this.querySelector('.wb-order-result');
                const fd = new FormData(this);
                fd.append('product_id', this.dataset.id);
                const card = this.closest('.wb-product-card');
                if (card) {
                    const nameEl = card.querySelector('[data-name]');
                    const btn = card.querySelector('.wb-buy-btn');
                    if (nameEl) fd.append('product_name', nameEl.dataset.name || '');
                    if (btn) fd.append('price', btn.dataset.price || 0);
                }
                const body = Object.fromEntries(fd.entries());
                try {
                    const resp = await fetch('/api/order', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                    const data = await resp.json();
                    result.textContent = data.success ? 'Заявка принята! Мы свяжемся с вами.' : 'Ошибка: ' + (data.error || '');
                    result.style.color = data.success ? '#137333' : '#c00';
                    if (data.success) this.reset();
                } catch (err) {
                    result.textContent = 'Ошибка сети: ' + err.message;
                    result.style.color = '#c00';
                }
            });
        });
        container.querySelectorAll('.wb-like-btn').forEach(btn => {
            if (btn.dataset.wbBound) return;
            btn.dataset.wbBound = '1';
            btn.addEventListener('click', async function(e) {
                e.preventDefault();
                e.stopPropagation();
                const pid = this.dataset.id;
                const name = this.dataset.name || '';
                try {
                    const resp = await fetch('/api/like', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({product_id: pid, product_name: name})});
                    const data = await resp.json();
                    if (data.success) {
                        this.textContent = '❤ В избранном (' + data.likes + ')';
                        this.style.borderColor = '#999';
                        this.style.color = '#999';
                    }
                } catch (err) {
                    console.error('like error', err);
                }
            });
        });
    }
    window.initWbActions = initWbActions;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => initWbActions(document));
    } else {
        initWbActions(document);
    }
})();
