'use strict';
(() => {
  const originalFetch = window.fetch.bind(window);
  const sessionReady = originalFetch('/api/auth/session', {cache:'no-store'}).then(async response => {
    if (!response.ok) { location.replace('/login'); return null; }
    return response.json();
  }).catch(() => null);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = String(init.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
    if (url.origin === location.origin && !['GET','HEAD','OPTIONS'].includes(method)) {
      const session = await sessionReady;
      if (!session) throw new Error('No se pudo verificar tu sesión. Volvé a ingresar.');
      const headers = new Headers(init.headers || (typeof input !== 'string' && input.headers) || {});
      headers.set('X-CSRF-Token', session.csrfToken);
      init = {...init, headers};
    }
    const response = await originalFetch(input, init);
    if (url.origin === location.origin && response.status === 401) location.replace('/login');
    return response;
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('owner-logout').addEventListener('click', async () => {
      const response = await fetch('/api/auth/logout', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if (response.ok) location.replace('/login');
    });
  });
})();
