<div class="include-catalog-tab-wrap" style="max-width:1400px;margin:0 auto;padding:2.5rem 1rem;">
  <div class="maxwidth-theme">
    <h2 style="font-size:1.6rem;font-weight:600;margin:0 0 1.25rem;">Каталог товаров</h2>
    <div class="catalog-tab-buttons" style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1.5rem;">
      <button type="button" class="catalog-tab-btn active" data-tab="all" style="padding:0.55rem 1.25rem;border-radius:20px;border:1px solid #b49d84;background:#b49d84;color:#fff;font-weight:600;cursor:pointer;">Все товары</button>
      <button type="button" class="catalog-tab-btn" data-tab="discount" style="padding:0.55rem 1.25rem;border-radius:20px;border:1px solid #b49d84;background:#fff;color:#b49d84;font-weight:600;cursor:pointer;">Со скидкой</button>
      <button type="button" class="catalog-tab-btn" data-tab="stock" style="padding:0.55rem 1.25rem;border-radius:20px;border:1px solid #b49d84;background:#fff;color:#b49d84;font-weight:600;cursor:pointer;">В наличии</button>
    </div>
    <div id="catalog-tab-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:1rem;"></div>
  </div>
</div>
<script>
(function() {
    var grid = document.getElementById('catalog-tab-grid');
    if (!grid) return;
    var allProducts = [];

    function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }

    function cardHtml(p) {
        var photo = p.photo || '';
        var src = photo.charAt(0) === '/' ? photo : '/proxy/image?url=' + encodeURIComponent(photo);
        var finalPrice = p.final_price || p.price;
        var priceHtml = (p.discount_price && p.discount_price < p.price) || (finalPrice < p.price)
            ? '<span style="color:#999;text-decoration:line-through;font-size:0.85rem;">' + p.price + ' ₽</span> <span style="font-weight:700;font-size:1.05rem;color:#c00;">' + finalPrice + ' ₽</span>'
            : '<span style="font-weight:700;font-size:1.05rem;">' + finalPrice + ' ₽</span>';
        var stock = (p.stock == null) ? 10 : p.stock;
        var stockHtml = stock <= 0
            ? '<div style="margin-top:0.5rem;font-size:0.82rem;font-weight:600;color:#c00;">Нет в наличии</div>'
            : '<div style="margin-top:0.5rem;font-size:0.82rem;font-weight:600;color:#137333;">В наличии</div>';
        return '<div class="wb-product-card" style="border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;background:#fff;">'
            + '<a href="' + esc(p.url || '#') + '" target="_blank" rel="noopener" style="text-decoration:none;color:inherit;">'
            + '<div style="padding:1rem;background:#f9f9f9;"><img src="' + esc(src) + '" alt="" loading="lazy" style="width:100%;height:180px;object-fit:contain;display:block;"></div>'
            + '<div style="padding:1rem;"><div style="font-weight:600;font-size:0.9rem;line-height:1.35;min-height:3.6em;overflow:hidden;">' + esc(p.name) + '</div>'
            + '<div style="margin-top:0.6rem;">' + priceHtml + '</div>' + stockHtml + '</div></a>'
            + '<div style="padding:0 1rem 1rem;">'
            + '<button type="button" class="wb-like-btn" data-id="' + esc(p.nm_id || p.id) + '" data-name="' + esc(p.name) + '" style="width:100%;background:#fff;color:#c00;border:1px solid #c00;padding:0.5rem;border-radius:6px;cursor:pointer;font-weight:600;">❤ В избранное</button>'
            + '<button type="button" class="wb-buy-btn" data-id="' + esc(p.nm_id || p.id) + '" data-name="' + esc(p.name) + '" data-price="' + esc(finalPrice) + '" data-stock="' + stock + '"' + (stock <= 0 ? ' disabled style="margin-top:0.5rem;width:100%;background:#ccc;color:#fff;border:0;padding:0.55rem;border-radius:6px;cursor:not-allowed;font-weight:600;"' : ' style="margin-top:0.5rem;width:100%;background:#b49d84;color:#fff;border:0;padding:0.55rem;border-radius:6px;cursor:pointer;font-weight:600;"') + '>Купить в 1 клик</button>'
            + '</div></div>';
    }

    function render(filter) {
        var list = allProducts;
        if (filter === 'discount') list = allProducts.filter(function(p) { return (p.final_price || p.price) < p.price; });
        if (filter === 'stock') list = allProducts.filter(function(p) { return (p.stock == null ? 10 : p.stock) > 0; });
        if (!list.length) {
            grid.innerHTML = '<p style="color:#666;">Товары появятся после синхронизации с Wildberries.</p>';
            return;
        }
        grid.innerHTML = list.slice(0, 8).map(cardHtml).join('');
        if (window.initWbActions) window.initWbActions(grid);
    }

    fetch('/api/wb-products')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            allProducts = data.products || [];
            render('all');
        })
        .catch(function() {
            grid.innerHTML = '<p style="color:#666;">Не удалось загрузить товары.</p>';
        });

    document.querySelectorAll('.catalog-tab-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.catalog-tab-btn').forEach(function(b) {
                b.classList.remove('active');
                b.style.background = '#fff';
                b.style.color = '#b49d84';
            });
            btn.classList.add('active');
            btn.style.background = '#b49d84';
            btn.style.color = '#fff';
            render(btn.getAttribute('data-tab'));
        });
    });
})();
</script>
