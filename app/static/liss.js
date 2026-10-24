(function () {
  'use strict';
  var root = document.documentElement;
  var themeButton = document.querySelector('[data-theme-toggle]');
  function updateThemeButton() {
    var light = root.dataset.theme === 'light';
    if (themeButton) {
      themeButton.setAttribute('aria-label', light ? '切换为深色主题' : '切换为浅色主题');
      themeButton.setAttribute('title', light ? '切换为深色主题' : '切换为浅色主题');
    }
    var meta = document.getElementById('theme-color');
    if (meta) meta.content = light ? '#f5f7f8' : '#0e151e';
  }
  if (themeButton) themeButton.addEventListener('click', function () {
    root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
    try { localStorage.setItem('liss-theme', root.dataset.theme); } catch (e) {}
    updateThemeButton();
  });
  updateThemeButton();
  document.querySelectorAll('[data-dismiss-message]').forEach(function (button) {
    button.addEventListener('click', function () { button.closest('.site-flash').remove(); });
  });
  document.querySelectorAll('[data-avatar-image]').forEach(function (image) {
    function fallback() { image.hidden = true; }
    image.addEventListener('error', fallback);
    if (image.complete && !image.naturalWidth) fallback();
  });
  function openWriting() {
    if (location.hash !== '#write') return;
    var composer = document.getElementById('write');
    if (!composer) return;
    composer.open = true;
    var input = composer.querySelector('textarea');
    if (input) input.focus({ preventScroll: true });
    composer.scrollIntoView({ block: 'start' });
    document.dispatchEvent(new Event('liss:write'));
  }
  openWriting();
  window.addEventListener('hashchange', openWriting);
  document.querySelectorAll('a[href$="#write"]').forEach(function (link) {
    link.addEventListener('click', function () { if (location.hash === '#write') openWriting(); });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') document.querySelectorAll('.account-menu[open]').forEach(function (menu) { menu.open = false; menu.querySelector('summary').focus(); });
  });
  document.addEventListener('click', function (event) {
    document.querySelectorAll('.account-menu[open]').forEach(function (menu) { if (!menu.contains(event.target)) menu.open = false; });
  });
  document.querySelectorAll('.site-form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var pending = form.querySelector('.form-pending');
      if (pending) pending.textContent = '正在提交…';
      form.setAttribute('aria-busy', 'true');
    });
  });
})();
