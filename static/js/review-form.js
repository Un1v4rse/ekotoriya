/* Отзывы (цикл 4 / этап 6): кнопка «Оставить свой отзыв» (bitrix-jqm в клоне
 * мёртвая — бесконечная загрузка) открывает рабочую форму: имя, оценка 1–5,
 * товар (необязательно), текст → POST /api/review (на модерацию админом).
 * Длинные тексты отзывов обрезаются с кнопкой «Отзыв полностью».
 * Инжектится из app.py (_inject_lazyload_fix) на все страницы сайта. */
(function () {
  if (window.__wbReview) return;
  window.__wbReview = 1;

  function closeModal() {
    var o = document.querySelector('.wb-ctor-overlay');
    var m = document.querySelector('.wb-ctor-modal');
    if (o) o.remove();
    if (m) m.remove();
  }

  function openModal(html) {
    closeModal();
    var o = document.createElement('div');
    o.className = 'wb-ctor-overlay';
    var m = document.createElement('div');
    m.className = 'wb-ctor-modal';
    m.innerHTML = '<button type="button" class="wb-ctor-modal-close" aria-label="Закрыть">&times;</button>' + html;
    m.querySelector('.wb-ctor-modal-close').addEventListener('click', closeModal);
    o.addEventListener('click', closeModal);
    document.body.appendChild(o);
    document.body.appendChild(m);
    var f = m.querySelector('input');
    if (f) f.focus();
  }

  var FORM =
    '<h3 class="wb-ctor-modal-title">Оставить отзыв</h3>' +
    '<form>' +
    '<input type="text" name="rv_name" placeholder="Ваше имя" required maxlength="200" style="width:100%;box-sizing:border-box;padding:10px 12px;margin-bottom:10px;border:1px solid #ddd;border-radius:6px;font-size:0.95rem;">' +
    '<div class="wb-review-stars-input" style="position:relative;">' +
    '<input type="radio" name="rv_rating" id="rv-star5" value="5" checked><label for="rv-star5" title="5 из 5">★</label>' +
    '<input type="radio" name="rv_rating" id="rv-star4" value="4"><label for="rv-star4" title="4 из 5">★</label>' +
    '<input type="radio" name="rv_rating" id="rv-star3" value="3"><label for="rv-star3" title="3 из 5">★</label>' +
    '<input type="radio" name="rv_rating" id="rv-star2" value="2"><label for="rv-star2" title="2 из 5">★</label>' +
    '<input type="radio" name="rv_rating" id="rv-star1" value="1"><label for="rv-star1" title="1 из 5">★</label>' +
    '</div>' +
    '<input type="text" name="rv_product" placeholder="Товар (необязательно)" maxlength="500" style="width:100%;box-sizing:border-box;padding:10px 12px;margin-bottom:10px;border:1px solid #ddd;border-radius:6px;font-size:0.95rem;">' +
    '<textarea name="rv_text" placeholder="Ваш отзыв" required maxlength="8000" style="width:100%;box-sizing:border-box;padding:10px 12px;margin-bottom:10px;border:1px solid #ddd;border-radius:6px;font-size:0.95rem;min-height:100px;resize:vertical;"></textarea>' +
    '<button type="submit" style="width:100%;padding:11px;border:0;border-radius:6px;background:#b49d84;color:#fff;font-size:1rem;font-weight:600;cursor:pointer;">Отправить отзыв</button>' +
    '<div class="wb-ctor-msg" style="margin-top:10px;font-size:0.9rem;"></div>' +
    '</form>';

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('.add_review .btn,[data-name="send_review"],[data-param-form_id="REVIEW"]');
    if (!a) return;
    e.preventDefault();
    e.stopPropagation();
    openModal(FORM);
    var form = document.querySelector('.wb-ctor-modal form');
    if (!form) return;
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var btn = form.querySelector('[type=submit]');
      if (btn) btn.disabled = true;
      var msg = form.parentElement.querySelector('.wb-ctor-msg');
      var rating = form.querySelector('input[name="rv_rating"]:checked');
      fetch('/api/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.rv_name.value,
          rating: rating ? parseInt(rating.value, 10) : 5,
          product_name: form.rv_product.value,
          text: form.rv_text.value
        })
      })
        .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
        .then(function (res) {
          var d = res.data || {};
          if (msg) {
            msg.style.color = d.success ? '#2e7d32' : '#c62828';
            msg.textContent = d.message || (d.error || 'Не удалось отправить. Попробуйте позже.');
          }
          if (d.success) form.reset();
        })
        .catch(function () {
          if (msg) {
            msg.style.color = '#c62828';
            msg.textContent = 'Не удалось отправить. Попробуйте позже.';
          }
        })
        .finally(function () { if (btn) btn.disabled = false; });
    });
  }, true);

  /* «Отзыв полностью»: обрезаем длинные тексты отзывов и даём кнопку разворачивания. */
  function setupClamp() {
    document.querySelectorAll('.wb-review-text').forEach(function (t) {
      if (t.dataset.wbClampReady) return;
      t.dataset.wbClampReady = 1;
      t.classList.add('clamped');
      setTimeout(function () {
        if (t.scrollHeight <= t.clientHeight + 4) return;
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'wb-review-toggle';
        b.textContent = 'Отзыв полностью';
        b.addEventListener('click', function () {
          t.classList.remove('clamped');
          b.remove();
        });
        t.parentElement.insertBefore(b, t.nextSibling);
      }, 50);
    });
  }

  document.addEventListener('wb:results-updated', setupClamp);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setupClamp);
  else setupClamp();
})();
