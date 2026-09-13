/*
You can use this file with your scripts.
It will not be overwritten when you upgrade solution.
*/

//window.onerror = function (msg, url, line, col, exception) { BX.ajax.get('/ajax/error_log_logic.php', { data: { msg: msg, exception: exception, url: url, line: line, col: col } }); }

$(document).ready(function(){
	$(document).on('click', '.mobile_regions .city_item', function(e){
		e.preventDefault();
		var _this = $(this);
		$.removeCookie('current_region');
		$.cookie('current_region', _this.data('id'), {path: '/',domain: 'next.aspro-partner.ru'});
	});

	$(document).on('click', '.region_wrapper .more_item:not(.current) span', function(e){
		$.removeCookie('current_region');
		$.cookie('current_region', $(this).data('region_id'), {path: '/',domain: 'next.aspro-partner.ru'});
	});

	$(document).on('click', '.confirm_region .aprove', function(e){
		var _this = $(this);
		$.removeCookie('current_region');
		$.cookie('current_region', _this.data('id'), {path: '/',domain: 'next.aspro-partner.ru'});
	});

	$(document).on('click', '.cities .item a', function(e){
    	e.preventDefault();
    	var _this = $(this);
    	$.removeCookie('current_region');
		$.cookie('current_region', _this.data('id'), {path: '/',domain: 'next.aspro-partner.ru'});
    });

    $(document).on('click', '.popup_regions .ui-menu a', function(e){
    	e.preventDefault();
    	var _this = $(this);
    	var href = _this.attr('href')
    	if(typeof arRegions !== 'undefined' && arRegions.length){
	    	$.removeCookie('current_region');
	    	for(i in arRegions){
	    		var region = arRegions[i];
	    		if(region.HREF == href){
					$.cookie('current_region', region.ID, {path: '/',domain: 'next.aspro-partner.ru'});
	    		}
	    	}
    	}
		location.href = href;
    });


	//цели по клику на Ozon и WB
	$('#bx_651765591_391 > a').click(function () {
		ym(96433627,'reachGoal','klik-ozon');
		return true;
	}); 
	$('#bx_651765591_390 > a').click(function () {
		ym(96433627,'reachGoal','klik-wb');
		return true;
	}); 
});

