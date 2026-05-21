import sys
import os
import re
import subprocess
import datetime
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Add project root to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DATABASE_URL


def _backup_before_downgrade():
    """If this invocation is a downgrade, pg_dump first and ABORT the whole
    command if the backup can't be made. Upgrades / history / heads are
    untouched. Downgrades drop columns and tables here, so they must never
    run without a recoverable snapshot."""
    if 'downgrade' not in [a.lower() for a in sys.argv]:
        return

    # Hard gate so a downgrade can't be a one-keystroke accident.
    if os.environ.get('ALLOW_DOWNGRADE') != '1':
        sys.exit('[alembic] Downgrade is destructive and is blocked. '
                 'Re-run with ALLOW_DOWNGRADE=1 once you are certain.')

    # pg_dump accepts a libpq URL; strip any SQLAlchemy "+driver" suffix.
    dump_url = re.sub(r'^(postgresql)\+[a-z0-9]+://', r'\1://', DATABASE_URL)
    backup_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'db_backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out = os.path.join(backup_dir, f'predowngrade_{ts}.dump')

    print(f'[alembic] downgrade detected - backing up to {out} ...')
    try:
        subprocess.run(['pg_dump', '--format=custom', '--file', out, dump_url], check=True)
    except FileNotFoundError:
        sys.exit('[alembic] ABORT: pg_dump not on PATH; refusing to downgrade without a backup.')
    except subprocess.CalledProcessError as e:
        sys.exit(f'[alembic] ABORT: backup failed (exit {e.returncode}); refusing to downgrade.')
    print(f'[alembic] backup OK: {out}\n  restore with:  pg_restore -d <dbname> "{out}"')


_backup_before_downgrade()

config = context.config
config.set_main_option('sqlalchemy.url', DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
