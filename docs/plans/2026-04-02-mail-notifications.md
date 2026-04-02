# Mail Notifications Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an async mail queue system that sends LDUD01 closure/reopen notifications via Outlook SMTP, with an admin UI for SMTP config and queue monitoring.

**Architecture:** A `mail_queue` DB table acts as the outbox. LDUD01 views write rows into it on closure/reopen events. APScheduler polls every N minutes (configurable), reads `smtp_config`, and flushes pending rows via `smtplib`. The kill-switch (`is_enabled=False`) makes local builds safe.

**Tech Stack:** Flask, PostgreSQL, APScheduler 3.x, smtplib (stdlib), Alembic migrations

---

### Task 1: Alembic Migration — mail_queue + smtp_config + users.email

**Files:**
- Create: `alembic/versions/a1b2c3d4e5f6_mail_queue_smtp_config.py`

**Step 1: Create the migration file**

```python
"""Add mail_queue, smtp_config tables and users.email column

Revision ID: a1b2c3d4e5f6
Revises: z6a7b8c9d0e1
Create Date: 2026-04-02
"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'z6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    # users email column
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")

    # smtp_config — single-row settings
    op.execute("""
        CREATE TABLE IF NOT EXISTS smtp_config (
            id SERIAL PRIMARY KEY,
            host TEXT NOT NULL DEFAULT 'smtp-mail.outlook.com',
            port INTEGER NOT NULL DEFAULT 587,
            username TEXT,
            password TEXT,
            from_email TEXT,
            from_name TEXT DEFAULT 'PORTMAN',
            use_tls BOOLEAN NOT NULL DEFAULT TRUE,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            schedule_minutes INTEGER NOT NULL DEFAULT 5,
            updated_by TEXT,
            updated_at TIMESTAMP
        )
    """)

    # mail_queue — outbox
    op.execute("""
        CREATE TABLE IF NOT EXISTS mail_queue (
            id SERIAL PRIMARY KEY,
            to_email TEXT NOT NULL,
            to_name TEXT,
            subject TEXT NOT NULL,
            body_html TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            sent_at TIMESTAMP,
            error_message TEXT,
            module_code TEXT,
            ref_id INTEGER
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_mail_queue_status ON mail_queue(status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS mail_queue")
    op.execute("DROP TABLE IF EXISTS smtp_config")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email")
```

**Step 2: Run the migration**

```bash
cd d:/PORTMAN
alembic upgrade head
```

Expected: `Running upgrade z6a7b8c9d0e1 -> a1b2c3d4e5f6`

**Step 3: Commit**

```bash
git add alembic/versions/a1b2c3d4e5f6_mail_queue_smtp_config.py
git commit -m "feat: migration for mail_queue, smtp_config, users.email"
```

---

### Task 2: mail_service.py

**Files:**
- Create: `mail_service.py`

**Step 1: Write mail_service.py**

```python
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from database import get_db, get_cursor


def get_smtp_config():
    """Return smtp_config row as dict, or None if not configured."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM smtp_config ORDER BY id LIMIT 1')
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def queue_mail(to_email, to_name, subject, body_html, module_code=None, ref_id=None):
    """Insert a pending mail into the queue. Safe to call from any view."""
    if not to_email:
        return
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        INSERT INTO mail_queue (to_email, to_name, subject, body_html, module_code, ref_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, [to_email, to_name, subject, body_html, module_code, ref_id])
    conn.commit()
    conn.close()


def process_mail_queue():
    """Called by scheduler. Reads smtp_config, sends all pending mails."""
    cfg = get_smtp_config()
    if not cfg or not cfg.get('is_enabled'):
        return  # kill-switch: do nothing when disabled

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT * FROM mail_queue
        WHERE status = 'pending' AND retry_count < max_retries
        ORDER BY created_at
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    for mail in rows:
        _send_one(mail, cfg)


def _send_one(mail, cfg):
    """Send a single mail row. Updates status in DB."""
    conn = get_db()
    cur = get_cursor(conn)
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = mail['subject']
        msg['From'] = f"{cfg.get('from_name', 'PORTMAN')} <{cfg['from_email']}>"
        msg['To'] = mail['to_email']
        msg.attach(MIMEText(mail['body_html'], 'html'))

        server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=15)
        if cfg.get('use_tls'):
            server.starttls()
        server.login(cfg['username'], cfg['password'])
        server.sendmail(cfg['from_email'], [mail['to_email']], msg.as_string())
        server.quit()

        cur.execute("""
            UPDATE mail_queue SET status='sent', sent_at=%s, error_message=NULL WHERE id=%s
        """, [datetime.now(), mail['id']])
    except Exception as e:
        new_retry = mail['retry_count'] + 1
        new_status = 'failed' if new_retry >= mail['max_retries'] else 'pending'
        cur.execute("""
            UPDATE mail_queue SET retry_count=%s, status=%s, error_message=%s WHERE id=%s
        """, [new_retry, new_status, str(e)[:500], mail['id']])
    finally:
        conn.commit()
        conn.close()
```

