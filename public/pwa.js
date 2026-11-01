'use strict';
(() => {
  let installPrompt = null;
  let workerReady = false;
  const button = document.getElementById('pwa-install');
  const dialog = document.getElementById('pwa-help');
  const status = document.getElementById('pwa-status');
  const banner = document.getElementById('pwa-offline');
  function installed() { return matchMedia('(display-mode: standalone)').matches || navigator.standalone === true; }
  function update() {
    button.hidden = installed();
    status.textContent = installed() ? 'Ariel instalada' : installPrompt ? 'Lista para instalar' : workerReady ? 'Aplicación preparada' : 'Acceso como aplicación';
    banner.hidden = navigator.onLine;
  }
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; update(); });
  window.addEventListener('appinstalled', () => { installPrompt = null; button.hidden = true; status.textContent = 'Ariel instalada'; });
  window.addEventListener('online', update);
  window.addEventListener('offline', update);
  button.addEventListener('click', async () => {
    if (installPrompt) {
      const prompt = installPrompt;
      installPrompt = null;
      await prompt.prompt();
      await prompt.userChoice;
      update();
    } else dialog.showModal();
  });
  update();
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js', {scope:'/'}).then(() => navigator.serviceWorker.ready).then(() => { workerReady = true; update(); }).catch(() => {
      status.textContent = 'No se pudo activar el acceso sin conexión';
    });
  }
})();
