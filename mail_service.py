import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from database import get_db, get_cursor


def get_smtp_config():
    """Return smtp_config row as dict, or None if table empty/missing."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM smtp_config ORDER BY id LIMIT 1')
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def queue_mail(to_email, to_name, subject, body_html, module_code=None, ref_id=None):
    """Insert a pending mail into mail_queue. Safe to call from any view."""
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


def get_user_email_by_id(user_id):
    """Return (email, username) for a user id."""
    if not user_id:
        return None, None
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT email, username FROM users WHERE id=%s', [user_id])
    row = cur.fetchone()
    conn.close()
    return (row['email'], row['username']) if row else (None, None)


def get_module_approver_info(module_code, fallback_module=None):
    """Return approver config and contact details for a module."""
    from database import get_module_config

    cfg = get_module_config(module_code) or {}
    approver_id = cfg.get('approver_id')
    approval_add = cfg.get('approval_add')

    if fallback_module:
        fallback_cfg = get_module_config(fallback_module) or {}
        if not approver_id:
            approver_id = fallback_cfg.get('approver_id')
        if approval_add is None:
            approval_add = fallback_cfg.get('approval_add')

    email, username = get_user_email_by_id(approver_id)
    return {
        'approver_id': approver_id,
        'approval_add': bool(approval_add),
        'email': email,
        'username': username,
    }


def trigger_mail_processing():
    """Kick off an asynchronous mail send attempt."""
    threading.Thread(target=process_mail_queue, daemon=True).start()


def notify_module_approver(module_code, subject, body_html, ref_id=None, fallback_module=None):
    """Queue an approval mail to the configured approver and trigger processing."""
    info = get_module_approver_info(module_code, fallback_module=fallback_module)
    if not info.get('email'):
        return False
    queue_mail(
        info['email'],
        info.get('username'),
        subject,
        body_html,
        module_code,
        ref_id
    )
    trigger_mail_processing()
    return True


def process_mail_queue():
    """Called by scheduler. Reads smtp_config, sends all pending mails."""
    cfg = get_smtp_config()
    if not cfg or not cfg.get('is_enabled'):
        return  # kill-switch off

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
        msg['From'] = f"{cfg.get('from_name', 'Portbird - DPPL')} <{cfg['from_email']}>"
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