**Step 2: Commit**

```bash
git add mail_service.py
git commit -m "feat: mail_service queue_mail and process_mail_queue"
```

---

### Task 3: Wire APScheduler into app.py

**Files:**
- Modify: `app.py` — add scheduler startup after app is created, before `if __name__ == '__main__'`

**Step 1: Add scheduler boot code to app.py**

Find the line `if __name__ == '__main__':` and insert above it:

```python
# ── Mail queue scheduler ──────────────────────────────────────────────────────
from apscheduler.schedulers.background import BackgroundScheduler
from mail_service import process_mail_queue, get_smtp_config as _get_smtp_cfg

def _mail_tick():
    """Wrapper that re-reads schedule_minutes each tick (no restart needed)."""
    try:
        process_mail_queue()
    except Exception:
        pass  # never crash the scheduler

_mail_scheduler = BackgroundScheduler(daemon=True)
_mail_scheduler.add_job(
    _mail_tick,
    trigger='interval',
    minutes=5,      # bootstrap interval — overridden dynamically below
    id='mail_queue',
    replace_existing=True,
    max_instances=1,
)

def _reschedule_mail_job():
    """Re-read schedule_minutes from DB and reschedule if changed."""
    try:
        cfg = _get_smtp_cfg()
        mins = int(cfg.get('schedule_minutes', 5)) if cfg else 5
        mins = max(1, mins)
        job = _mail_scheduler.get_job('mail_queue')
        if job:
            _mail_scheduler.reschedule_job('mail_queue', trigger='interval', minutes=mins)
    except Exception:
        pass

_mail_scheduler.start()
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "feat: wire APScheduler mail queue processor into app.py"
```

---

### Task 4: LDUD01 notification hooks

**Files:**
- Modify: `modules/LDUD01/views.py` — add `queue_mail` calls after `close_record` and `reopen_record`

**Step 1: Add helper to look up user email by user_id**

Add this near the top of `modules/LDUD01/views.py` (after existing imports):

```python
from mail_service import queue_mail as _queue_mail

def _get_user_email(user_id):
    """Return (email, username) for a user_id, or (None, None)."""
    if not user_id:
        return None, None
    from database import get_db, get_cursor
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT email, username FROM users WHERE id=%s', [user_id])
    row = cur.fetchone()
    conn.close()
    return (row['email'], row['username']) if row else (None, None)

def _get_closer_email(record_id):
    """Return email of the last user who closed this LDUD record."""
    from database import get_db, get_cursor
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT actioned_by FROM approval_log
        WHERE module_code='LDUD01' AND record_id=%s
          AND action IN ('Closed','Partial Close')
        ORDER BY actioned_at DESC LIMIT 1
    """, [record_id])
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, None
    # actioned_by is username — look up email
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT email, username FROM users WHERE username=%s', [row['actioned_by']])
    row2 = cur.fetchone()
    conn.close()
    return (row2['email'], row2['username']) if row2 else (None, row['actioned_by'])
```

**Step 2: Queue mail after closure (in the `close` route)**

In the `close` route, after `model.close_record(record_id, close_type, ...)`:

```python
# Notify approver of closure
try:
    from database import get_module_config
    cfg = get_module_config('LDUD01')
    approver_id = cfg.get('approver_id')
    approver_email, approver_name = _get_user_email(approver_id)
    if approver_email:
        _queue_mail(
            to_email=approver_email,
            to_name=approver_name,
            subject=f"[PORTMAN] LDUD01 Record {record_id} — {close_type}",
            body_html=f"""
            <p>Hello {approver_name or 'Approver'},</p>
            <p>LDUD01 record <strong>#{record_id}</strong> has been marked as
            <strong>{close_type}</strong> by <strong>{session.get('username')}</strong>.</p>
            <p>Please review in PORTMAN.</p>
            <hr><p style="color:#888;font-size:11px;">This is an automated notification from PORTMAN.</p>
            """,
            module_code='LDUD01',
            ref_id=record_id,
        )
except Exception:
    pass  # never block the main response
```

**Step 3: Queue mail after reopen (in the `reopen` route)**

After `model.reopen_record(record_id, comment, ...)`:

