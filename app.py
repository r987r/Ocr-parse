"""
OCR Parse - PDF Review Annotation Web App
Flask application entry point.

Uses Tesseract (free, local) for OCR. No document retention – session
files are deleted after export or automatically after a timeout.
"""

import os
import uuid
import json
import shutil
import time
import threading
import logging
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file,
    abort,
    redirect,
    url_for,
)
from werkzeug.utils import secure_filename

app = Flask(__name__)

_DEV_SECRET = "ocr-parse-dev-secret-change-me"
_secret_key = os.environ.get("SECRET_KEY", _DEV_SECRET)
if _secret_key == _DEV_SECRET:
    logging.warning(
        "Using the default development SECRET_KEY. "
        "Set the SECRET_KEY environment variable for any shared deployment."
    )
app.secret_key = _secret_key
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

UPLOAD_BASE = Path("uploads")
UPLOAD_BASE.mkdir(exist_ok=True)

ALLOWED_PDF = {"pdf"}
ALLOWED_DOC = {"pdf", "docx", "doc"}

# Session auto-cleanup: delete session files after this many seconds
SESSION_MAX_AGE_SECONDS = int(os.environ.get("SESSION_MAX_AGE", "1800"))  # 30 min


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allowed(filename, exts):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in exts


def _session_dir(session_id: str) -> Path:
    d = UPLOAD_BASE / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json(path: Path, default=None):
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return default if default is not None else []


