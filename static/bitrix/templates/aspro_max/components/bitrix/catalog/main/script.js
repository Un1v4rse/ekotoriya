function setNewHeader(obOffer) {
  var ratingHtml = (priceHtml = imgHtml = skuHtml = buttonHtml = "");

  if (arMaxOptions["THEME"]["SHOW_HEADER_GOODS"] != "Y" || !$(".main-catalog-wrapper.details").length) return;

  $("#headerfixed").addClass("with-product");

  if ($(".product-info-headnote .rating").length) {
    //show rating
    ratingHtml =
      '<div class="votes_block nstar' +
      (arAsproOptions["THEME"]["REVIEWS_VIEW"] == "EXTENDED" ? " pointer" : "") +
      '"><div class="ratings"><div class="inner_rating"';
    var inner = $(".product-info-headnote .rating .inner_rating");
    if (inner.length && inner.attr("title") !== undefined) {
      ratingHtml += 'title="' + inner.attr("title");
    }
    ratingHtml += '">';

    $(".product-info-headnote .rating .inner_rating:first > div").each(function (index) {
      var index_title = index + 1;
      ratingHtml += '<div class="item-rating ' + ($(this).hasClass("filed") ? "filed" : "");
      if (inner.attr("title") === undefined) {
        ratingHtml += '" title="' + index_title;
      }
      ratingHtml += '">' + $(this).html() + "</div>";
    });

    ratingHtml += "</div></div></div>";

    if ($(".product-info-headnote .rating span").length) {
      ratingHtml += $(".product-info-headnote .rating span")[0].outerHTML;
    }
  }
  if ($('div *[itemprop="offers"]').length) {
    //show price
    if (
      typeof obOffer == "undefined" &&
      BX.util.object_search_key("ASPRO_ITEM_POPUP_PRICE", BX.message) &&
      BX.message("ASPRO_ITEM_POPUP_PRICE") == "Y"
    ) {
      var obOffer = {
        SHOW_POPUP_PRICE: false,
        PRICES_COUNT: BX.message("ASPRO_ITEM_PRICES"),
      };
      if (BX.util.object_search_key("ASPRO_ITEM_PRICE_MATRIX", BX.message)) {
        obOffer.USE_PRICE_COUNT = true;
        obOffer.PRICE_MATRIX_HTML = BX.message("ASPRO_ITEM_PRICE_MATRIX");
      } else if (BX.util.object_search_key("ASPRO_ITEM_PRICE", BX.message)) {
        obOffer.PRICES_HTML = BX.message("ASPRO_ITEM_PRICE");
      }
    }

    if (typeof obOffer !== "undefined") {
      if (
        !obOffer.SHOW_POPUP_PRICE &&
        (("PRICES_COUNT" in obOffer && obOffer.PRICES_COUNT > 1) ||
          ("PRICES" in obOffer && Object.keys(obOffer.PRICES).length > 1) ||
          ("ITEM_PRICES" in obOffer && Object.keys(obOffer.ITEM_PRICES).length > 1))
      ) {
        var bPriceCount = obOffer.USE_PRICE_COUNT && obOffer.PRICE_MATRIX_HTML,
          topPrice = "";

        if (bPriceCount) {
          if ($("div:not(.adaptive-block)>.cost.detail .with_matrix:visible").length) {
            topPrice = $("div:not(.adaptive-block)>.cost.detail .with_matrix:visible").html();
          } else {
            topPrice = '<div class="prices-wrapper">' + $("div:not(.adaptive-block)>.cost.detail").html() + "</div>";
          }
        } else {
          topPrice = '<div class="prices-wrapper">' + obOffer.PRICES_HTML + "</div>";
        }

        priceHtml = '<div class="with_matrix pl with_old price_matrix_wrapper">' + topPrice + "</div>";
        priceHtml += '<div class="js_price_wrapper">';
        priceHtml +=
          '<div class="js-info-block rounded3">' +
          '<div class="block_title text-upper font_xs font-bold">' +
          BX.message("PRICES_TYPE") +
          '<i class="svg inline  svg-inline-close" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path data-name="Rounded Rectangle 114 copy 3" class="cccls-1" d="M334.411,138l6.3,6.3a1,1,0,0,1,0,1.414,0.992,0.992,0,0,1-1.408,0l-6.3-6.306-6.3,6.306a1,1,0,0,1-1.409-1.414l6.3-6.3-6.293-6.3a1,1,0,0,1,1.409-1.414l6.3,6.3,6.3-6.3A1,1,0,0,1,340.7,131.7Z" transform="translate(-325 -130)"></path></svg></i>' +
          "</div>" +
          '<div class="block_wrap">' +
          '<div class="block_wrap_inner prices scrollblock">';

        if (bPriceCount) {
          priceHtml += obOffer.PRICE_MATRIX_HTML;
        } else if (obOffer.PRICES_HTML) {
          priceHtml += obOffer.PRICES_HTML;
        }

        priceHtml += '<div class="more-btn text-center"></div>';
        priceHtml += "</div></div></div>";
        priceHtml +=
          '<div class="js-show-info-block more-item-info rounded3 bordered-block text-center"><i class="svg inline  svg-inline-fw" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" width="8" height="2" viewBox="0 0 8 2"><path id="Ellipse_292_copy_3" data-name="Ellipse 292 copy 3" class="cls-1" d="M320,4558a1,1,0,1,1-1,1A1,1,0,0,1,320,4558Zm-3,0a1,1,0,1,1-1,1A1,1,0,0,1,317,4558Zm6,0a1,1,0,1,1-1,1A1,1,0,0,1,323,4558Z" transform="translate(-316 -4558)"></path></svg></i></div>';
        priceHtml += "</div>";
      } else {
        priceHtml = $("div:not(.adaptive-block)>.cost.detail").html();
      }
    } else {
      priceHtml = $("div:not(.adaptive-block)>.cost.detail").html();
    }
  }

  if ($(".product-info .complect_prices_block").length) {
    //show price complect
    priceHtml = $(".cost.detail").html();
  }

  let bonusHtml = "";
  let bonusBlockDetail = $("div:not(.adaptive-block)>.cost.detail ~ .bonus-system-block");
  if (bonusBlockDetail.length) {
    bonusHtml = bonusBlockDetail[0].outerHTML;
  }

  if ($("#photo-sku").length) {
    //show img
    imgSrc = $("#photo-sku .detail-gallery-big__picture.one").attr("src")
      ? $("#photo-sku .detail-gallery-big__picture.one").attr("src")
      : $("#photo-sku .detail-gallery-big__picture").data("src")
      ? $("#photo-sku .detail-gallery-big__picture").data("src")
      : $("#photo-sku .detail-gallery-big__picture").attr("src");
  } else if ($(".detail-gallery-big #photo-0").length) {
    imgSrc = $(".detail-gallery-big #photo-0 .detail-gallery-big__picture").data("src")
      ? $(".detail-gallery-big #photo-0 .detail-gallery-big__picture").data("src")
      : $(".detail-gallery-big #photo-0 .detail-gallery-big__picture").attr("src");
  }
  //show button
  if ($(".slide_offer").length) {
    if ($(".product-container .buy_block .to-order").length) {
      buttonHtml =
        '<span class="buy_block">' +
        $(".product-container .buy_block .to-order")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        "</span>";
    } else if ($(".product-container .buy_block .to-subscribe").length) {
      buttonHtml =
        '<span class="buy_block">' +
        $(".product-container .buy_block .to-subscribe")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        $(".product-container .buy_block .in-subscribe")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        "</span>";
    } else if ($(".product-container .buy_block .btn").length) {
      buttonHtml =
        '<span class="buy_block"><span class="btn btn-default btn-sm slide_offer more stay_on_page type_block' +
        ($(".product-container .buy_block .offer_buy_block .btn").hasClass("has_prediction") ? " has_prediction" : "") +
        '">' +
        ($(".product-container .buy_block .offer_buy_block .in-cart").is(":visible")
          ? $(".product-container .buy_block .offer_buy_block .in-cart").html()
          : BX.message("MORE_INFO_SKU")) +
        "</span></span>";
    }
  } else if ($(".buy_block .sku_props").length) {
    if ($(".product-container .buy_block .btn--out-of-production").length) {
      buttonHtml =
        '<span class="buy_block">' +
        $(".product-container .buy_block .btn--out-of-production")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        "</span>";
    } else if ($(".product-container .buy_block .to-order").length) {
      buttonHtml =
        '<span class="buy_block">' +
        $(".product-container .buy_block .to-order")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        "</span>";
    } else if ($(".product-container .buy_block .to-subscribe").length) {
      buttonHtml =
        '<span class="buy_block">' +
        $(".product-container .buy_block .to-subscribe")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        $(".product-container .buy_block .in-subscribe")
          .clone()
          .wrap("<div/>")
          .parent()
          .html()
          .replace("btn-lg", "btn-sm") +
        "</span>";
    } else if ($(".product-container .buy_block .btn").length) {
      buttonHtml =
        '<span class="buy_block"><span class="btn btn-default btn-sm more type_block' +
        ($(".product-container .buy_block .offer_buy_block .btn").hasClass("has_prediction") ? " has_prediction" : "") +
        '">' +
        ($(".product-container .buy_block .offer_buy_block .in-cart").is(":visible")
          ? $(".product-container .buy_block .offer_buy_block .in-cart").html()
          : BX.message("MORE_INFO_SKU")) +
        "</span></span>";
    }
    // buttonHtml = $('.buy_block .button_block').html().replace(/btn-lg/g, 'btn-sm more ww');
  } else if ($(".buy_block .button_block").length) {
    buttonHtml = $(".buy_block .button_block")
      .html()
      .replace(/btn-lg/g, "btn-sm");
  }

  if ($(".product-info .complect_prices_block").length) {
    //show price complect
    buttonHtml = $(".buy_complect_wrap").html();
  }

  var $skuContainer = $(".sku_props, .sku-props");
  if ($skuContainer.length) {
    var skuHtml = '<span class="bx_catalog_item_scu dotted">' +
                  BX.message("DETAIL_HEADER_SKU_ANCHOR_TEXT") +
                  '</span>';
  } else {
      var skuHtml = "";
  }

  // setTimeout(function(){
  if (!$("#headerfixed .ajax-head-row").length) {
    $("#headerfixed .logo-row").before('<div class="ajax-head-row"></div>');
  }

  $("#headerfixed .ajax-head-row").html(`
  <div class="ajax_load">
    <div class="table-view  flexbox flexbox--row">
      <div class="table-view__item item main_item_wrapper item_info catalog-adaptive flexbox flexbox--row flexbox--gap flexbox--gap-20">
          <div class="line-block__item flex1">
            <div class="flexbox flexbox--row flexbox--gap flexbox--gap-20">
              <div class="item-foto">
                <div class="item-foto__picture">
                  <img src="${imgSrc}" />
                </div>
              </div>
              <div class="flexbox flexbox--gap flexbox--gap-4">
                <div class="item-title">
                  <span class="lineclamp-1">${$("#pagetitle").text()}</span>
                </div>
                <div class="flexbox flexbox--row column-gap column-gap--20 row-gap row-gap--4 flexbox--wrap">
                  ${
                    $(".product-info-headnote .rating").length
                      ? `<div class="rating sm-stars flexbox flexbox--row flexbox--gap flexbox--gap-8">${ratingHtml}</div>`
                      : ""
                  }
                  ${
                    $(".product-main .quantity_block_wrapper .item-stock:visible").length
                      ? `<div class="item-stock">${$(
                          ".product-main .quantity_block_wrapper .item-stock:visible"
                        ).html()}</div>`
                      : ""
                  }
                  <div class="item-sku text-form font_sxs muted muted777">${skuHtml}</div>
                </div>
              </div>
            </div>
          </div>
          <div class="line-block__item no-shrinked">
            <div class="line-block flexbox--gap flexbox--gap-40">
              <div class="item-price">
                <div class="cost prices ${$(".cost.detail.sku_matrix").length ? "sku_matrix" : ""}">
                  ${priceHtml || ""}
                </div>
                ${bonusHtml}
              </div>
              <div class="flexbox flexbox--row flexbox--gap flexbox--gap-12">
                <div class="item-buttons but-cell">
                  ${buttonHtml}
                </div>
                ${
                $(".product-info .like_icons").length
                  ? `
                <div class="item-icons s_${$(".product-info .like_icons").data("size")}">
                  <div class="like_icons list static icons long">
                    ${$(".product-info .like_icons").html()}
                  </div>
                </div>`
                  : ""
                }
              </div>
            </div>
          </div>
      </div>
  </div>
</div>
  `);

  InitLazyLoad();

  if (typeof obMaxPredictions === "object") {
    obMaxPredictions.showAll();
  }
}

