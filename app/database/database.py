# app/database.py
import sqlite3
import json
import os

# Database path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'payers.db')  # can be renamed, but kept for compatibility

# In‑memory cache for payers (used by eob_extraction.py / patterns.py)
_payers_cache = {}


# ----------------------------------------------------------------------
# Connection helper
# ----------------------------------------------------------------------

def get_connection():
    """Return a SQLite connection with row_factory set to dict‑like rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ----------------------------------------------------------------------
# Generic migration helper
# ----------------------------------------------------------------------

def migrate_table(table_name: str, create_sql: str, default_rows: list[dict] = None):
    """
    Create the table if it doesn't exist, and if the table is empty and default_rows
    is provided, insert those rows.

    - table_name: name of the table (used for existence check).
    - create_sql: SQL CREATE TABLE statement (must include the table name).
    - default_rows: list of dicts, where each dict maps column names to values.
                   For JSON columns, values will be automatically JSON‑serialized.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Create table if not exists
    cursor.execute(create_sql)

    # 2. Seed only if the table is empty and default_rows is provided
    if default_rows:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        if count == 0:
            # Prepare column names and placeholders
            if not default_rows:
                return
            columns = list(default_rows[0].keys())
            placeholders = ', '.join(['?'] * len(columns))
            column_names = ', '.join(columns)

            # Build insert statement
            insert_sql = f"INSERT INTO {table_name} ({column_names}) VALUES ({placeholders})"

            # Convert values: JSON‑serialize if necessary (e.g., aliases is a list)
            for row in default_rows:
                values = []
                for col in columns:
                    val = row[col]
                    # If the value is a list or dict, serialize to JSON
                    if isinstance(val, (list, dict)):
                        val = json.dumps(val)
                    values.append(val)
                cursor.execute(insert_sql, values)

            conn.commit()

    conn.close()


# ----------------------------------------------------------------------
# Table‑specific migration functions
# ----------------------------------------------------------------------

def migrate_payers():
    """Create the payers table and seed default payers if empty."""
    create_sql = '''
        CREATE TABLE IF NOT EXISTS payers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            aliases TEXT NOT NULL   -- JSON array
        )
    '''
    default_payers = [
        {"name": "Aetna", "aliases": ["aetna", "aetna better health", "aetna life insurance"]},
        {"name": "Anthem", "aliases": ["anthem", "anthem blue cross", "anthem bcbs"]},
        {"name": "Blue Cross Blue Shield", "aliases": ["blue cross blue shield", "bcbs", "blue cross", "blue shield"]},
        {"name": "Cigna", "aliases": ["cigna", "cigna healthcare"]},
        {"name": "Humana", "aliases": ["humana", "humana inc"]},
        {"name": "UnitedHealthcare", "aliases": ["unitedhealthcare", "united healthcare", "united health care", "uhc"]},
        {"name": "Medicare", "aliases": ["medicare", "cms medicare", "centers for medicare"]},
        {"name": "Medicaid", "aliases": ["medicaid"]},
        {"name": "Kaiser Permanente", "aliases": ["kaiser permanente", "kaiser"]},
        {"name": "Molina Healthcare", "aliases": ["molina healthcare", "molina"]},
        {"name": "Centene", "aliases": ["centene", "centene corporation"]},
        {"name": "WellCare", "aliases": ["wellcare"]},
        {"name": "Tricare", "aliases": ["tricare"]},
        {"name": "Oscar Health", "aliases": ["oscar health", "oscar"]},
        {"name": "Ambetter", "aliases": ["ambetter"]},
        {"name": "Health Net", "aliases": ["health net", "healthnet"]},
        {"name": "Oxford Health Plans", "aliases": ["oxford health plans", "oxford health"]},
        {"name": "GEHA", "aliases": ["geha"]},
        {"name": "MetLife", "aliases": ["metlife"]},
        {"name": "Guardian", "aliases": ["guardian life", "guardian"]},
    ]
    migrate_table("payers", create_sql, default_payers)


# ----------------------------------------------------------------------
# Master initialisation
# ----------------------------------------------------------------------

def init_db():
    """Run all migrations. Call this once when the application starts."""
    migrate_payers()
    # Future tables: add their migration functions here
    # migrate_cpt_codes()
    # migrate_other_table()


# ----------------------------------------------------------------------
# Payer cache (loaded after migrations)
# ----------------------------------------------------------------------

def _load_payers_cache():
    """Load all payers from the database into the in‑memory cache."""
    global _payers_cache
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name, aliases FROM payers')
    rows = cursor.fetchall()
    _payers_cache = {row['name']: json.loads(row['aliases']) for row in rows}
    conn.close()


def get_known_payers():
    """Return the cached dict of payer names → list of aliases."""
    return _payers_cache


# ----------------------------------------------------------------------
# Payer CRUD (cache‑aware)
# ----------------------------------------------------------------------

def get_all_payers():
    """Return a list of dicts: [{"name": ..., "aliases": [...]}, ...]."""
    return [{"name": name, "aliases": aliases} for name, aliases in _payers_cache.items()]


def save_payer(name, aliases):
    """Insert or replace a payer. Refreshes the cache."""
    conn = get_connection()
    cursor = conn.cursor()
    aliases_json = json.dumps(aliases)
    cursor.execute('INSERT OR REPLACE INTO payers (name, aliases) VALUES (?, ?)',
                   (name, aliases_json))
    conn.commit()
    conn.close()
    _load_payers_cache()


def delete_payer(name):
    """Delete a payer by name. Refreshes the cache."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM payers WHERE name = ?', (name,))
    conn.commit()
    conn.close()
    _load_payers_cache()


def replace_all_payers(payers_list):
    """
    Replace the entire payer table with a new list.
    payers_list: list of dicts with keys "name" and "aliases".
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM payers')
    for payer in payers_list:
        name = payer['name']
        aliases = payer.get('aliases', [])
        aliases_json = json.dumps(aliases)
        cursor.execute('INSERT INTO payers (name, aliases) VALUES (?, ?)',
                       (name, aliases_json))
    conn.commit()
    conn.close()
    _load_payers_cache()


# ----------------------------------------------------------------------
# Initialise everything when this module is imported
# ----------------------------------------------------------------------

init_db()
_load_payers_cache()