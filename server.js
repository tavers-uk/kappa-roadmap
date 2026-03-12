const express = require('express');
const path    = require('path');
const { initDB, getAllTasks, addTask, updateTask, deleteTask, seedTasks } = require('./db');

const app  = express();
const PORT = process.env.PORT || 3000;
const ADMIN_PASS = process.env.ADMIN_PASSWORD || 'kappa2026';

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── ADMIN STATIC ─────────────────────────────────────────────────────────────
app.use('/admin', express.static(path.join(__dirname, 'admin')));

// ── AUTH MIDDLEWARE ───────────────────────────────────────────────────────────
function auth(req, res, next) {
  const token = req.headers['x-admin-token'];
  if (!token || token !== ADMIN_PASS) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
}

// ── PUBLIC API ───────────────────────────────────────────────────────────────
app.get('/api/tasks', (req, res) => {
  res.json(getAllTasks());
});

// ── ADMIN API ────────────────────────────────────────────────────────────────
app.post('/api/auth', (req, res) => {
  const { password } = req.body;
  if (password === ADMIN_PASS) return res.json({ ok: true, token: ADMIN_PASS });
  res.status(401).json({ error: 'Invalid password' });
});

app.post('/api/tasks', auth, (req, res) => {
  const id = addTask(req.body);
  res.json({ id });
});

app.put('/api/tasks/:id', auth, (req, res) => {
  updateTask(req.params.id, req.body);
  res.json({ ok: true });
});

app.delete('/api/tasks/:id', auth, (req, res) => {
  deleteTask(req.params.id);
  res.json({ ok: true });
});

// ── SEED CHECK ───────────────────────────────────────────────────────────────
app.post('/api/seed', auth, (req, res) => {
  const result = seedTasks(req.body.tasks || []);
  res.json(result);
});

// ── BOOT ─────────────────────────────────────────────────────────────────────
initDB();
app.listen(PORT, () => console.log(`KCS Roadmap live → http://localhost:${PORT}`));
