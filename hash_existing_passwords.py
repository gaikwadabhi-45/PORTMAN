"""
One-time utility: hash all plaintext passwords in the users table.

Run once after deploying the password-hashing changes:
    python hash_existing_passwords.py

Safe to run multiple times — already-hashed passwords are skipped.
"""
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash
from config import DATABASE_URL


def main():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, username, password FROM users WHERE password IS NOT NULL AND password <> ''")
    users = cur.fetchall()

    skipped = 0
    updated = 0

    for user in users:
        pw = user['password']
        if pw.startswith(('pbkdf2:', 'scrypt:')):
            skipped += 1
            continue
        hashed = generate_password_hash(pw)
        cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, user['id']))
        print(f"  Hashed password for user '{user['username']}' (id={user['id']})")
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nDone. Updated: {updated}, Already hashed (skipped): {skipped}")


if __name__ == '__main__':
    main()