BX.addCustomEvent("onWindowResize", function (eventdata) {
  if (window.predictionWindow && typeof window.predictionWindow.close === "function") {
    window.predictionWindow.close();
  }
});

$(document).on("click", ".ordered-block.goods .tabs li", function () {
  setTimeout(sliceItemBlockSlide, 5);
});

$(document).on("click", ".item-stock .store_view.dotted", function () {
  scroll_block($(".js-store-scroll"), $("a[href='#stores']"));
});

$(document).on("click", "#headerfixed .ratings", function () {
  scroll_block($(".js-store-scroll"), $("a[href='#reviews']"));
});

$(document).on(
  "click",
  ".blog-info__rating--top-info, #headerfixed .wproducts .wrapp_stockers .rating .votes_block",
  function () {
    var reviews = $(".reviews.EXTENDED");
    if (reviews.length) {
      var tabsBlock = $(".ordered-block.tabs-block");
      var blockToScroll = tabsBlock.length ? tabsBlock : reviews;
      scroll_block(blockToScroll, $('.ordered-block .nav-tabs a[href="#reviews"]'));
    }
  }
);

$(document).on("click", ".table-view__item--has-stores .item-stock .value", function () {
  $(this).closest(".table-view__item-wrapper").find(".stores-icons .btn").trigger("click");
});

