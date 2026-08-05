// static/js/notifications_store.js — real in-memory notification history.
// Plain ES6 module, no backend, no persistence across page reload (by
// design -- this is a session-scoped log, not a database). Every
// notify() call from notifications.js pushes an entry here first, then
// the toast renders as before. This exists specifically so a future
// history UI (drawer, panel, whatever) has real, structured data to
// read instead of nothing -- notify() itself only ever produced
// ephemeral toasts before this.

const MAX_NOTIFICATIONS = 200;
const _notifications = [];
let _nextId = 1;

// agents/servers are arrays because real alerts are grouped (e.g. a
// single disconnect notification can name several servers at once) --
// a single scalar field would lose that real shape.
export function addNotification({ level = 'info', message = '', agents = [], servers = [] } = {}) {
  const entry = {
    id: _nextId++,
    ts: Date.now() / 1000, // seconds, matching the real convention used everywhere else in this codebase
    level,
    message,
    agents: Array.isArray(agents) ? agents : [],
    servers: Array.isArray(servers) ? servers : [],
  };
  _notifications.push(entry);
  if (_notifications.length > MAX_NOTIFICATIONS) _notifications.shift();
  return entry;
}

export function getNotifications() {
  return _notifications.slice(); // copy -- callers must not mutate the real store directly
}

export function getNotificationsFiltered({ level, agent, server } = {}) {
  return _notifications.filter(n => {
    if (level && n.level !== level) return false;
    if (agent && !n.agents.includes(agent)) return false;
    if (server && !n.servers.includes(server)) return false;
    return true;
  });
}

export function clearNotifications() {
  _notifications.length = 0;
}

export function getAllAgents() {
  const set = new Set();
  _notifications.forEach(n => n.agents.forEach(a => set.add(a)));
  return [...set].sort();
}

export function getAllServers() {
  const set = new Set();
  _notifications.forEach(n => n.servers.forEach(s => set.add(s)));
  return [...set].sort();
}

export function getLevelCounts() {
  const counts = { all: _notifications.length, info: 0, warning: 0, error: 0 };
  _notifications.forEach(n => { if (counts[n.level] !== undefined) counts[n.level]++; });
  return counts;
}

export function getAgentCounts() {
  const map = new Map();
  _notifications.forEach(n => n.agents.forEach(a => map.set(a, (map.get(a) || 0) + 1)));
  return map;
}

export function getServerCounts() {
  const map = new Map();
  _notifications.forEach(n => n.servers.forEach(s => map.set(s, (map.get(s) || 0) + 1)));
  return map;
}

export default { addNotification, getNotifications, getNotificationsFiltered, clearNotifications, getAllAgents, getAllServers, getLevelCounts, getAgentCounts, getServerCounts };
