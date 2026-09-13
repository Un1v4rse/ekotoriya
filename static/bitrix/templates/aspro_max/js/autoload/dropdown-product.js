/*hover top products*/
$(document).on("mouseenter", ".basket-link:not(.basket):not(.counter-state--empty)", function () {
  if (window.matchMedia("(min-width: 992px)").matches) {
    const _this = $(this);
    const $wrapper = _this.parent();

    let $hover_block = $wrapper.find(".basket_hover_block");
    let type = _this.hasClass("compare") ? "compare" : "favorite";
    let obParams = {
      type: type,
    };

    if (!$hover_block.length) {
      $hover_block = $(
        '<div class="basket_hover_block loading_block1 loading_block_content1 dropdown-' + type + '"></div>'
      );
      $hover_block.appendTo($wrapper);
    }

    if (!$hover_block.hasClass("loaded")) {
      BX.ajax
        .runAction("aspro:max.DropdownProducts.show", {
          data: {
            params: obParams,
          },
        })
        .then(
          (response) => {
            $hover_block.addClass("loaded");
            $hover_block.html(response.data.html);
          },
          (response) => {
            console.log("error");
          }
        );
    }
  }
});

$(document).on("click", ".dropdown-product__items .remove-cell", function () {
  let _this = $(this);
  let itemAction = JItemAction.factory(this);
  itemAction.state = false;

  _this.css("pointer-events", "none");

  _this.closest(".dropdown-product__item").fadeOut(400, function () {
    let parentWrap = _this.closest(".basket_hover_block");
    let visibleItems = parentWrap.find(".dropdown-product__item:visible");

    $(this).remove();
    if (!visibleItems.length) {
      parentWrap.html("");
    }
  });
  itemAction.updateState();
});
/**/
