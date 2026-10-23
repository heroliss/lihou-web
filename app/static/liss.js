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
  // PageDown 1.0 predates fenced_code. Escape fenced content before conversion;
  // its existing sanitizing converter still processes the complete HTML output.
  if (window.Markdown && typeof window.Markdown.getSanitizingConverter === 'function') {
    var previewConverter = window.Markdown.getSanitizingConverter();
    previewConverter.hooks.chain('preConversion', function (source) {
      var fences = /^(\x60{3,}|~{3,})[ ]*(?:\{[^\n]*\}|\.?[\w#.+-]*[ ]*(?:hl_lines=(?:"[^"\n]*"|'[^'\n]*')[ ]*)?)\n([\s\S]*?)^\1[ ]*$/gm;
      return source.replace(fences, function (block, fence, code) {
        var escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return '\n\n<pre><code>' + escaped + '</code></pre>\n\n';
      });
    });
    window.flask_pagedown_converter = function (source) { return previewConverter.makeHtml(source); };
  }
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
  }
  openWriting();
  window.addEventListener('hashchange', openWriting);
  document.querySelectorAll('a[href$="#write"]').forEach(function (link) {
    link.addEventListener('click', function () { if (location.hash === '#write') openWriting(); });
  });
  document.querySelectorAll('.flask-pagedown-preview').forEach(function (preview) {
    preview.setAttribute('role', 'region');
    preview.setAttribute('aria-label', 'Markdown 实时预览');
  });
  window.addEventListener('load', function () {
    document.querySelectorAll('.flask-pagedown-input').forEach(function (input) {
      input.addEventListener('input', function () { if (typeof input.onkeyup === 'function') input.onkeyup(); });
    });
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