```python
# Notify the operator who last closed this record
try:
    closer_email, closer_name = _get_closer_email(record_id)
    if closer_email:
        _queue_mail(
            to_email=closer_email,
            to_name=closer_name,
            subject=f"[PORTMAN] LDUD01 Record {record_id} — Sent Back to Draft",
            body_html=f"""
            <p>Hello {closer_name or ''},</p>
            <p>LDUD01 record <strong>#{record_id}</strong> has been <strong>sent back to Draft</strong>
            by <strong>{session.get('username')}</strong>.</p>
            <p><strong>Reason:</strong> {comment}</p>
            <p>Please review and resubmit in PORTMAN.</p>
            <hr><p style="color:#888;font-size:11px;">This is an automated notification from PORTMAN.</p>
            """,
            module_code='LDUD01',
            ref_id=record_id,
        )
except Exception:
    pass
```

**Step 4: Commit**

```bash
git add modules/LDUD01/views.py
git commit -m "feat: queue mail notifications on LDUD01 closure and reopen"
```

---

### Task 5: Admin backend routes

**Files:**
- Modify: `modules/ADMIN/views.py`

**Step 1: Add SMTP config and mail queue endpoints**

```python
# ── SMTP Config ───────────────────────────────────────────────────────────────

@bp.route('/api/smtp-config')
@admin_required
def get_smtp_config():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM smtp_config ORDER BY id LIMIT 1')
    row = cur.fetchone()
    conn.close()
    return jsonify(dict(row) if row else {})


@bp.route('/api/smtp-config/save', methods=['POST'])
@admin_required
def save_smtp_config():
    data = request.json
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT id FROM smtp_config ORDER BY id LIMIT 1')
    existing = cur.fetchone()
    now = __import__('datetime').datetime.now()
    fields = ['host','port','username','password','from_email','from_name',
              'use_tls','is_enabled','schedule_minutes']
    vals = [data.get(f) for f in fields] + [session.get('username'), now]
    if existing:
        sets = ', '.join(f'{f}=%s' for f in fields)
        cur.execute(f'UPDATE smtp_config SET {sets}, updated_by=%s, updated_at=%s WHERE id=%s',
                    vals + [existing['id']])
    else:
        cols = ', '.join(fields + ['updated_by','updated_at'])
        phs = ', '.join('%s' for _ in fields + ['updated_by','updated_at'])
        cur.execute(f'INSERT INTO smtp_config ({cols}) VALUES ({phs})', vals)
    conn.commit()
    conn.close()
    # Reschedule the mail job with new interval
    try:
        from app import _reschedule_mail_job
        _reschedule_mail_job()
    except Exception:
        pass
    return jsonify({'success': True})


@bp.route('/api/mail-queue')
@admin_required
def get_mail_queue():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT id, to_email, to_name, subject, status, retry_count, max_retries,
               to_char(created_at, 'DD-MM-YYYY HH24:MI') AS created_at,
               to_char(sent_at,    'DD-MM-YYYY HH24:MI') AS sent_at,
               error_message, module_code, ref_id
        FROM mail_queue ORDER BY id DESC LIMIT 200
    """)
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/mail-queue/retry', methods=['POST'])
@admin_required
def retry_failed_mail():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        UPDATE mail_queue SET status='pending', retry_count=0, error_message=NULL
        WHERE status='failed'
    """)
    count = cur.rowcount
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'reset': count})


@bp.route('/api/mail-queue/send-now', methods=['POST'])
@admin_required
def send_mail_now():
    """Manually trigger the mail queue processor."""
    from mail_service import process_mail_queue
    try:
        process_mail_queue()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

**Step 2: Add email field to user endpoints**

In `add_user`, add email to insert:
```python
email = data.get('email', '').strip()
cur.execute('INSERT INTO users (username, password, email, is_admin) VALUES (%s, %s, %s, %s) RETURNING id',
            [username, password, email or None, is_admin])
