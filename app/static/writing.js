(function () {
  'use strict';
  if (!window.EasyMDE || !window.DOMPurify) return;

  var paths = {
    heading: '<path d="M5 5v14m14-14v14M5 12h14"/>',
    bold: '<path d="M7 4h6a4 4 0 0 1 0 8H7zm0 8h7a4 4 0 0 1 0 8H7z"/>',
    italic: '<path d="M10 4h9M5 20h9M15 4 9 20"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    quote: '<path d="M4 7h6v6H4zm10 0h6v6h-6zM10 13c0 4-2 5-4 5m14-5c0 4-2 5-4 5"/>',
    code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 4l-4 16"/>',
    link: '<path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 0 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    preview: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    split: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>',
    fullscreen: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>'
  };
  function icon(name) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>';
  }
  function tool(name, title, action, glyph) {
    return { name: name, title: title, action: action, icon: icon(glyph || name), className: 'writing-tool' };
  }
  // Preview is untrusted input too. Keep basic Markdown and reject inline image
  // data; the Python sanitizer is still authoritative when an article is saved.
  window.DOMPurify.addHook('uponSanitizeAttribute', function (node, data) {
    if (data.attrName === 'src' && !/^https?:\/\//i.test(data.attrValue)) data.keepAttr = false;
  });
  document.querySelectorAll('.writing-field textarea').forEach(function (textarea) {
    var field = textarea.closest('.writing-field');
    var form = textarea.form;
    var editor;
    try {
      editor = new window.EasyMDE({
        element: textarea,
        autoDownloadFontAwesome: false,
        spellChecker: false,
        nativeSpellcheck: false,
        forceSync: true,
        autosave: { enabled: false },
        uploadImage: false,
        sideBySideFullscreen: false,
        minHeight: '300px',
        maxHeight: '480px',
        placeholder: textarea.placeholder,
        renderingConfig: {
          singleLineBreaks: false,
          sanitizerFunction: function (html) {
            return window.DOMPurify.sanitize(html, {
              ALLOWED_TAGS: ['a', 'b', 'blockquote', 'br', 'code', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 'strong', 'ul'],
              ALLOWED_ATTR: ['alt', 'title', 'href', 'src'],
              ALLOW_DATA_ATTR: false,
              ALLOW_ARIA_ATTR: false
            });
          }
        },
        promptURLs: true,
        promptTexts: { link: '粘贴链接地址（https://…）', image: '粘贴图片的 HTTPS 直链（不支持本地上传）' },
        insertTexts: { image: ['![图片说明](', ')'], link: ['[链接文字](', ')'] },
        toolbar: [
          tool('heading', '标题', window.EasyMDE.toggleHeadingSmaller),
          tool('bold', '粗体', window.EasyMDE.toggleBold),
          tool('italic', '斜体', window.EasyMDE.toggleItalic),
          '|', tool('unordered-list', '列表', window.EasyMDE.toggleUnorderedList, 'list'),
          tool('quote', '引用', window.EasyMDE.toggleBlockquote),
          tool('code', '代码块', window.EasyMDE.toggleCodeBlock),
          '|', tool('link', '插入链接', window.EasyMDE.drawLink),
          tool('image', '插入图片链接', window.EasyMDE.drawImage),
          '|', tool('preview', '预览', window.EasyMDE.togglePreview),
          tool('side-by-side', '分屏预览', function (instance) {
            if (window.innerWidth < 600) window.EasyMDE.togglePreview(instance);
            else window.EasyMDE.toggleSideBySide(instance);
          }, 'split'),
          tool('fullscreen', '全屏写作', window.EasyMDE.toggleFullScreen)
        ],
        status: [{
          className: 'writing-character-count',
          defaultValue: function (el) { el.textContent = Array.from(textarea.value).length + ' 字符'; },
          onUpdate: function (el) { el.textContent = Array.from(editor.value()).length + ' 字符'; }
        }]
      });
      field.classList.add('is-enhanced');
      var input = editor.codemirror.getInputField();
      input.setAttribute('aria-label', '文章正文，Markdown 编辑器');
      input.setAttribute('aria-describedby', textarea.getAttribute('aria-describedby'));
      editor.gui.toolbar.setAttribute('aria-label', '文章格式工具栏');
      editor.gui.toolbar.querySelectorAll('button').forEach(function (button) {
        button.setAttribute('aria-label', button.title);
      });
      form.addEventListener('submit', function () { editor.codemirror.save(); });
      function refresh() {
        editor.codemirror.refresh();
        if (location.hash === '#write') editor.codemirror.focus();
      }
      var composer = field.closest('details');
      if (composer) composer.addEventListener('toggle', function () { if (composer.open) refresh(); });
      window.addEventListener('hashchange', function () { if (location.hash === '#write') refresh(); });
      window.matchMedia('(max-width: 600px)').addEventListener('change', function (event) {
        if (event.matches && editor.isSideBySideActive()) editor.toggleSideBySide();
        editor.codemirror.refresh();
      });
      document.addEventListener('liss:write', refresh);
      if (!composer || composer.open) refresh();
    } catch (error) {
      // A failed enhancement must leave the native, server-backed form usable.
      if (editor) editor.toTextArea();
      field.classList.remove('is-enhanced');
    }
  });
})();