$(document).on("click", "#headerfixed .item-buttons .more:not(.stay_on_page)", function () {
  if ($(".product-container .buy_block .offer_buy_block .to-cart").is(":visible")) {
    $(".product-container .buy_block .offer_buy_block .to-cart").trigger("click");
  } else if ($(".middle-info-wrapper .to-cart").is(":visible")) {
    $(".middle-info-wrapper .to-cart").trigger("click");
  } else if ($(".product-side .to-cart").is(":visible")) {
    $(".product-side .to-cart").trigger("click");
  } else {
    location.href = arAsproOptions["PAGES"]["BASKET_PAGE_URL"];
  }
});

$(document).on("click", "#headerfixed .item-sku", function () {
  var $target = $(".product-container .sku_props .bx_catalog_item_scu, .product-main:not(.product-action) .sku-props .sku-props__group");

  if ($target.length) {
    var offset = $target.offset().top;
    $("body, html").animate({ scrollTop: offset - 150 }, 500);
  }
});

$(document).on("click", ".stores-title .stores-title__list", function () {
  var _this = $(this);
  _this.siblings().removeClass("stores-title--active");
  _this.addClass("stores-title--active");

  $(".stores_block_wrap .stores-amount-list").removeClass("stores-amount-list--active");
  $(".stores_block_wrap .stores-amount-list:eq(" + _this.index() + ")")
    .addClass("stores-amount-list--active")
    .queue(function () {
      if (_this.hasClass("stores-title--map")) {
        if (typeof map !== "undefined") {
          map.container.fitToViewport();
          if (typeof clusterer !== "undefined" && !$(this).find(".detail_items").is(":visible")) {
            map.setBounds(clusterer.getBounds(), {
              zoomMargin: 40,
              // checkZoomRange: true
            });
          }
        }
      }
    });
});