```

In `get_users`, return email:
```python
cur.execute('SELECT id, username, email, is_admin FROM users')
```

**Step 3: Commit**

```bash
git add modules/ADMIN/views.py
git commit -m "feat: admin routes for smtp_config and mail_queue"
```

---

### Task 6: Admin UI — Mail tab + user email field

**Files:**
- Modify: `templates/admin.html`

**Step 1: Add Mail tab button**

Add to `.admin-tabs`:
```html
<button class="tab-btn" onclick="showTab('mail')">Mail</button>
```

**Step 2: Add Mail tab content** (after `banks-tab` div)

```html
<!-- Mail Tab -->
<div id="mail-tab" class="tab-content">
    <div class="admin-section" style="margin-bottom:16px;">
        <h3>SMTP Configuration</h3>
        <div class="toolbar" style="flex-wrap:wrap;gap:10px;align-items:center;">
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;">
                Mail Enabled:
                <label class="smtp-toggle">
                    <input type="checkbox" id="smtpEnabled">
                    <span class="smtp-slider"></span>
                </label>
            </label>
            <button class="btn btn-save" onclick="saveSmtpConfig()">Save Config</button>
            <button class="btn" style="background:#e2e8f0;color:#2d3748;border:1px solid #cbd5e0;" onclick="sendMailNow()">Send Now</button>
            <span id="smtpStatus" style="font-size:11px;"></span>
        </div>
        <div class="config-panel" style="display:block;margin-top:12px;">
            <div class="config-row" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
                <label>Host:<br><input type="text" id="smtpHost" placeholder="smtp-mail.outlook.com" style="width:100%;padding:6px;"></label>
                <label>Port:<br><input type="number" id="smtpPort" placeholder="587" style="width:100%;padding:6px;"></label>
                <label>Schedule (minutes):<br><input type="number" id="smtpSchedule" min="1" placeholder="5" style="width:100%;padding:6px;"></label>
            </div>
            <div class="config-row" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <label>Username:<br><input type="text" id="smtpUsername" placeholder="user@domain.com" style="width:100%;padding:6px;"></label>
                <label>Password:<br><input type="password" id="smtpPassword" placeholder="••••••••" style="width:100%;padding:6px;"></label>
            </div>
            <div class="config-row" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
                <label>From Email:<br><input type="text" id="smtpFromEmail" placeholder="noreply@domain.com" style="width:100%;padding:6px;"></label>
                <label>From Name:<br><input type="text" id="smtpFromName" placeholder="PORTMAN" style="width:100%;padding:6px;"></label>
                <label style="display:flex;align-items:center;gap:8px;padding-top:20px;">
                    <input type="checkbox" id="smtpUseTls"> Use TLS (STARTTLS)
                </label>
            </div>
        </div>
    </div>

    <div class="admin-section">
        <h3>Mail Queue</h3>
        <div class="toolbar" style="gap:8px;flex-wrap:wrap;">
            <button class="btn" style="background:#e2e8f0;color:#2d3748;border:1px solid #cbd5e0;" onclick="loadMailQueue()">↻ Refresh</button>
            <button class="btn" style="background:#fef3c7;color:#92400e;border:1px solid #fcd34d;" onclick="retryFailed()">Retry Failed</button>
            <span id="mailQueueCount" style="font-size:11px;color:#718096;margin-left:8px;"></span>
        </div>
        <table class="admin-table" id="mailQueueTable" style="margin-top:8px;">
            <thead>
                <tr>
                    <th>ID</th><th>To</th><th>Subject</th>
                    <th>Status</th><th>Retries</th>
                    <th>Created</th><th>Sent</th><th>Error</th>
                </tr>
            </thead>
            <tbody id="mailQueueBody"></tbody>
        </table>
    </div>
