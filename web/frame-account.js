'use strict';

// Register before the iframe load event, when the workbench forwards its account.
window.vehicleConfigFrameAccount = (() => {
  if (window.parent === window) return null;
  if (window.frameElement?.matches('#frame-container > iframe[data-app="vehicle-config"]')) {
    document.documentElement.classList.add('planhub-embedded');
  }
  let identity = null, switching = false, resolve;
  const ready = new Promise(done => { resolve = done; });
  window.addEventListener('message', event => {
    if (switching || event.source !== window.parent || event.origin !== location.origin) return;
    const data = event.data;
    if (!data || typeof data !== 'object' || !Object.hasOwn(data, 'account')) return;
    const field = value => typeof value === 'string' ? value.trim() : '';
    const account = field(data.account), roleName = field(data.roleName);
    const roleCode = Number.isSafeInteger(data.roleCode) ? String(data.roleCode) : field(data.roleCode);
    if (identity) {
      if (identity.account === account && identity.roleCode === roleCode && identity.roleName === roleName) return;
      identity = null;
      switching = true;
      document.documentElement.inert = true;
      location.reload();
      return;
    }
    if (![account,roleCode,roleName].every(value => value && value.length <= 100 && !/[\x00-\x1f\x7f]/.test(value))) return;
    identity = Object.freeze({account,roleCode,roleName});
    resolve(identity);
  });
  return {ready, get current() { return identity; }};
})();
