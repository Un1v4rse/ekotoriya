<div class="include-favorit-wrap" style="max-width:1400px;margin:0 auto;padding:2.5rem 1rem;">
  <div class="maxwidth-theme">
    <h2 style="font-size:1.6rem;font-weight:600;margin:0 0 1.5rem;">Популярные товары</h2>
    <div id="favorit-items-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:1rem;"></div>
    <div style="text-align:center;margin-top:1.5rem;">
      <button type="button" id="favorit-show-more" style="display:none;background:#b49d84;color:#fff;border:none;padding:0.8rem 2.5rem;border-radius:6px;cursor:pointer;font-weight:600;font-size:1rem;">Показать ещё</button>
    </div>
  </div>
</div>
<script>
(function() {
    var grid = document.getElementById('favorit-items-grid');
    var moreBtn = document.getElementById('favorit-show-more');
    if (!grid) return;
    var PAGE = 4;
    var all = [];
    var offset = 0;

    function esc(s) { return String(s || '').replace(/"/g, '&quot;'); }

    function cardHtml(p) {
        var photo = p.photo || '';
        var src = photo.charAt(0) === '/' ? photo : '/proxy/image?url=' + encodeURIComponent(photo);
        var priceHtml = p.discount_price && p.discount_price < p.price
            ? '<span style="color:#999;text-decoration:line-through;font-size:0.85rem;">' + p.price + ' ₽</span> <span style="font-weight:700;font-size:1.05rem;">' + p.final_price + ' ₽</span>'
            : '<span style="font-weight:700;font-size:1.05rem;">' + (p.final_price || p.price) + ' ₽</span>';
        return '<div class="wb-product-card" style="border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;background:#fff;">'
            + '<a href="' + (p.url || '#') + '" target="_blank" rel="noopener" style="text-decoration:none;color:inherit;">'
            + '<div style="padding:1rem;background:#f9f9f9;"><img src="' + src + '" alt="" loading="lazy" style="width:100%;height:180px;object-fit:contain;display:block;"></div>'
            + '<div style="padding:1rem;"><div data-name="' + esc(p.name) + '" style="font-weight:600;font-size:0.9rem;line-height:1.35;min-height:3.6em;overflow:hidden;">' + (p.name || '') + '</div>'
            + '<div style="margin-top:0.6rem;">' + priceHtml + '</div></div></a>'
            + '<div style="padding:0 1rem 1rem;"><button type="button" class="wb-like-btn" data-id="' + p.nm_id + '" data-name="' + esc(p.name) + '" style="width:100%;background:#fff;color:#c00;border:1px solid #c00;padding:0.5rem;border-radius:6px;cursor:pointer;font-weight:600;">❤ В избранное</button></div>'
            + '</div>';
    }

    function showNext() {
        if (!all.length) return;
        var chunk = [];
        for (var i = 0; i < PAGE && offset < all.length; i++) {
            chunk.push(all[offset]);
            offset++;
        }
        grid.insertAdjacentHTML('beforeend', chunk.map(cardHtml).join(''));
        if (window.initWbActions) window.initWbActions(grid);
        if (offset >= all.length) {
            // все товары показаны
            moreBtn.textContent = 'Все товары показаны';
            moreBtn.disabled = true;
            moreBtn.style.opacity = '0.6';
            moreBtn.style.cursor = 'default';
        }
    }

    fetch('/api/wb-products')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            all = data.products || [];
            if (!all.length) {
                grid.innerHTML = '<p style="color:#666;">Товары появятся после синхронизации с Wildberries.</p>';
                return;
            }
            offset = 0;
            showNext();
            // кнопка активна, пока товаров больше одной страницы; показ циклический — жать можно много раз
            if (all.length > PAGE) moreBtn.style.display = 'inline-block';
        })
        .catch(function() {
            grid.innerHTML = '<p style="color:#666;">Не удалось загрузить товары.</p>';
        });

    if (moreBtn) {
        moreBtn.addEventListener('click', function() {
            showNext();
            moreBtn.blur();
        });
    }
})();
</script>
