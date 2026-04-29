"""Restore PORTMAN schema after accidental JNPA migrations were applied

Revision ID: z0y9x8w7v6u5
Revises: c0d1e2f3a4b5
Create Date: 2026-04-29
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'z0y9x8w7v6u5'
down_revision: Union[str, None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Drop tables added by JNPA (jnpa03–jnpa05)                        #
    # ------------------------------------------------------------------ #
    op.execute('DROP TABLE IF EXISTS pipeline_terminal_mapping CASCADE')
    op.execute('DROP TABLE IF EXISTS pipeline_master CASCADE')
    op.execute('DROP TABLE IF EXISTS terminal_master CASCADE')
    op.execute('DROP TABLE IF EXISTS expected_vessels CASCADE')
    op.execute('DROP TABLE IF EXISTS tank_master CASCADE')

    # ------------------------------------------------------------------ #
    # 2. Drop columns added by JNPA on existing tables (jnpa05/06)        #
    # ------------------------------------------------------------------ #
    op.execute('ALTER TABLE vessel_agents DROP COLUMN IF EXISTS agent_code')
    op.execute('ALTER TABLE vessel_customers DROP COLUMN IF EXISTS customer_code')

    # ------------------------------------------------------------------ #
    # 3. Recreate tables dropped by jnpa01 (downgrade was intentional     #
    #    no-op in JNPA, so we must restore from PORTMAN schema)           #
    # ------------------------------------------------------------------ #

    # Masters (no FK dependencies)
    op.execute('''
        CREATE TABLE IF NOT EXISTS barges (
            id SERIAL PRIMARY KEY,
            barge_name TEXT NOT NULL UNIQUE,
            dwt REAL,
            barge_owner_name TEXT,
            barge_owner_email TEXT
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_master (
            id SERIAL PRIMARY KEY,
            mbc_name TEXT NOT NULL UNIQUE,
            dwt REAL,
            mbc_owner_name TEXT
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_doc_series (
            id SERIAL PRIMARY KEY,
            name TEXT
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS port_payloaders (
            id SERIAL PRIMARY KEY,
            name TEXT,
            make TEXT
        )
    ''')

    # mbc_header (parent for all mbc child tables)
    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_header (
            id SERIAL PRIMARY KEY,
            doc_num TEXT,
            doc_series TEXT,
            doc_date TEXT,
            mbc_name TEXT,
            operation_type TEXT,
            cargo_type TEXT,
            cargo_name TEXT,
            bl_quantity REAL,
            quantity_uom TEXT,
            doc_status TEXT DEFAULT 'Pending',
            created_by TEXT,
            created_date TEXT,
            load_port TEXT
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_delays (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL,
            delay_name TEXT,
            delay_account_type TEXT,
            equipment_name TEXT,
            start_datetime TEXT,
            end_datetime TEXT,
            total_time_mins REAL,
            total_time_hrs REAL,
            delays_to_sof TEXT,
            invoiceable TEXT,
            minus_delay_hours TEXT,
            FOREIGN KEY (mbc_id) REFERENCES mbc_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_load_port_lines (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL,
            arrived_load_port TEXT,
            alongside_berth TEXT,
            loading_commenced TEXT,
            loading_completed TEXT,
            cast_off_load_port TEXT,
            eta TEXT,
            FOREIGN KEY (mbc_id) REFERENCES mbc_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_discharge_port_lines (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL,
            arrival_gull_island TEXT,
            departure_gull_island TEXT,
            vessel_arrival_port TEXT,
            vessel_all_made_fast TEXT,
            unloading_commenced TEXT,
            cleaning_commenced TEXT,
            unloading_completed TEXT,
            vessel_cast_off TEXT,
            vessel_unloaded_by TEXT,
            vessel_unloading_berth TEXT,
            discharge_stop_shifting TEXT,
            discharge_start_shifting TEXT,
            cleaning_completed TEXT,
            arrived_yellow_crane TIMESTAMP,
            sailed_out_load_port TIMESTAMP,
            FOREIGN KEY (mbc_id) REFERENCES mbc_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_export_load_port_lines (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL,
            arrived_at_port TEXT,
            alongside_at_berth TEXT,
            loading_commenced TEXT,
            loading_completed TEXT,
            cast_off_from_berth TEXT,
            sailed_out_from_port TEXT,
            eta_at_gull_island TEXT,
            unloaded_by TEXT,
            berth_master TEXT,
            FOREIGN KEY (mbc_id) REFERENCES mbc_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_customer_details (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL,
            customer_name TEXT,
            bill_of_coastal_goods_no TEXT,
            quantity REAL,
            material_po TEXT,
            cargo_name VARCHAR(200),
            FOREIGN KEY (mbc_id) REFERENCES mbc_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_cleaning_details (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL,
            payloader_name TEXT,
            hmr_start TEXT,
            hmr_end TEXT,
            diesel_start TEXT,
            diesel_end TEXT,
            start_time TEXT,
            end_time TEXT,
            FOREIGN KEY (mbc_id) REFERENCES mbc_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS mbc_proof_documents (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL REFERENCES mbc_header(id) ON DELETE CASCADE,
            original_filename TEXT NOT NULL,
            file_bytes BYTEA NOT NULL,
            mime_type TEXT,
            uploaded_by TEXT,
            uploaded_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    op.execute('CREATE INDEX IF NOT EXISTS idx_mbc_proof_mbc_id ON mbc_proof_documents(mbc_id)')

    # vex tables
    op.execute('''
        CREATE TABLE IF NOT EXISTS vex_header (
            id SERIAL PRIMARY KEY,
            vex_doc_num TEXT,
            doc_series TEXT,
            vessel_name TEXT,
            customer_name TEXT,
            cargo_name TEXT,
            bill_of_coastal_goods_date TEXT,
            bill_of_coastal_goods_qty REAL,
            quantity_uom TEXT,
            doc_status TEXT DEFAULT 'Pending',
            created_by TEXT,
            created_date TEXT
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS vex_barge_lines (
            id SERIAL PRIMARY KEY,
            vex_id INTEGER NOT NULL,
            barge_name TEXT,
            FOREIGN KEY (vex_id) REFERENCES vex_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS vex_mbc_lines (
            id SERIAL PRIMARY KEY,
            vex_id INTEGER NOT NULL,
            mbc_name TEXT,
            FOREIGN KEY (vex_id) REFERENCES vex_header(id) ON DELETE CASCADE
        )
    ''')

    # ldud child tables (ldud_header already exists)
    op.execute('''
        CREATE TABLE IF NOT EXISTS ldud_barge_lines (
            id SERIAL PRIMARY KEY,
            ldud_id INTEGER NOT NULL,
            trip_number INTEGER,
            hold_name TEXT,
            barge_name TEXT,
            contractor_name TEXT,
            cargo_name TEXT,
            bpt_bfl TEXT,
            along_side_vessel TEXT,
            commenced_loading TEXT,
            completed_loading TEXT,
            cast_off_mv TEXT,
            anchored_gull_island TEXT,
            aweigh_gull_island TEXT,
            along_side_berth TEXT,
            commence_discharge_berth TEXT,
            completed_discharge_berth TEXT,
            cast_off_berth TEXT,
            cast_off_berth_nt TEXT,
            discharge_quantity REAL,
            crane_loaded_from TEXT,
            trip_start TEXT,
            amf_at_port TEXT,
            cast_off_port TEXT,
            port_crane TEXT,
            cast_off_loading_berth TEXT,
            anchored_gull_island_empty TEXT,
            aweigh_gull_island_empty TEXT,
            FOREIGN KEY (ldud_id) REFERENCES ldud_header(id) ON DELETE CASCADE
        )
    ''')

    op.execute('''
        CREATE TABLE IF NOT EXISTS ldud_barge_cleaning (
            id SERIAL PRIMARY KEY,
            ldud_id INTEGER NOT NULL,
            barge_name TEXT,
            payloader_name TEXT,
            hmr_start NUMERIC,
            hmr_end NUMERIC,
            diesel_start TEXT,
            diesel_end TEXT,
            start_time TEXT,
            end_time TEXT,
            FOREIGN KEY (ldud_id) REFERENCES ldud_header(id) ON DELETE CASCADE
        )
    ''')

    # Restore barge_name column on lueu_lines (was dropped by jnpa01)
    op.execute('ALTER TABLE lueu_lines ADD COLUMN IF NOT EXISTS barge_name TEXT')

    # ------------------------------------------------------------------ #
    # 4. Recreate table dropped by jnpa02                                 #
    # ------------------------------------------------------------------ #
    op.execute('''
        CREATE TABLE IF NOT EXISTS vcn_stowage_plan (
            id SERIAL PRIMARY KEY,
            vcn_id INTEGER NOT NULL,
            cargo_name TEXT,
            hatch_name TEXT,
            hold_name TEXT,
            hatchwise_quantity REAL,
            hatch_completion_time TIMESTAMP,
            FOREIGN KEY (vcn_id) REFERENCES vcn_header(id) ON DELETE CASCADE
        )
    ''')


def downgrade() -> None:
    # Re-apply JNPA changes in order (mirrors jnpa01 → jnpa07)

    # jnpa01
    op.execute('DROP TABLE IF EXISTS mbc_proof_documents CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_cleaning_details CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_customer_details CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_export_load_port_lines CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_discharge_port_lines CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_load_port_lines CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_delays CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_header CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_master CASCADE')
    op.execute('DROP TABLE IF EXISTS mbc_doc_series CASCADE')
    op.execute('DROP TABLE IF EXISTS vex_mbc_lines CASCADE')
    op.execute('DROP TABLE IF EXISTS vex_barge_lines CASCADE')
    op.execute('DROP TABLE IF EXISTS vex_header CASCADE')
    op.execute('DROP TABLE IF EXISTS ldud_barge_cleaning CASCADE')
    op.execute('DROP TABLE IF EXISTS ldud_barge_lines CASCADE')
    op.execute('DROP TABLE IF EXISTS barges CASCADE')
    op.execute('DROP TABLE IF EXISTS port_payloaders CASCADE')
    op.execute('ALTER TABLE lueu_lines DROP COLUMN IF EXISTS barge_name')

    # jnpa02
    op.execute('DROP TABLE IF EXISTS vcn_stowage_plan CASCADE')

    # jnpa03
    op.execute('''
        CREATE TABLE IF NOT EXISTS terminal_master (
            id SERIAL PRIMARY KEY,
            terminal_name TEXT NOT NULL UNIQUE,
            description TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    op.execute('''
        CREATE TABLE IF NOT EXISTS pipeline_master (
            id SERIAL PRIMARY KEY,
            pipeline_name TEXT NOT NULL UNIQUE,
            description TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    op.execute('''
        CREATE TABLE IF NOT EXISTS pipeline_terminal_mapping (
            id SERIAL PRIMARY KEY,
            pipeline_id INTEGER NOT NULL REFERENCES pipeline_master(id),
            terminal_id INTEGER NOT NULL REFERENCES terminal_master(id),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(pipeline_id, terminal_id)
        )
    ''')

    # jnpa04
    op.execute('''
        CREATE TABLE IF NOT EXISTS expected_vessels (
            id SERIAL PRIMARY KEY,
            terminal_name TEXT,
            vessel_name TEXT,
            via_number TEXT,
            loa NUMERIC(10,2),
            draft NUMERIC(10,2),
            agent_tank_consignee TEXT,
            cargo_name TEXT,
            mla TEXT,
            quantity NUMERIC(15,3),
            ddp DATE,
            dop DATE,
            eta TIMESTAMPTZ,
            ata TIMESTAMPTZ,
            lpc TIMESTAMPTZ,
            doc TIMESTAMPTZ,
            nor TIMESTAMPTZ,
            berth_name TEXT,
            vcn_id INTEGER,
            doc_status TEXT DEFAULT 'Pending',
            created_by TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')

    # jnpa05
    op.execute('ALTER TABLE vessel_agents ADD COLUMN IF NOT EXISTS agent_code VARCHAR(20)')
    op.execute('ALTER TABLE vessel_customers ADD COLUMN IF NOT EXISTS customer_code VARCHAR(20)')
    op.execute('''
        ALTER TABLE expected_vessels ADD COLUMN IF NOT EXISTS agents TEXT;
        ALTER TABLE expected_vessels ADD COLUMN IF NOT EXISTS tanks TEXT;
        ALTER TABLE expected_vessels ADD COLUMN IF NOT EXISTS consignees TEXT;
        ALTER TABLE expected_vessels DROP COLUMN IF EXISTS agent_tank_consignee;
    ''')
    op.execute('''
        CREATE TABLE IF NOT EXISTS tank_master (
            id SERIAL PRIMARY KEY,
            tank_code VARCHAR(20),
            tank_name TEXT,
            is_active BOOLEAN DEFAULT TRUE
        )
    ''')

    # jnpa06
    op.execute('ALTER TABLE vessel_agents ALTER COLUMN agent_code TYPE TEXT')
    op.execute('ALTER TABLE vessel_customers ALTER COLUMN customer_code TYPE TEXT')
    op.execute('ALTER TABLE tank_master ALTER COLUMN tank_code TYPE TEXT')

    # jnpa07
    op.execute('''
        ALTER TABLE expected_vessels ALTER COLUMN quantity TYPE TEXT USING quantity::TEXT
    ''')