</div>
```

**Step 3: Add toggle CSS**

```css
/* SMTP enable/disable toggle */
.smtp-toggle { position:relative; display:inline-block; width:44px; height:24px; }
.smtp-toggle input { opacity:0; width:0; height:0; }
.smtp-slider { position:absolute; cursor:pointer; inset:0; background:#cbd5e0; border-radius:24px; transition:.3s; }
.smtp-slider:before { content:""; position:absolute; height:18px; width:18px; left:3px; bottom:3px; background:white; border-radius:50%; transition:.3s; }
.smtp-toggle input:checked + .smtp-slider { background:#48bb78; }
.smtp-toggle input:checked + .smtp-slider:before { transform:translateX(20px); }
```

**Step 4: Add email field to Add User modal**

In the Add User modal, after password field:
```html
<div class="form-group">
    <label>Email</label>
    <input type="email" id="newEmail" placeholder="user@domain.com">
</div>
```

Update `addUser()` JS to include email:
```javascript
const email = document.getElementById('newEmail').value.trim();
body: JSON.stringify({username, password, email, is_admin})
```

Update `hideAddUser()` to reset:
```javascript
document.getElementById('newEmail').value = '';
```

Add email column to users table display in `loadUsers()`:
```javascript
// Add Email column to thead (update the table header HTML too)
<th>Username</th><th>Email</th><th>Is Admin</th><th>Actions</th>
// In tbody rows:
<td>${u.email || '<span style="color:#a0aec0">—</span>'}</td>
```

**Step 5: Add JS for Mail tab**

```javascript
// ── Mail Tab ─────────────────────────────────────────────────────────────────
async function loadSmtpConfig() {
    const res = await fetch('/admin/api/smtp-config');
    const cfg = await res.json();
    document.getElementById('smtpEnabled').checked  = !!cfg.is_enabled;
    document.getElementById('smtpHost').value        = cfg.host || 'smtp-mail.outlook.com';
    document.getElementById('smtpPort').value        = cfg.port || 587;
    document.getElementById('smtpUsername').value    = cfg.username || '';
    document.getElementById('smtpPassword').value    = cfg.password || '';
    document.getElementById('smtpFromEmail').value   = cfg.from_email || '';
    document.getElementById('smtpFromName').value    = cfg.from_name || 'PORTMAN';
    document.getElementById('smtpUseTls').checked    = cfg.use_tls !== false;
    document.getElementById('smtpSchedule').value    = cfg.schedule_minutes || 5;
}

async function saveSmtpConfig() {
    const status = document.getElementById('smtpStatus');
    status.textContent = 'Saving...'; status.style.color = '#c05621';
    const data = {
        host:             document.getElementById('smtpHost').value.trim(),
        port:             parseInt(document.getElementById('smtpPort').value) || 587,
        username:         document.getElementById('smtpUsername').value.trim(),
        password:         document.getElementById('smtpPassword').value,
        from_email:       document.getElementById('smtpFromEmail').value.trim(),
        from_name:        document.getElementById('smtpFromName').value.trim() || 'PORTMAN',
        use_tls:          document.getElementById('smtpUseTls').checked,
        is_enabled:       document.getElementById('smtpEnabled').checked,
        schedule_minutes: parseInt(document.getElementById('smtpSchedule').value) || 5,
    };
    const res = await fetch('/admin/api/smtp-config/save', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
    });
    if (res.ok) { status.textContent = 'Saved!'; status.style.color = '#276749'; setTimeout(()=>status.textContent='',2000); }
    else { status.textContent = 'Failed to save'; status.style.color = '#c53030'; }
}

async function loadMailQueue() {
    const res = await fetch('/admin/api/mail-queue');
    const rows = await res.json();
    document.getElementById('mailQueueCount').textContent = `${rows.length} record(s)`;
    const statusColors = { pending:'#c05621', sent:'#276749', failed:'#c53030' };
    const statusBg     = { pending:'#feebc8', sent:'#c6f6d5', failed:'#fed7d7' };
    document.getElementById('mailQueueBody').innerHTML = rows.map(r => `
        <tr>
            <td>${r.id}</td>
            <td style="font-size:11px;">${r.to_email}<br><span style="color:#718096;">${r.to_name||''}</span></td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.subject}">${r.subject}</td>
            <td><span style="background:${statusBg[r.status]||'#e2e8f0'};color:${statusColors[r.status]||'#2d3748'};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">${r.status}</span></td>
            <td style="text-align:center;">${r.retry_count}/${r.max_retries}</td>
            <td style="font-size:11px;">${r.created_at||''}</td>
            <td style="font-size:11px;">${r.sent_at||'—'}</td>
            <td style="font-size:11px;color:#c53030;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.error_message||''}">${r.error_message||''}</td>
        </tr>
    `).join('');
}

async function retryFailed() {
    const res = await fetch('/admin/api/mail-queue/retry', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const d = await res.json();
    if (d.success) { alert(`Reset ${d.reset} failed mail(s) to pending.`); loadMailQueue(); }
}

async function sendMailNow() {
    const status = document.getElementById('smtpStatus');
    status.textContent = 'Sending...'; status.style.color = '#c05621';
    const res = await fetch('/admin/api/mail-queue/send-now', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    if (res.ok) { status.textContent = 'Done!'; status.style.color = '#276749'; loadMailQueue(); setTimeout(()=>status.textContent='',3000); }
    else { const d=await res.json(); status.textContent=d.error||'Failed'; status.style.color='#c53030'; }
}
```

**Step 6: Hook into showTab**

```javascript
if (tab === 'mail') { loadSmtpConfig(); loadMailQueue(); }
```

**Step 7: Commit**

```bash
git add templates/admin.html
git commit -m "feat: mail admin tab with SMTP config, queue viewer, user email field"
```

---

### Task 7: Smoke test

**Step 1:** Set `is_enabled=False` in admin → trigger LDUD closure → confirm row appears in mail_queue with status `pending` but nothing is sent.

**Step 2:** Enable SMTP, click **Send Now** → confirm row flips to `sent`.

**Step 3:** Reopen a closed LDUD record → confirm a new mail row is queued for the closer.

**Step 4:** Final commit**

```bash
git add -A
git commit -m "feat: LDUD01 mail notifications with async queue and admin UI"
```