document.addEventListener("DOMContentLoaded", function() {
    if (navigator.cookieEnabled === false) {
        console.log("Cookies отключены!");
    } else {
        // Настройки
        const BUTTON_ACCEPT_TEXT = `Соглашаюсь`;
        const BUTTON_REJECT_TEXT = `Отклонить`;
        const BUTTON_SETTINGS_TEXT = 'Настроить';
        const BUTTON_SAVE_TEXT = 'Сохранить настройки';
        const BUTTON_COLOR = '#b49d84';
        const MAIN_TEXT = `Используя сайт, вы предоставляете согласие на обработку файлов cookie (с помощью сервисов веб-аналитики) в соответствии с политикой обработки персональных данных. Запретить обработку файлов cookie можно в настройках браузера.`;
        const FULL_TEXT = `
            <h3>Настройки cookie</h3>
            <p>Наш сайт использует cookie-файлы для:</p>
            <ul>
                <li><strong>Обязательные</strong> – работа сайта (всегда активны)</li>
                <li><strong>Аналитические</strong> – сбор статистики</li>
                <li><strong>Рекламные</strong> – персонализация рекламы</li>
            </ul>
            <p>Данные обрабатываются в соответствии с законодательством РФ.</p>
        `;

        // Проверка, было ли уже согласие
        function getCookie(name) {
            const matches = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/([\.$?*|{}\(\)\[\]\\\/\+^])/g, '\\$1') + "=([^;]*)"));
            return matches ? decodeURIComponent(matches[1]) : undefined;
        }

        // Если согласия еще не было – показываем баннер
        if (!getCookie("COOKIE_CONSENT")) {
            document.body.insertAdjacentHTML('beforeend', cookieBannerTemplate());
        }

        // Шаблон баннера
        function cookieBannerTemplate() {
            const styleContainer = `
                position: fixed;
                bottom: 0;
                right: 50%;
                transform: translateX(50%);
                padding: 20px;
                font-family: Arial, sans-serif;
                max-width: 1000px;
                width: 90%;
                display: flex;
                flex-wrap: wrap;
                gap: 15px;
                align-items: center;
                justify-content: space-between;
                box-shadow: 0 5px 20px rgba(0, 0, 0, 0.2);
                background-color: #fff;
                border-radius: 10px 10px 0 0;
                z-index: 9999;
            `;
            const styleBtn = `
                color: white;
                background-color: ${BUTTON_COLOR};
                font-size: 14px;
                padding: 10px 20px;
                border-radius: 5px;
                border: none;
                cursor: pointer;
                margin: 5px;
            `;
            const styleBtnReject = `
                color: ${BUTTON_COLOR};
                background: none;
                border: 1px solid ${BUTTON_COLOR};
                font-size: 14px;
                padding: 10px 20px;
                border-radius: 5px;
                cursor: pointer;
                margin: 5px;
            `;

            return `
                <div class="cookie-banner" style="${styleContainer}">
                    <div style="flex: 1; min-width: 250px;">
                        <p>${MAIN_TEXT}</p>
                    </div>
                    <div style="display: flex; flex-wrap: wrap; justify-content: flex-end;">
                        <button style="${styleBtnReject}" onclick="setCookieConsent('reject')">${BUTTON_REJECT_TEXT}</button>
                        <button style="${styleBtn}" onclick="setCookieConsent('accept')">${BUTTON_ACCEPT_TEXT}</button>
                    </div>
                </div>
            `;
        }

        // Шаблон настроек cookie
        function cookieSettingsTemplate() {
            const styleModal = `
                position: fixed;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 5px 30px rgba(0, 0, 0, 0.3);
                z-index: 10000;
                max-width: 600px;
                width: 90%;
                max-height: 80vh;
                overflow-y: auto;
            `;
            const styleOverlay = `
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.5);
                z-index: 9999;
            `;
            const styleCheckbox = `
                margin: 15px 0;
                display: flex;
                align-items: center;
            `;

            return `
                <div class="cookie-overlay" style="${styleOverlay}">
                    <div class="cookie-settings" style="${styleModal}">
                        ${FULL_TEXT}
                        <div style="${styleCheckbox}">
                            <input type="checkbox" id="analytics-cookies" checked disabled>
                            <label for="analytics-cookies">Обязательные (нельзя отключить)</label>
                        </div>
                        <div style="${styleCheckbox}">
                            <input type="checkbox" id="marketing-cookies" checked>
                            <label for="marketing-cookies">Рекламные cookie</label>
                        </div>
                        <div style="${styleCheckbox}">
                            <input type="checkbox" id="statistics-cookies" checked>
                            <label for="statistics-cookies">Аналитические cookie</label>
                        </div>
                        <div style="display: flex; justify-content: flex-end; margin-top: 20px;">
                            <button style="
                                color: white;
                                background-color: ${BUTTON_COLOR};
                                padding: 10px 20px;
                                border: none;
                                border-radius: 5px;
                                cursor: pointer;
                            " onclick="saveCookieSettings()">
                                ${BUTTON_SAVE_TEXT}
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }

        // Функции для работы с cookie
        window.setCookieConsent = function(action) {
            const date = new Date();
            date.setFullYear(date.getFullYear() + 1); // Cookie на 1 год

            if (action === 'accept') {
                document.cookie = `COOKIE_CONSENT=all; expires=${date.toUTCString()}; path=/`;
                document.cookie = `COOKIE_ANALYTICS=true; expires=${date.toUTCString()}; path=/`;
                document.cookie = `COOKIE_MARKETING=true; expires=${date.toUTCString()}; path=/`;
            } else if (action === 'reject') {
                document.cookie = `COOKIE_CONSENT=necessary; expires=${date.toUTCString()}; path=/`;
                document.cookie = `COOKIE_ANALYTICS=false; expires=${date.toUTCString()}; path=/`;
                document.cookie = `COOKIE_MARKETING=false; expires=${date.toUTCString()}; path=/`;
            }

            document.querySelector('.cookie-banner')?.remove();
        };

        window.showCookieSettings = function() {
            document.body.insertAdjacentHTML('beforeend', cookieSettingsTemplate());
        };

        window.saveCookieSettings = function() {
            const analytics = document.getElementById('statistics-cookies').checked;
            const marketing = document.getElementById('marketing-cookies').checked;
            const date = new Date();
            date.setFullYear(date.getFullYear() + 1);

            document.cookie = `COOKIE_CONSENT=custom; expires=${date.toUTCString()}; path=/`;
            document.cookie = `COOKIE_ANALYTICS=${analytics}; expires=${date.toUTCString()}; path=/`;
            document.cookie = `COOKIE_MARKETING=${marketing}; expires=${date.toUTCString()}; path=/`;

            document.querySelector('.cookie-overlay')?.remove();
            document.querySelector('.cookie-banner')?.remove();
        };
    }
});