"""
Platform Pengerjaan Soal - Backend Server
Parses PDF files containing questions in table format and serves them via API.
"""

import os
import re
import json
import glob
from flask import Flask, render_template, jsonify, send_from_directory

import pdfplumber

app = Flask(__name__)

# Directory where PDFs are stored (same directory as this script)
PDF_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_pdf_questions(filepath):
    """
    Parse a PDF file containing questions in a 4-column table format:
    No | Soal | Pilihan Ganda | Penjelasan
    
    Handles continuation rows (questions spanning multiple pages) by merging them.
    """
    pdf = pdfplumber.open(filepath)
    
    all_rows = []
    for page in pdf.pages:
        tables = page.extract_tables()
        if tables:
            for table in tables:
                for row in table:
                    all_rows.append(row)
    
    pdf.close()
    
    # Merge rows: questions may span multiple pages (continuation rows have no number)
    merged_questions = []
    current_question = None
    
    for row in all_rows:
        # Skip header rows
        if row[0] and row[0].strip() == 'No':
            continue
        
        if row[0] and row[0].strip().isdigit():
            # New question
            if current_question:
                merged_questions.append(current_question)
            current_question = {
                'no': int(row[0].strip()),
                'soal': (row[1] or '').strip(),
                'pilihan_raw': (row[2] or '').strip(),
                'penjelasan_raw': (row[3] or '').strip(),
            }
        else:
            # Continuation row - merge with current question
            if current_question:
                if row[1]:
                    current_question['soal'] += ' ' + row[1].strip()
                if row[2]:
                    current_question['pilihan_raw'] += '\n' + row[2].strip()
                if row[3]:
                    current_question['penjelasan_raw'] += ' ' + row[3].strip()
    
    # Don't forget the last question
    if current_question:
        merged_questions.append(current_question)
    
    # Process each question: parse choices and extract correct answer
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
    """
    Parse raw choice text into a list of {label, text} objects.
    Handles formats like:
      A. Some text
      B. Some text
      C. Some text
      D. Some text
    """
    # Split by choice labels (A., B., C., D., E.)
    pattern = r'([A-E])\.\s*'
    parts = re.split(pattern, raw_text)
    
    choices = []
    i = 1  # Skip first empty part
    while i < len(parts) - 1:
        label = parts[i].strip()
        text = parts[i + 1].strip()
        # Clean up newlines within choice text
        text = re.sub(r'\s*\n\s*', ' ', text).strip()
        choices.append({
            'label': label,
            'text': text,
        })
        i += 2
    
    return choices


def parse_explanation(raw_text):
    """
    Parse explanation text. The correct answer letter is at the beginning.
    Format: "B. Explanation text here..."
    Returns: (correct_answer_letter, explanation_text)
    """
    raw_text = raw_text.strip()
    
    # Match pattern like "A." or "B." at the start
    match = re.match(r'^([A-E])\.\s*', raw_text)
    if match:
        correct_answer = match.group(1)
        explanation = raw_text[match.end():].strip()
        # Clean up newlines 
        explanation = re.sub(r'\s*\n\s*', ' ', explanation).strip()
        return correct_answer, explanation
    
    # Fallback: try to find the answer letter in starting text
    match = re.match(r'^([A-E])\s', raw_text)
    if match:
        correct_answer = match.group(1)
        explanation = raw_text[match.end():].strip()
        explanation = re.sub(r'\s*\n\s*', ' ', explanation).strip()
        return correct_answer, explanation
    
    return 'A', raw_text  # Default fallback


def clean_text(text):
    """Clean up text by normalizing whitespace and newlines."""
    if not text:
        return ''
    # Replace newlines followed by spaces with a single space
    text = re.sub(r'\s*\n\s*', ' ', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def get_available_pdfs():
    """Get list of all PDF files in the directory."""
    pdf_files = glob.glob(os.path.join(PDF_DIR, '*.pdf'))
    return [
        {
            'filename': os.path.basename(f),
            'display_name': os.path.splitext(os.path.basename(f))[0],
            'size_kb': round(os.path.getsize(f) / 1024, 1),
        }
        for f in sorted(pdf_files)
    ]


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Serve the main frontend page."""
    return render_template('index.html')


@app.route('/api/pdfs')
def list_pdfs():
    """List all available PDF files."""
    pdfs = get_available_pdfs()
    return jsonify({'pdfs': pdfs})


@app.route('/api/questions/<path:filename>')
def get_questions(filename):
    """Get all parsed questions from a specific PDF file."""
    filepath = os.path.join(PDF_DIR, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    if not filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Not a PDF file'}), 400
    
    try:
        questions = parse_pdf_questions(filepath)
        return jsonify({
            'filename': filename,
            'display_name': os.path.splitext(filename)[0],
            'total_questions': len(questions),
            'questions': questions,
        })
    except Exception as e:
        return jsonify({'error': f'Failed to parse PDF: {str(e)}'}), 500


@app.route('/static/<path:path>')
def send_static(path):
    """Serve static files."""
    return send_from_directory('static', path)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    print("=" * 60)
    print("  Platform Pengerjaan Soal")
    print(f"  Buka browser di: http://localhost:{port}")
    print("=" * 60)
    
    pdfs = get_available_pdfs()
    if pdfs:
        print(f"\n  PDF terdeteksi: {len(pdfs)} file")
        for pdf in pdfs:
            print(f"    - {pdf['display_name']} ({pdf['size_kb']} KB)")
    else:
        print("\n  [!] Tidak ada file PDF ditemukan di folder ini")
    
    print("=" * 60)
    app.run(debug=debug, host='0.0.0.0', port=port)
