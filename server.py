"""
QuizMaster — Platform Pengerjaan Soal
Backend server with SQLite database and PDF upload support.
"""

import os
import re
import json
import sqlite3
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory

import pdfplumber

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'quizmaster.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')

# Ensure uploads directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════

def get_db():
    """Get a database connection with row_factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize the database schema."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS quiz_sets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            filename    TEXT,
            total_questions INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS questions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_set_id     INTEGER NOT NULL,
            number          INTEGER NOT NULL,
            question_text   TEXT NOT NULL,
            correct_answer  TEXT NOT NULL,
            explanation     TEXT,
            FOREIGN KEY (quiz_set_id) REFERENCES quiz_sets(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS choices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            label       TEXT NOT NULL,
            choice_text TEXT NOT NULL,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def import_pdf_to_db(filepath, display_name=None):
    """Parse a PDF file and store all questions into the database."""
    questions = parse_pdf_questions(filepath)
    if not questions:
        return None, "Tidak ada soal ditemukan dalam PDF"

    filename = os.path.basename(filepath)
    if not display_name:
        display_name = os.path.splitext(filename)[0]

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO quiz_sets (name, filename, total_questions) VALUES (?, ?, ?)",
            (display_name, filename, len(questions))
        )
        quiz_set_id = cursor.lastrowid

        for q in questions:
            q_cursor = conn.execute(
                "INSERT INTO questions (quiz_set_id, number, question_text, correct_answer, explanation) VALUES (?, ?, ?, ?, ?)",
                (quiz_set_id, q['number'], q['question'], q['correct_answer'], q['explanation'])
            )
            question_id = q_cursor.lastrowid

            for c in q['choices']:
                conn.execute(
                    "INSERT INTO choices (question_id, label, choice_text) VALUES (?, ?, ?)",
                    (question_id, c['label'], c['text'])
                )

        conn.commit()
        return quiz_set_id, None
    except Exception as e:
        conn.rollback()
        return None, str(e)
    finally:
        conn.close()


def get_quiz_sets():
    """Get all quiz sets from the database."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, filename, total_questions, created_at FROM quiz_sets ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_quiz_questions(quiz_set_id):
    """Get all questions for a quiz set, with choices."""
    conn = get_db()

    quiz = conn.execute(
        "SELECT id, name, total_questions FROM quiz_sets WHERE id = ?",
        (quiz_set_id,)
    ).fetchone()

    if not quiz:
        conn.close()
        return None

    questions_rows = conn.execute(
        "SELECT id, number, question_text, correct_answer, explanation FROM questions WHERE quiz_set_id = ? ORDER BY number",
        (quiz_set_id,)
    ).fetchall()

    questions = []
    for qr in questions_rows:
        choices_rows = conn.execute(
            "SELECT label, choice_text FROM choices WHERE question_id = ? ORDER BY label",
            (qr['id'],)
        ).fetchall()

        questions.append({
            'number': qr['number'],
            'question': qr['question_text'],
            'correct_answer': qr['correct_answer'],
            'explanation': qr['explanation'],
            'choices': [{'label': c['label'], 'text': c['choice_text']} for c in choices_rows],
        })

    conn.close()

    return {
        'id': quiz['id'],
        'display_name': quiz['name'],
        'total_questions': quiz['total_questions'],
        'questions': questions,
    }


