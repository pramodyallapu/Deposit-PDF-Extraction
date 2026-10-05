# app/database.py
import sqlite3
import json
import os
import re
import difflib

# Database path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'payers.db')  # can be renamed, but kept for compatibility

# In‑memory cache for payers and practices
_payers_cache = {}
_practices_cache = {}


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


def migrate_practices():
    """Create the practices table and seed default practices if empty."""
    create_sql = '''
        CREATE TABLE IF NOT EXISTS practices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            aliases TEXT NOT NULL   -- JSON array
        )
    '''
    default_practices = [
        {"name": "ABC Medical Group", "aliases": ["abc medical", "abc med group", "abc medical group"]},
        {"name": "City Hospital", "aliases": ["city hospital", "city medical center", "city hosp"]},
        {"name": "Family Practice Associates", "aliases": ["family practice", "family med associates", "fpa"]},
        {"name": "Memorial Healthcare", "aliases": ["memorial healthcare", "memorial health", "memorial"]},
        {"name": "St. Mary's Medical Center", "aliases": ["st marys medical", "st marys hospital", "st mary"]},
        {"name": "University Medical Group", "aliases": ["university medical", "university med group", "umg"]},
        {"name": "Children's Health Center", "aliases": ["childrens health", "childrens hospital", "childrens center"]},
    ]
    migrate_table("practices", create_sql, default_practices)


# ----------------------------------------------------------------------
# Master initialisation
# ----------------------------------------------------------------------

def init_db():
    """Run all migrations. Call this once when the application starts."""
    migrate_payers()
    migrate_practices()


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
# Practice cache
# ----------------------------------------------------------------------

def _load_practices_cache():
    """Load all practices from the database into the in‑memory cache."""
    global _practices_cache
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name, aliases FROM practices')
    rows = cursor.fetchall()
    _practices_cache = {row['name']: json.loads(row['aliases']) for row in rows}
    conn.close()


def get_known_practices():
    """Return the cached dict of practice names → list of aliases."""
    return _practices_cache


# ----------------------------------------------------------------------
# Payer CRUD (cache‑aware)
# ----------------------------------------------------------------------

def get_all_payers():
    """Return a list of dicts: [{"name": ..., "aliases": [...]}, ...]."""
    return [{"name": name, "aliases": aliases} for name, aliases in _payers_cache.items()]


def get_payer_by_name(name):
    """Get a specific payer by name."""
    aliases = _payers_cache.get(name)
    if aliases is not None:
        return {"name": name, "aliases": aliases}
    return None


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
    """Replace the entire payer table with a new list."""
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
# Practice CRUD (cache‑aware)
# ----------------------------------------------------------------------

def get_all_practices():
    """Return a list of dicts: [{"name": ..., "aliases": [...]}, ...]."""
    return [{"name": name, "aliases": aliases} for name, aliases in _practices_cache.items()]


def get_practice_by_name(name):
    """Get a specific practice by name."""
    aliases = _practices_cache.get(name)
    if aliases is not None:
        return {"name": name, "aliases": aliases}
    return None


def save_practice(name, aliases):
    """Insert or replace a practice. Refreshes the cache."""
    conn = get_connection()
    cursor = conn.cursor()
    aliases_json = json.dumps(aliases)
    cursor.execute('INSERT OR REPLACE INTO practices (name, aliases) VALUES (?, ?)',
                   (name, aliases_json))
    conn.commit()
    conn.close()
    _load_practices_cache()


def delete_practice(name):
    """Delete a practice by name. Refreshes the cache."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM practices WHERE name = ?', (name,))
    conn.commit()
    conn.close()
    _load_practices_cache()


def replace_all_practices(practices_list):
    """Replace the entire practices table with a new list."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM practices')
    for practice in practices_list:
        name = practice['name']
        aliases = practice.get('aliases', [])
        aliases_json = json.dumps(aliases)
        cursor.execute('INSERT INTO practices (name, aliases) VALUES (?, ?)',
                       (name, aliases_json))
    conn.commit()
    conn.close()
    _load_practices_cache()


# ----------------------------------------------------------------------
# Matching functions for extraction
# ----------------------------------------------------------------------

