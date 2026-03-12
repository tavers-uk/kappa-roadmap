const fs   = require('fs');
const path = require('path');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, 'data', 'roadmap.json');
let _data = null;

// ── INTERNAL ─────────────────────────────────────────────────────────────────
function _read() {
  if (_data) return _data;
  if (fs.existsSync(DB_PATH)) {
    _data = JSON.parse(fs.readFileSync(DB_PATH, 'utf8'));
  } else {
    _data = { nextId: 1, tasks: [] };
  }
  return _data;
}

function _write() {
  fs.writeFileSync(DB_PATH, JSON.stringify(_data, null, 2), 'utf8');
}

// ── PUBLIC API ───────────────────────────────────────────────────────────────
function initDB() {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  _read();
  _write();
  console.log('[DB] Initialized →', DB_PATH);
}

function getAllTasks() {
  const d = _read();
  return [...d.tasks].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id);
}

function getTaskCount() {
  return _read().tasks.length;
}

function addTask({ cat, entity, task, ett, platform, tags, status, sort_order }) {
  const d = _read();
  const now = new Date().toISOString();
  const t = {
    id: d.nextId++,
    cat, entity, task,
    ett: ett || '', platform: platform || '',
    tags: tags || '', status: status || 'PLANNED',
    sort_order: sort_order || 0,
    created_at: now, updated_at: now
  };
  d.tasks.push(t);
  _write();
  return t.id;
}

function updateTask(id, { cat, entity, task, ett, platform, tags, status, sort_order }) {
  const d = _read();
  const t = d.tasks.find(x => x.id === Number(id));
  if (!t) return false;
  Object.assign(t, {
    cat, entity, task,
    ett: ett || '', platform: platform || '',
    tags: tags || '', status: status || 'PLANNED',
    sort_order: sort_order || 0,
    updated_at: new Date().toISOString()
  });
  _write();
  return true;
}

function deleteTask(id) {
  const d = _read();
  const idx = d.tasks.findIndex(x => x.id === Number(id));
  if (idx === -1) return false;
  d.tasks.splice(idx, 1);
  _write();
  return true;
}

function seedTasks(tasks) {
  const d = _read();
  if (d.tasks.length > 0) return { skipped: true, count: d.tasks.length };
  const now = new Date().toISOString();
  tasks.forEach((t, i) => {
    d.tasks.push({
      id: d.nextId++,
      cat: t.cat, entity: t.entity, task: t.task,
      ett: t.ett || '', platform: t.platform || '',
      tags: t.tags || '', status: t.status || 'PLANNED',
      sort_order: i, created_at: now, updated_at: now
    });
  });
  _write();
  return { seeded: tasks.length };
}

module.exports = { initDB, getAllTasks, getTaskCount, addTask, updateTask, deleteTask, seedTasks };
