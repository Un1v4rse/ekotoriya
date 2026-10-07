/* Полифилл Bitrix-lazyload: плагин ls.unveilhooks загружается раньше lazysizes
   и не регистрируется, из-за чего фоны/картинки с data-bg/data-src не подгружались
   (оставался спиннер double_ring). Обрабатываем вручную через IntersectionObserver. */
(function () {
    'use strict';

    function applyBg(el) {
        if (el.classList.contains('lazyloaded')) return;
        var src = el.getAttribute('data-bg') || el.getAttribute('data-src');
        if (!src) return;
        var img = new Image();
        img.onload = function () {
            el.style.backgroundImage = 'url("' + src + '")';
            el.classList.add('lazyloaded');
            el.classList.remove('lazy');
        };
        img.onerror = function () {
            el.classList.add('lazyloaded'); /* убираем спиннер даже при ошибке */
        };
        img.src = src;
    }

    function applyImg(el) {
        if (el.classList.contains('lazyloaded')) return;
        var src = el.getAttribute('data-src');
        if (!src) return;
        var img = new Image();
        img.onload = function () {
            el.src = src;
            el.classList.add('lazyloaded');
            el.classList.remove('lazy');
        };
        img.onerror = function () {
            el.classList.add('lazyloaded');
        };
        img.src = src;
    }

    function fixAll() {
        /* В iframe редактора страницы скролл живёт в родительском документе,
           поэтому IntersectionObserver (который обрезает видимую область по
           границам iframe в родителе) для нижних блоков никогда не срабатывает.
           В редакторе грузим всё сразу, без наблюдателей. */
        var inEditorFrame = false;
        try { inEditorFrame = !!(window.frameElement && window.frameElement.id === 'pe-content-frame'); } catch (e) {}
        if (inEditorFrame) {
            document.querySelectorAll('[data-bg].lazy, [data-lazyload].lazy').forEach(applyBg);
            document.querySelectorAll('img.lazy[data-src]').forEach(applyImg);
            return;
        }
        var els = document.querySelectorAll('[data-bg].lazy, [data-lazyload].lazy');
        if (!('IntersectionObserver' in window)) {
            els.forEach(applyBg);
            document.querySelectorAll('img.lazy[data-src]').forEach(applyImg);
            return;
        }
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (!en.isIntersecting) return;
                io.unobserve(en.target);
                applyBg(en.target);
            });
        }, { rootMargin: '300px' });
        els.forEach(function (el) { io.observe(el); });

        var ioImg = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (!en.isIntersecting) return;
                ioImg.unobserve(en.target);
                applyImg(en.target);
            });
        }, { rootMargin: '300px' });
        document.querySelectorAll('img.lazy[data-src]').forEach(function (el) { ioImg.observe(el); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fixAll);
    } else {
        fixAll();
    }
    /* Хук для редактора: контент, подгруженный в iframe позже (ленивые блоки,
       черновик), тоже нужно «раскрыть» — редактор дёргает fixAll повторно. */
    window.__lazyFixAll = fixAll;
})();
