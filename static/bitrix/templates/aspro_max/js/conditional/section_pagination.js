BX.addCustomEvent('onAsproAjaxPagination', (eventdata) => {
  if (eventdata?.url) {
    const pagerURL = new URL(window.location.origin + eventdata?.url);
    window.history.replaceState(null, '', pagerURL);
  }
});