$(document).on("click", ".info_ext_block .title", function () {
  var _this = $(this);
  _this.toggleClass("opened");
  _this.next().slideToggle();
});

$(document).on("click", ".stores-icons .btn", function () {
  var _this = $(this),
    block = _this.closest(".table-view__item-wrapper").next(),
    bVisibleblock = block.is(":visible"),
    animate = bVisibleblock ? "slideUp" : "slideDown";

  if (!_this.hasClass("clicked")) {
    _this.addClass("clicked");

    block.stop().slideToggle({
      start: () => {
        _this.toggleClass("closed");
      },
      duration: 400,
      done: () => {
        _this.removeClass("clicked");
      },
    });
  }
});

var checkFilterLandgings = function () {
  if ($(".top-content-block .with-filter .with-filter-wrapper").length) {
    var bActiveClass = false;
    if ($("#mobilefilter .with-filter-wrapper").length) {
      if ($("#mobilefilter .with-filter-wrapper .bx_filter_parameters_box").hasClass("active")) {
        bActiveClass = true;
      }
      $("#mobilefilter .with-filter-wrapper").empty();
      $(".top-content-block .with-filter .with-filter-wrapper").prependTo($("#mobilefilter .with-filter-wrapper"));
    } else {
      $(".top-content-block .with-filter .with-filter-wrapper").prependTo($("#mobilefilter .bx_filter_parameters"));
    }
    if ($("#mobilefilter .bx_filter_parameters .landings-list__item--active").length || bActiveClass) {
      $("#mobilefilter .with-filter-wrapper .bx_filter_parameters_box").addClass("active");
    }
    $("#mobilefilter .scrollbar").scrollTop(0);
  }
};