def _save_json(path: Path, data):
    with path.open("w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _cleanup_session(session_id: str):
    """Remove all files for a session (no document retention)."""
    sdir = UPLOAD_BASE / session_id
    if sdir.exists():
        shutil.rmtree(sdir, ignore_errors=True)


def _cleanup_old_sessions():
    """Remove sessions older than SESSION_MAX_AGE_SECONDS."""
    now = time.time()
    if not UPLOAD_BASE.exists():
        return
    for child in UPLOAD_BASE.iterdir():
        if child.is_dir():
            try:
                age = now - child.stat().st_mtime
                if age > SESSION_MAX_AGE_SECONDS:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass


def _start_cleanup_timer():
    """Run periodic cleanup every 10 minutes."""
    _cleanup_old_sessions()
    t = threading.Timer(600, _start_cleanup_timer)
    t.daemon = True
    t.start()


# Start the cleanup timer when the module is loaded
_start_cleanup_timer()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    """Health check endpoint for deployment platforms."""
    return jsonify({"status": "ok"})


@app.route("/upload", methods=["POST"])
def upload():
    """Receive uploaded files, start a session, redirect to review page."""
    if "annotated_pdf" not in request.files:
        return jsonify({"error": "No annotated PDF file provided."}), 400

    ann_file = request.files["annotated_pdf"]
    if not ann_file.filename or not _allowed(ann_file.filename, ALLOWED_PDF):
        return jsonify({"error": "Please upload a PDF file for the annotated document."}), 400

    session_id = str(uuid.uuid4())
    sdir = _session_dir(session_id)

    # Save annotated PDF
    ann_file.save(str(sdir / "annotated.pdf"))

    # Save optional original document
    has_original = False
    orig_ext = None
    if "original_doc" in request.files:
        orig_file = request.files["original_doc"]
        if orig_file.filename and _allowed(orig_file.filename, ALLOWED_DOC):
            orig_ext = orig_file.filename.rsplit(".", 1)[1].lower()
            orig_file.save(str(sdir / f"original.{orig_ext}"))
            has_original = True

    # Persist config – always use Tesseract (free, local)
    config = {
        "session_id": session_id,
        "ocr_engine": "tesseract",
        "api_key": "",
        "has_original": has_original,
        "orig_ext": orig_ext,
    }
    _save_json(sdir / "config.json", config)
    _save_json(sdir / "annotations.json", [])

    return jsonify({"session_id": session_id, "redirect": f"/review/{session_id}"})


@app.route("/review/<session_id>")
def review(session_id):
    """Render the review/edit page."""
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        abort(404)
    config = _load_json(sdir / "config.json", {})
    if not config:
        abort(404)
    return render_template("review.html", session_id=session_id, config=config)


@app.route("/api/process/<session_id>", methods=["POST"])
def process(session_id):
    """Convert PDF to images and run OCR on each page."""
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404

    config = _load_json(sdir / "config.json", {})
    pdf_path = sdir / "annotated.pdf"
    if not pdf_path.exists():
        return jsonify({"error": "Annotated PDF not found."}), 404

    pages_dir = sdir / "pages"
    pages_dir.mkdir(exist_ok=True)

    # -- Convert PDF pages to PNG images --
    try:
        import pdf2image

        images = pdf2image.convert_from_path(
            str(pdf_path),
            dpi=150,
            output_folder=str(pages_dir),
            fmt="png",
            output_file="page",
        )
    except Exception as exc:
        return jsonify({"error": f"PDF conversion failed: {exc}"}), 500

    # -- OCR each page --
    from ocr_engine import OCREngine

    ocr = OCREngine(
        engine=config.get("ocr_engine", "tesseract"),
        api_key=config.get("api_key", "") or None,
    )

    ocr_results = []
    for idx, img in enumerate(images):
        try:
            blocks = ocr.extract_text_blocks(img)
        except Exception as exc:
            blocks = [{"text": f"[OCR error: {exc}]", "confidence": 0, "block_num": 0}]

        ocr_results.append({"page": idx + 1, "blocks": blocks})

    _save_json(sdir / "ocr_results.json", ocr_results)

    return jsonify({"success": True, "num_pages": len(images), "results": ocr_results})


@app.route("/api/page/<session_id>/<int:page_num>")
def get_page_image(session_id, page_num):
    """Serve a PNG image of a PDF page (1-indexed)."""
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        abort(404)
    pages_dir = sdir / "pages"

    # pdf2image may produce page1.png, page01.png, page001.png, etc.
    for pad in range(1, 5):
        fmt = f"page{str(page_num).zfill(pad)}.png"
        candidate = pages_dir / fmt
        if candidate.exists():
            return send_file(str(candidate), mimetype="image/png")

    # Fallback: glob
    matches = sorted(pages_dir.glob(f"*{page_num}*.png"))
    if matches:
        return send_file(str(matches[0]), mimetype="image/png")

    abort(404)


@app.route("/api/ocr_results/<session_id>")
def get_ocr_results(session_id):
    sdir = UPLOAD_BASE / session_id
    return jsonify(_load_json(sdir / "ocr_results.json", []))


@app.route("/api/annotations/<session_id>", methods=["GET"])
def get_annotations(session_id):
    sdir = UPLOAD_BASE / session_id
    return jsonify(_load_json(sdir / "annotations.json", []))


@app.route("/api/annotations/<session_id>", methods=["POST"])
def save_annotations(session_id):
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404
    data = request.get_json(force=True)
    if not isinstance(data, list):
        return jsonify({"error": "Expected a JSON array."}), 400
    _save_json(sdir / "annotations.json", data)
    return jsonify({"success": True})


@app.route("/api/export/<session_id>", methods=["POST"])
def export_document(session_id):
    """Generate and stream the Word document."""
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404

    config = _load_json(sdir / "config.json", {})
    annotations = _load_json(sdir / "annotations.json", [])

    # Find original docx
    original_path = None
    orig_ext = config.get("orig_ext")
    if orig_ext in ("docx", "doc"):
        candidate = sdir / f"original.{orig_ext}"
        if candidate.exists():
            original_path = str(candidate)

    output_path = str(sdir / "output.docx")

    try:
        from word_export import create_word_document

        create_word_document(annotations, original_path, output_path)
    except Exception as exc:
        return jsonify({"error": f"Export failed: {exc}"}), 500

    return send_file(
        output_path,
        as_attachment=True,
        download_name="reviewed_document.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
    )


@app.route("/api/cleanup/<session_id>", methods=["POST"])
def cleanup(session_id):
    """Delete all session files (no document retention)."""
    _cleanup_session(session_id)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  OCR Parse – PDF Review Annotation Tool")
    print("  Free Tesseract OCR · No document retention")
    print("=" * 60)
    print("  Open your browser and go to: http://localhost:5000")
    print("=" * 60)
    print()
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)
