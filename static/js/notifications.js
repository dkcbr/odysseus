// static/js/notifications.js — Notification toast stack (ES6)
// Minimal, real subsystem: notify({level, message}) creates a toast,
// stacks it in a fixed-position container, auto-dismisses with a fade.
// No modalManager involvement -- this isn't a panel, it's a persistent
// overlay element, styled with the same hardcoded hex HUD palette used
// throughout tonight (no CSS custom properties that don't exist).

import { addNotification } from './notifications_store.js';

const LEVEL_COLORS = {
  info: '#4ad4e8',
  warning: '#ff8c3a',
  error: '#ff4a5e',
};

const AUTO_DISMISS_MS = 6000;

function _ensureStack() {
  let stack = document.getElementById('jarvis-notification-stack');
  if (stack) return stack;
  stack = document.createElement('div');
  stack.id = 'jarvis-notification-stack';
  stack.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:320px;pointer-events:none;';
  document.body.appendChild(stack);
  return stack;
}

export function notify({ level = 'info', message = '', agents = [], servers = [] } = {}) {
  if (!message) return;
  addNotification({ level, message, agents, servers }); // real persistence, before the ephemeral toast
  const color = LEVEL_COLORS[level] || LEVEL_COLORS.info;
  const stack = _ensureStack();

  const toast = document.createElement('div');
  toast.style.cssText = `pointer-events:auto;background:#08111c;border:1px solid ${color};border-radius:4px;padding:10px 12px;font-size:12px;color:#e6f7ff;box-shadow:0 0 8px ${color}55;opacity:0;transform:translateX(20px);transition:opacity 0.25s ease, transform 0.25s ease;`;
  toast.innerHTML = `<span style="color:${color};font-weight:600;text-transform:uppercase;font-size:10px;margin-right:6px;">${level}</span>${message.replace(/</g, '&lt;')}`;

  stack.appendChild(toast);
  // Trigger the fade-in on next frame.
  requestAnimationFrame(() => {
    toast.style.opacity = '1';
    toast.style.transform = 'translateX(0)';
  });

  const remove = () => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(20px)';
    setTimeout(() => toast.remove(), 300);
  };
  toast.addEventListener('click', remove);
  setTimeout(remove, AUTO_DISMISS_MS);
}

export function init() {
  _ensureStack();
}

export default { init, notify };
