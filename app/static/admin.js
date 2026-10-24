(() => {
  'use strict';
  document.querySelectorAll('.admin-main form[method="post"]').forEach((form) => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      if (!button || !form.checkValidity()) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = '正在处理…';
    });
  });
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('.admin-main button[aria-busy="true"]').forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = button.dataset.originalLabel || '继续';
    });
  });
  document.querySelectorAll('.admin-main button[type="submit"]').forEach((button) => {
    button.dataset.originalLabel = button.textContent;
  });
})();
