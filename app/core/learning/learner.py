"""
Complete self-learning engine - learns from EVERY user correction.
No migrations needed - tables auto-create.
"""
import re
import json
import sqlite3
import hashlib  # <-- ADD THIS IMPORT
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(BASE_DIR, 'payers.db')


@dataclass
class LearnedPattern:
    """A pattern learned from user corrections."""
    pattern_hash: str
    field_name: str
    pattern_type: str  # 'label_based', 'free_text', 'positional'
    label_text: Optional[str] = None
    value_pattern: Optional[str] = None
    position_info: Optional[Dict] = None
    confidence: float = 0.5
    frequency: int = 1
    examples: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class LearningEngine:
    """
    Self-learning extraction engine.
    
    KEY PRINCIPLE: Learn from EVERY user correction, regardless of confidence.
    """
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_tables()
        self._cache = None
        
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_tables(self):
        """Create tables if they don't exist (no migrations)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 1. Corrections table - stores EVERY user correction
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS extraction_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_hash TEXT NOT NULL,
                field_name TEXT NOT NULL,
                extracted_value TEXT,
                corrected_value TEXT NOT NULL,
                context_before TEXT,
                context_after TEXT,
                label_nearby TEXT,
                position_info TEXT,
                doc_metadata TEXT,
                extraction_confidence REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL,
                UNIQUE(document_hash, field_name)
            )
        ''')
        
        # 2. Learned patterns table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT UNIQUE NOT NULL,
                field_name TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                label_text TEXT,
                value_pattern TEXT,
                position_info TEXT,
                confidence REAL DEFAULT 0.5,
                frequency INTEGER DEFAULT 1,
                examples TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        # 3. Pattern performance tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pattern_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT NOT NULL,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                last_used TEXT,
                FOREIGN KEY (pattern_hash) REFERENCES learned_patterns(pattern_hash)
            )
        ''')
        
        # 4. Free-text patterns (for insurance/practice names)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS free_text_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                field_name TEXT NOT NULL,
                pattern_hash TEXT UNIQUE NOT NULL,
                position_score REAL DEFAULT 0.0,
                keyword_score REAL DEFAULT 0.0,
                format_score REAL DEFAULT 0.0,
                confidence REAL DEFAULT 0.5,
                frequency INTEGER DEFAULT 1,
                examples TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        conn.commit()
        conn.close()
    
    # ============================================================
    # CORE: LEARN FROM EVERY CORRECTION
    # ============================================================
    
    def learn_from_correction(self, document_text: str, field_name: str,
                              extracted_value: str, corrected_value: str,
                              extraction_confidence: float = 0.0,
                              doc_metadata: Optional[Dict] = None) -> Dict:
        """
        Learn from a user correction - ALWAYS called when user corrects ANY field.
        
        Returns:
            {
                'pattern_learned': bool,
                'pattern_hash': str or None,
                'correction_stored': bool
            }
        """
        # Skip if no correction (same value)
        if extracted_value and extracted_value == corrected_value:
            return {'pattern_learned': False, 'correction_stored': False, 'reason': 'no_change'}
        
        # Find context around the corrected value
        context = self._find_context(document_text, corrected_value)
        
        # Find label near the value
        label = self._find_label_nearby(context, field_name)
        
        # Calculate position info
        position_info = self._calculate_position(context, corrected_value)
        
        # Create document hash
        doc_hash = hashlib.md5(document_text[:10000].encode()).hexdigest()[:16]
        if doc_metadata:
            doc_hash = hashlib.md5(
                f"{doc_hash}:{doc_metadata.get('filename', '')}".encode()
            ).hexdigest()[:16]
        
        # STORE CORRECTION (always)
        self._store_correction(
            doc_hash, field_name, extracted_value, corrected_value,
            context, label, position_info, extraction_confidence, doc_metadata
        )
        
        # LEARN PATTERN (always try)
        pattern_learned = False
        pattern_hash = None
        
        # Try to learn a label-based pattern
        if label and len(label) < 50:
            pattern = self._learn_label_pattern(
                field_name, corrected_value, label, position_info
            )
            if pattern:
                pattern_learned = True
                pattern_hash = pattern.pattern_hash
        
        # If no label found, try positional pattern
        if not pattern_learned and position_info:
            pattern = self._learn_positional_pattern(
                field_name, corrected_value, position_info, context
            )
            if pattern:
                pattern_learned = True
                pattern_hash = pattern.pattern_hash
        
        # For insurance/practice names, also learn free-text pattern
        if field_name in ['insurance_name', 'practice_name'] and not pattern_learned:
            self._learn_free_text_pattern(field_name, corrected_value, document_text)
            pattern_learned = True
        
        return {
            'pattern_learned': pattern_learned,
            'pattern_hash': pattern_hash,
            'correction_stored': True
        }
    
    def _find_context(self, text: str, value: str, window: int = 3) -> Dict[str, Any]:
        """Find context around a value in the document."""
        lines = text.split('\n')
        value_clean = re.sub(r'[$,]', '', value).strip()
        
        for i, line in enumerate(lines):
            line_clean = re.sub(r'[$,]', '', line).strip()
            if value_clean in line_clean or value in line:
                start = max(0, i - window)
                end = min(len(lines), i + window + 1)
                pos = line.find(value) if value in line else line.find(value_clean)
                
                return {
                    "before": '\n'.join(lines[start:i]) if i > start else "",
                    "line": line,
                    "after": '\n'.join(lines[i+1:end]) if i+1 < end else "",
                    "line_number": i,
                    "position_in_line": pos if pos >= 0 else 0,
                    "line_text": line,
                    "full_context": '\n'.join(lines[start:end])
                }
        return {}
    
    def _find_label_nearby(self, context: Dict, field_name: str) -> Optional[str]:
        """Find a label near the value using aliases from patterns.py."""
        try:
            from ..patterns import FIELD_ALIASES
        except ImportError:
            FIELD_ALIASES = {
                "check_number": ["check", "check no", "check #", "draft", "eft", "trace", "reference"],
                "check_date": ["date", "check date", "payment date", "draft date", "issued"],
                "check_amount": ["amount", "total", "payment", "paid", "draft amount", "net"],
                "insurance_name": ["payer", "insurance", "carrier", "plan"],
                "practice_name": ["provider", "practice", "pay to", "billing", "rendering"]
            }
        
        line = context.get("line_text", "")
        before = context.get("before", "")
        combined = f"{before} {line}"
        
        aliases = FIELD_ALIASES.get(field_name, [])
        
        alias_list = []
        for item in aliases:
            if isinstance(item, tuple):
                alias_list.append(item[0].lower())
            else:
                alias_list.append(str(item).lower())
        
        best_match = None
        best_length = 0
        
        for alias in alias_list:
            if alias in combined.lower():
                idx = combined.lower().find(alias)
                length = len(alias)
                if length > best_length:
                    best_length = length
                    best_match = {
                        'text': combined[idx:idx+length+20].strip(),
                        'alias': alias,
                        'position': idx
                    }
        
        if best_match:
            idx = best_match['position']
            start = max(0, idx - 5)
            end = min(len(combined), idx + len(best_match['alias']) + 25)
            label_text = combined[start:end].strip()
            label_text = re.sub(r'\s+', ' ', label_text)
            if len(label_text) > 3 and len(label_text) < 100:
                return label_text
        
        return None
    
    def _calculate_position(self, context: Dict, value: str) -> Dict[str, Any]:
        """Calculate position info for a value."""
        line_text = context.get("line_text", "")
        pos = context.get("position_in_line", 0)
        
        if not line_text:
            return {"position": "unknown"}
        
        if pos < len(line_text) * 0.3:
            position = "start"
        elif pos > len(line_text) * 0.7:
            position = "end"
        else:
            position = "middle"
        
        delimiter = None
        for d in [':', '=', '-', '—', ';']:
            if d in line_text and line_text.find(d) < pos:
                delimiter = d
                break
        
        return {
            "position": position,
            "delimiter": delimiter,
            "line_position": pos,
            "line_length": len(line_text)
        }
    
    def _store_correction(self, doc_hash: str, field_name: str,
                         extracted_value: str, corrected_value: str,
                         context: Dict, label: Optional[str],
                         position_info: Dict, confidence: float,
                         doc_metadata: Optional[Dict]):
        """Store correction in database."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO extraction_corrections 
            (document_hash, field_name, extracted_value, corrected_value,
             context_before, context_after, label_nearby, position_info,
             doc_metadata, extraction_confidence, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            doc_hash,
            field_name,
            extracted_value or "",
            corrected_value,
            context.get("before", ""),
            context.get("after", ""),
            label or "",
            json.dumps(position_info),
            json.dumps(doc_metadata or {}),
            confidence,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
    
    # ============================================================
    # LEARN LABEL-BASED PATTERNS
    # ============================================================
    
    def _learn_label_pattern(self, field_name: str, corrected_value: str,
                             label: str, position_info: Dict) -> Optional[LearnedPattern]:
        """Learn a label-based pattern (e.g., 'Total: $123.45')."""
        label_clean = re.sub(r'\s+', ' ', label).strip()
        label_clean = re.sub(r'[:\s]+$', '', label_clean)
        
        if len(label_clean) < 3:
            return None
        
        value_pattern = self._generate_value_pattern(corrected_value)
        
        pattern_hash = hashlib.md5(
            f"{field_name}:{label_clean}:{value_pattern}".encode()
        ).hexdigest()[:12]
        
        pattern = LearnedPattern(
            pattern_hash=pattern_hash,
            field_name=field_name,
            pattern_type="label_based",
            label_text=label_clean,
            value_pattern=value_pattern,
            position_info=position_info,
            confidence=0.5,
            frequency=1,
            examples=[corrected_value],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        self._save_pattern(pattern)
        return pattern
    
    def _generate_value_pattern(self, value: str) -> str:
        """Generate a regex pattern from a value."""
        value = value.strip()
        
        if re.match(r'^[\$]?\s*[\d,]+\.\d{2}$', value):
            return r'[\$]?\s*[\d,]+\.\d{2}'
        
        if re.match(r'^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$', value):
            return r'\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}'
        
        if re.match(r'^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$', value, re.IGNORECASE):
            return r'[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}'
        
        if re.match(r'^[A-Za-z0-9]{4,20}$', value):
            return r'[A-Za-z0-9]{4,20}'
        
        if re.match(r'^\d{5}$', value):
            return r'\d{5}'
        
        if re.match(r'^[A-Z][A-Za-z ,.&\-]{3,50}(?:LLC|PLLC|PC|PA|Inc|Corp)?$', value):
            return r'[A-Z][A-Za-z ,.&\-]{3,50}(?:\s+(?:LLC|PLLC|PC|PA|Inc|Corp))?'
        
        return re.escape(value)
    
    def _save_pattern(self, pattern: LearnedPattern, regex: Optional[str] = None):
        """Save a learned pattern to database."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id, frequency, confidence FROM learned_patterns WHERE pattern_hash = ?',
            (pattern.pattern_hash,)
        )
        existing = cursor.fetchone()
        
        examples_json = json.dumps(pattern.examples[:5])
        
        if existing:
            new_conf = min(0.95, existing['confidence'] + 0.05)
            cursor.execute('''
                UPDATE learned_patterns 
                SET frequency = frequency + 1,
                    confidence = ?,
                    examples = ?,
                    updated_at = ?
                WHERE pattern_hash = ?
            ''', (new_conf, examples_json, datetime.now().isoformat(), pattern.pattern_hash))
        else:
            cursor.execute('''
                INSERT INTO learned_patterns 
                (pattern_hash, field_name, pattern_type, label_text, value_pattern,
                 position_info, confidence, frequency, examples, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pattern.pattern_hash,
                pattern.field_name,
                pattern.pattern_type,
                pattern.label_text,
                pattern.value_pattern,
                json.dumps(pattern.position_info) if pattern.position_info else None,
                pattern.confidence,
                pattern.frequency,
                examples_json,
                pattern.created_at,
                pattern.updated_at
            ))
        
        conn.commit()
        conn.close()
        self._cache = None
    
    # ============================================================
    # LEARN POSITIONAL PATTERNS
    # ============================================================
    
    def _learn_positional_pattern(self, field_name: str, corrected_value: str,
                                  position_info: Dict, context: Dict) -> Optional[LearnedPattern]:
        """Learn a positional pattern."""
        if not position_info or position_info.get('position') == 'unknown':
            return None
        
        value_pattern = self._generate_value_pattern(corrected_value)
        
        pattern_hash = hashlib.md5(
            f"{field_name}:positional:{position_info.get('position')}:{value_pattern}".encode()
        ).hexdigest()[:12]
        
        pattern = LearnedPattern(
            pattern_hash=pattern_hash,
            field_name=field_name,
            pattern_type="positional",
            label_text=None,
            value_pattern=value_pattern,
            position_info=position_info,
            confidence=0.4,
            frequency=1,
            examples=[corrected_value],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        self._save_pattern(pattern)
        return pattern
    
    # ============================================================
    # LEARN FREE-TEXT PATTERNS
    # ============================================================
    
    def _learn_free_text_pattern(self, field_name: str, value: str, document_text: str):
        """Learn a free-text pattern for insurance/practice names."""
        position = document_text.find(value)
        if position == -1:
            return
        
        line_number = document_text[:position].count('\n')
        total_lines = document_text.count('\n')
        
        position_score = 1.0 - (line_number / max(total_lines, 1))
        
        keywords = self._get_keywords(field_name)
        keyword_score = sum(1 for kw in keywords if kw.lower() in value.lower()) / max(len(keywords), 1)
        format_score = self._calculate_format_score(value)
        
        pattern_hash = hashlib.md5(
            f"{field_name}:{position_score}:{keyword_score}:{format_score}".encode()
        ).hexdigest()[:12]
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, confidence, frequency FROM free_text_patterns 
            WHERE pattern_hash = ?
        ''', (pattern_hash,))
        existing = cursor.fetchone()
        
        examples_json = json.dumps([value])
        
        if existing:
            new_conf = min(0.95, existing['confidence'] + 0.05)
            cursor.execute('''
                UPDATE free_text_patterns 
                SET confidence = ?,
                    frequency = frequency + 1,
                    updated_at = ?
                WHERE pattern_hash = ?
            ''', (new_conf, datetime.now().isoformat(), pattern_hash))
        else:
            cursor.execute('''
                INSERT INTO free_text_patterns 
                (field_name, pattern_hash, position_score, keyword_score, format_score,
                 confidence, frequency, examples, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                field_name,
                pattern_hash,
                position_score,
                keyword_score,
                format_score,
                0.5,
                1,
                examples_json,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
        
        conn.commit()
        conn.close()
    
    def _get_keywords(self, field_name: str) -> List[str]:
        keywords = {
            'insurance_name': ['insurance', 'healthcare', 'health', 'medical', 'care', 
                              'plan', 'blue', 'cross', 'united', 'aetna', 'cigna', 
                              'humana', 'medicare', 'medicaid', 'kaiser', 'benefits'],
            'practice_name': ['practice', 'clinic', 'medical', 'group', 'associates',
                             'hospital', 'center', 'care', 'health', 'llc', 'pc', 
                             'pa', 'pllc', 'md', 'do', 'physicians']
        }
        return keywords.get(field_name, [])
    
    def _calculate_format_score(self, text: str) -> float:
        score = 0.0
        if text and text[0].isupper():
            score += 0.3
        if text.isupper():
            score += 0.2
        if len(text.split()) >= 2:
            score += 0.3
        if re.search(r'(LLC|PLLC|PC|PA|Inc|Corp|Associates|Group|Clinic|Hospital)', text, re.I):
            score += 0.2
        return min(1.0, score)
    
    # ============================================================
    # GET PATTERNS FOR EXTRACTION (Fallback)
    # ============================================================
    
    def get_patterns_for_extraction(self, field_name: Optional[str] = None,
                                    min_confidence: float = 0.3) -> List[Dict]:
        """Get learned patterns formatted for extraction."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        query = '''
            SELECT * FROM learned_patterns 
            WHERE is_active = 1 AND confidence >= ?
        '''
        params = [min_confidence]
        
        if field_name:
            query += ' AND field_name = ?'
            params.append(field_name)
        
        query += ' ORDER BY confidence DESC, frequency DESC LIMIT 30'
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        patterns = []
        for row in rows:
            pattern = {
                'pattern_hash': row['pattern_hash'],
                'field_name': row['field_name'],
                'pattern_type': row['pattern_type'],
                'confidence': row['confidence'],
                'frequency': row['frequency'],
                'value_pattern': row['value_pattern']
            }
            
            if row['pattern_type'] == 'label_based' and row['label_text'] and row['value_pattern']:
                label = re.escape(row['label_text'])
                value = row['value_pattern']
                pattern['regex'] = f"{label}\\s*[::=]?\\s*({value})"
                pattern['label_text'] = row['label_text']
            
            elif row['pattern_type'] == 'positional' and row['position_info']:
                pattern['position_info'] = json.loads(row['position_info'])
            
            patterns.append(pattern)
        
        return patterns
    
    def get_free_text_candidates(self, field_name: str, lines: List[str],
                                 min_confidence: float = 0.3) -> List[Dict]:
        """Get free-text extraction candidates."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM free_text_patterns 
            WHERE field_name = ? AND is_active = 1 AND confidence >= ?
            ORDER BY confidence DESC, frequency DESC
        ''', (field_name, min_confidence))
        
        patterns = cursor.fetchall()
        conn.close()
        
        if not patterns:
            return []
        
        candidates = []
        
        for pattern in patterns:
            for i, line in enumerate(lines):
                if len(line.strip()) < 10:
                    continue
                
                position_score = 1.0 - (i / max(len(lines), 1))
                if position_score < 0.2:
                    continue
                
                if not line[0].isupper():
                    continue
                
                keywords = self._get_keywords(field_name)
                keyword_match = any(kw in line.lower() for kw in keywords)
                
                if not keyword_match:
                    continue
                
                score = (
                    pattern['position_score'] * 0.3 +
                    pattern['keyword_score'] * 0.3 +
                    pattern['format_score'] * 0.2 +
                    pattern['confidence'] * 0.2
                )
                
                if score > min_confidence:
                    candidates.append({
                        'text': line.strip(),
                        'score': min(1.0, score),
                        'pattern_hash': pattern['pattern_hash'],
                        'source': 'learned_free_text'
                    })
        
        return candidates
    
    # ============================================================
    # STATISTICS
    # ============================================================
    
    def get_stats(self) -> Dict:
        """Get learning statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM extraction_corrections')
        corrections = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM learned_patterns WHERE is_active = 1')
        patterns = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT field_name, COUNT(*) as count 
            FROM learned_patterns WHERE is_active = 1 GROUP BY field_name
        ''')
        by_field = {row['field_name']: row['count'] for row in cursor.fetchall()}
        
        cursor.execute('SELECT AVG(confidence) FROM learned_patterns WHERE is_active = 1')
        avg_conf = cursor.fetchone()[0] or 0.0
        
        cursor.execute('SELECT COUNT(*) FROM free_text_patterns WHERE is_active = 1')
        free_text = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_corrections': corrections,
            'total_patterns': patterns,
            'patterns_by_field': by_field,
            'average_confidence': round(avg_conf, 3),
            'free_text_patterns': free_text
        }
    
    def get_all_patterns(self, limit: int = 50) -> List[Dict]:
        """Get all learned patterns for admin."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM learned_patterns WHERE is_active = 1 
            ORDER BY confidence DESC, frequency DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_corrections(self, limit: int = 100) -> List[Dict]:
        """Get recent corrections."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM extraction_corrections 
            ORDER BY id DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]


# ============================================================
# GLOBAL INSTANCE
# ============================================================

_learning_engine = None


def get_learning_engine() -> LearningEngine:
    """Get or create the global learning engine."""
    global _learning_engine
    if _learning_engine is None:
        _learning_engine = LearningEngine()
    return _learning_engine