def delete_quiz_set(quiz_set_id):
    """Delete a quiz set and all its questions/choices (cascading)."""
    conn = get_db()
    conn.execute("DELETE FROM quiz_sets WHERE id = ?", (quiz_set_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════
#  PDF PARSING (kept from original)
# ═══════════════════════════════════════════════════════════════════

def parse_pdf_questions(filepath):
    """Parse a PDF file containing questions in a 4-column table format."""
    pdf = pdfplumber.open(filepath)

    all_rows = []
    for page in pdf.pages:
        tables = page.extract_tables()
        if tables:
            for table in tables:
                for row in table:
                    all_rows.append(row)
    pdf.close()

    # Merge continuation rows
    merged_questions = []
    current_question = None

    for row in all_rows:
        if row[0] and row[0].strip() == 'No':
            continue

        if row[0] and row[0].strip().isdigit():
            if current_question:
                merged_questions.append(current_question)
            current_question = {
                'no': int(row[0].strip()),
                'soal': (row[1] or '').strip(),
                'pilihan_raw': (row[2] or '').strip(),
                'penjelasan_raw': (row[3] or '').strip(),
            }
        else:
            if current_question:
                if row[1]:
                    current_question['soal'] += ' ' + row[1].strip()
                if row[2]:
                    current_question['pilihan_raw'] += '\n' + row[2].strip()
                if row[3]:
                    current_question['penjelasan_raw'] += ' ' + row[3].strip()

    if current_question:
        merged_questions.append(current_question)

    questions = []
    for q in merged_questions:
        choices = parse_choices(q['pilihan_raw'])
        correct_answer, explanation = parse_explanation(q['penjelasan_raw'])
        questions.append({
            'number': q['no'],
            'question': clean_text(q['soal']),
            'choices': choices,
            'correct_answer': correct_answer,
            'explanation': clean_text(explanation),
        })

    return questions


def parse_choices(raw_text):
    """Parse raw choice text into a list of {label, text} objects."""
    pattern = r'([A-E])\.\s*'
    parts = re.split(pattern, raw_text)
    choices = []
    i = 1
    while i < len(parts) - 1:
        label = parts[i].strip()
        text = re.sub(r'\s*\n\s*', ' ', parts[i + 1].strip()).strip()
        choices.append({'label': label, 'text': text})
        i += 2
    return choices


def parse_explanation(raw_text):
    """Extract correct answer letter and explanation text."""
    raw_text = raw_text.strip()
    match = re.match(r'^([A-E])\.\s*', raw_text)
    if match:
        return match.group(1), re.sub(r'\s*\n\s*', ' ', raw_text[match.end():]).strip()
    match = re.match(r'^([A-E])\s', raw_text)
    if match:
        return match.group(1), re.sub(r'\s*\n\s*', ' ', raw_text[match.end():]).strip()
    return 'A', raw_text


def clean_text(text):
    """Normalize whitespace and newlines."""
    if not text:
        return ''
    text = re.sub(r'\s*\n\s*', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════
#  AUTO-IMPORT: Import existing PDFs on first run
# ═══════════════════════════════════════════════════════════════════

def auto_import_pdfs():
    """On first run, import any PDF files in the base directory into the database."""
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) as cnt FROM quiz_sets").fetchone()['cnt']
    conn.close()

    if existing > 0:
        return  # Database already has data, skip auto-import

    import glob
    pdf_files = glob.glob(os.path.join(BASE_DIR, '*.pdf'))
    for pdf_path in sorted(pdf_files):
        name = os.path.splitext(os.path.basename(pdf_path))[0]
        quiz_id, error = import_pdf_to_db(pdf_path, name)
        if quiz_id:
            print(f"  [OK] Imported: {name} (ID: {quiz_id})")
        else:
            print(f"  [!] Failed to import {name}: {error}")


# ═══════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Serve the main frontend page."""
    return render_template('index.html')


@app.route('/api/quizzes')
def list_quizzes():
    """List all available quiz sets."""
    quizzes = get_quiz_sets()
    return jsonify({'quizzes': quizzes})


@app.route('/api/quizzes/<int:quiz_id>')
def get_quiz(quiz_id):
    """Get all questions for a specific quiz set."""
    data = get_quiz_questions(quiz_id)
    if not data:
        return jsonify({'error': 'Quiz tidak ditemukan'}), 404
    return jsonify(data)


@app.route('/api/upload', methods=['POST'])
def upload_pdf():
    """Upload a PDF file and import questions into the database."""
    if 'pdf' not in request.files:
        return jsonify({'error': 'Tidak ada file yang dikirim'}), 400

    file = request.files['pdf']
    if file.filename == '':
        return jsonify({'error': 'Nama file kosong'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'File harus berformat PDF'}), 400

    # Custom name from form, or use filename
    custom_name = request.form.get('name', '').strip()
    display_name = custom_name or os.path.splitext(file.filename)[0]

    # Save uploaded file
    safe_filename = re.sub(r'[^\w\s\-.]', '', file.filename).strip()
    if not safe_filename:
        safe_filename = f"upload_{int(time.time())}.pdf"
    filepath = os.path.join(UPLOAD_DIR, safe_filename)
    file.save(filepath)

    # Parse and import into database
    try:
        quiz_id, error = import_pdf_to_db(filepath, display_name)
        if error:
            os.remove(filepath)
            return jsonify({'error': f'Gagal parsing PDF: {error}'}), 400

        return jsonify({
            'success': True,
            'quiz_id': quiz_id,
            'name': display_name,
            'message': f'Berhasil mengimpor soal!',
        })
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': f'Terjadi kesalahan: {str(e)}'}), 500


@app.route('/api/quizzes/<int:quiz_id>', methods=['DELETE'])
def delete_quiz(quiz_id):
    """Delete a quiz set."""
    delete_quiz_set(quiz_id)
    return jsonify({'success': True, 'message': 'Quiz berhasil dihapus'})


@app.route('/static/<path:path>')
def send_static(path):
    """Serve static files."""
    return send_from_directory('static', path)


# ═══════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════

# Initialize database and auto-import on startup
init_db()
auto_import_pdfs()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'

    print("=" * 60)
    print("  QuizMaster - Platform Pengerjaan Soal")
    print(f"  http://localhost:{port}")
    print("=" * 60)

    quizzes = get_quiz_sets()
    print(f"\n  Quiz tersedia: {len(quizzes)} set soal")
    for q in quizzes:
        print(f"    - {q['name']} ({q['total_questions']} soal)")

    print("=" * 60)
    app.run(debug=debug, host='0.0.0.0', port=port)