function setFixedBuyBlock() {
  try {
    var fixedMobile = arAsproOptions.THEME["FIXED_BUY_MOBILE"] == "Y";

    if (fixedMobile) {
      var buyBlock = $(".product-action .buy_block");
      var counterWrapp = $(".product-action .counter_wrapp:not(.services_counter)");

      if (buyBlock.length && counterWrapp.length && !$(".list-offers.ajax_load").length) {
        if (window.matchMedia("(max-width: 767px)").matches) {
          if (buyBlock.data("hasCatalog") === undefined) {
            var hasCatalog = buyBlock.hasClass("catalog_block");
            buyBlock.data("hasCatalog", hasCatalog);
          }
          buyBlock.addClass("catalog_block");

          if (counterWrapp.data("hasList") === undefined) {
            var hasList = counterWrapp.hasClass("list");
            counterWrapp.data("hasList", hasList);
          }
          counterWrapp.removeClass("list").addClass("fixed");
        } else {
          if (buyBlock.data("hasCatalog") !== undefined && !buyBlock.data("hasCatalog")) {
            buyBlock.removeClass("catalog_block");
          }

          if (counterWrapp.data("hasList") !== undefined && counterWrapp.data("hasList")) {
            counterWrapp.addClass("list");
          }
          counterWrapp.removeClass("fixed");
        }
      }
    }
  } catch (e) {}
}

$(document).ready(function () {
  lazyLoadPagenBlock();
  BX.addCustomEvent("onWindowResize", function () {
    setFixedBuyBlock();
  });
  setFixedBuyBlock();
});

$(document).on("change", "input.complect_checkbox_item", function () {
  setNewPriceComplect();
});

function setNewPriceComplect() {
  var allCheckbox = $(".complect_main_wrap .complect_checkbox_item:checked");
  var newPrice = 0;
  allCheckbox.each(function () {
    var th = $(this).closest(".catalog-block-view__item").find(".button_block .to-cart");
    var thPrice = th.attr("data-value");
    var thQuantity = th.attr("data-quantity");
    if (thPrice > 0 && thQuantity > 0) {
      newPrice += thPrice * thQuantity;
    }
  });
  $(".complect_price_value").html(newPrice.toLocaleString("ru"));
  //console.log(newPrice);
}

BX.addCustomEvent("onCompleteAction", function (eventdata) {
  if (eventdata.action === "ajaxContentLoaded" || eventdata.action === "jsLoadBlock") {
    if (typeof window.tableScrollerOb === "object" && window.tableScrollerOb) {
      window.tableScrollerOb.toggle();
    }
  }
});