def match_known_payer(text: str, min_ratio: float = 0.8):
    """
    Match text against known payers from database.
    Returns: (is_match: bool, matched_name: str or None, confidence: float)
    """
    if not text or not text.strip():
        return False, None, 0.0
    
    text_norm = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    if not text_norm:
        return False, None, 0.0
    
    best_match = None
    best_score = 0.0
    
    for canonical, aliases in _payers_cache.items():
        all_names = [canonical] + aliases
        for alias in all_names:
            alias_norm = re.sub(r"[^a-z0-9 ]", "", alias.lower()).strip()
            if not alias_norm:
                continue
            
            # Exact match
            if alias_norm == text_norm:
                return True, canonical, 1.0
            
            # Substring match
            if alias_norm in text_norm or text_norm in alias_norm:
                ratio = min(len(alias_norm), len(text_norm)) / max(len(alias_norm), len(text_norm))
                score = 0.85 + (ratio * 0.15)
                if score > best_score:
                    best_score = score
                    best_match = canonical
            
            # Fuzzy match
            ratio = difflib.SequenceMatcher(None, alias_norm, text_norm).ratio()
            if ratio >= min_ratio and ratio > best_score:
                best_score = ratio
                best_match = canonical
    
    if best_match and best_score >= min_ratio:
        return True, best_match, best_score
    
    return False, None, 0.0


def match_known_practice(text: str, min_ratio: float = 0.8):
    """
    Match text against known practices from database.
    Returns: (is_match: bool, matched_name: str or None, confidence: float)
    """
    if not text or not text.strip():
        return False, None, 0.0
    
    text_norm = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    if not text_norm:
        return False, None, 0.0
    
    best_match = None
    best_score = 0.0
    
    for canonical, aliases in _practices_cache.items():
        all_names = [canonical] + aliases
        for alias in all_names:
            alias_norm = re.sub(r"[^a-z0-9 ]", "", alias.lower()).strip()
            if not alias_norm:
                continue
            
            # Exact match
            if alias_norm == text_norm:
                return True, canonical, 1.0
            
            # Substring match
            if alias_norm in text_norm or text_norm in alias_norm:
                ratio = min(len(alias_norm), len(text_norm)) / max(len(alias_norm), len(text_norm))
                score = 0.85 + (ratio * 0.15)
                if score > best_score:
                    best_score = score
                    best_match = canonical
            
            # Fuzzy match
            ratio = difflib.SequenceMatcher(None, alias_norm, text_norm).ratio()
            if ratio >= min_ratio and ratio > best_score:
                best_score = ratio
                best_match = canonical
    
    if best_match and best_score >= min_ratio:
        return True, best_match, best_score
    
    return False, None, 0.0


# ----------------------------------------------------------------------
# Bulk import functions
# ----------------------------------------------------------------------

def bulk_import_payers(payers_list):
    """
    Bulk import payers from a list.
    
    Args:
        payers_list: List of dicts with 'name' and 'aliases' keys
    
    Returns:
        Dict with 'created', 'updated', and 'errors' counts
    """
    result = {"created": 0, "updated": 0, "errors": []}
    
    for payer in payers_list:
        name = payer.get("name", "").strip()
        aliases = payer.get("aliases", [])
        
        if not name:
            result["errors"].append({"error": "Name is required", "data": payer})
            continue
        
        try:
            existing = _payers_cache.get(name)
            if existing is not None:
                save_payer(name, aliases)
                result["updated"] += 1
            else:
                save_payer(name, aliases)
                result["created"] += 1
        except Exception as e:
            result["errors"].append({"error": str(e), "data": payer})
    
    return result


def bulk_import_practices(practices_list):
    """
    Bulk import practices from a list.
    
    Args:
        practices_list: List of dicts with 'name' and 'aliases' keys
    
    Returns:
        Dict with 'created', 'updated', and 'errors' counts
    """
    result = {"created": 0, "updated": 0, "errors": []}
    
    for practice in practices_list:
        name = practice.get("name", "").strip()
        aliases = practice.get("aliases", [])
        
        if not name:
            result["errors"].append({"error": "Name is required", "data": practice})
            continue
        
        try:
            existing = _practices_cache.get(name)
            if existing is not None:
                save_practice(name, aliases)
                result["updated"] += 1
            else:
                save_practice(name, aliases)
                result["created"] += 1
        except Exception as e:
            result["errors"].append({"error": str(e), "data": practice})
    
    return result


# ----------------------------------------------------------------------
# Initialise everything when this module is imported
# ----------------------------------------------------------------------

# Initialize database tables
init_db()

# Load caches
_load_payers_cache()
_load_practices_cache()

# Print status
print(f"✅ Database initialized at: {DB_PATH}")
print(f"   - {len(_payers_cache)} payers loaded")
print(f"   - {len(_practices_cache)} practices loaded")