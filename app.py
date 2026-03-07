"""
OCR Parse - PDF Review Annotation Web App
Flask application entry point.

Uses Tesseract (free, local) for OCR.  Documents are stored for up to
24 hours.  A unique access token is emailed to the uploader so they can
return to their document at any time within that window.
"""

import os
import re
import uuid
import json
import shutil
import time
import secrets
import smtplib
import threading
import logging
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

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

# Session auto-cleanup: delete session files after this many seconds (default 24 h)
SESSION_MAX_AGE_SECONDS = int(os.environ.get("SESSION_MAX_AGE", "86400"))

# ---------------------------------------------------------------------------
# Email / SMTP configuration (all optional – set to enable notifications)
# ---------------------------------------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER

# Debug mode: set DEBUG_MODE=1 to include verbose processing info in API
# responses and on the review page.  Disable for production deployments.
DEBUG_MODE = os.environ.get("DEBUG_MODE", "1") == "1"

# Debug log: persists failure metadata when the user opts in via the UI
DEBUG_LOG_PATH = UPLOAD_BASE / "debug_log.json"
_debug_log_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Access-token registry
# ---------------------------------------------------------------------------
# Maps a 256-bit URL-safe token → session UUID so email links are unguessable.
# Stored in uploads/tokens.json (separate from individual session dirs).
_tokens_lock = threading.Lock()


def _tokens_file() -> Path:
    """Return the path to the token registry (evaluated dynamically so test
    overrides of UPLOAD_BASE are respected)."""
    return UPLOAD_BASE / "tokens.json"


def _register_token(token: str, session_id: str) -> None:
    """Persist a new token → session_id mapping."""
    with _tokens_lock:
        registry = _load_json(_tokens_file(), {})
        registry[token] = session_id
        _save_json(_tokens_file(), registry)


def _get_session_for_token(token: str) -> str | None:
    """Return the session_id for *token*, or ``None`` if not found."""
    with _tokens_lock:
        registry = _load_json(_tokens_file(), {})
        return registry.get(token)


def _unregister_tokens_for_session(session_id: str) -> None:
    """Remove every token that maps to *session_id*."""
    with _tokens_lock:
        registry = _load_json(_tokens_file(), {})
        updated = {k: v for k, v in registry.items() if v != session_id}
        if len(updated) != len(registry):
            _save_json(_tokens_file(), updated)


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


def _append_debug_log(entry: dict[str, Any]):
    """Append one failure entry to the persistent debug log (max 200 entries)."""
    with _debug_log_lock:
        log = _load_json(DEBUG_LOG_PATH, [])
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]
        _save_json(DEBUG_LOG_PATH, log)


def _log_upload_failure(
    debug_mode: bool,
    error_msg: str,
    error_type: str,
    extra: dict[str, Any] | None = None,
):
    """Log an upload failure to the server log; persist metadata when debug mode is on."""
    logging.warning("Upload failed [%s]: %s", error_type, error_msg)
    if debug_mode:
        entry: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "upload_failed",
            "error_type": error_type,
            "error": error_msg,
        }
        ann = request.files.get("annotated_pdf")
        if ann and ann.filename:
            entry["filename"] = secure_filename(ann.filename)
        if request.content_length:
            entry["content_length_bytes"] = request.content_length
        if extra:
            entry.update(extra)
        _append_debug_log(entry)


def _cleanup_session(session_id: str):
    """Remove all files for a session and deregister its access token."""
    _unregister_tokens_for_session(session_id)
    sdir = UPLOAD_BASE / session_id
    if sdir.exists():
        shutil.rmtree(sdir, ignore_errors=True)


def _cleanup_old_sessions():
    """Remove sessions older than SESSION_MAX_AGE_SECONDS and purge their tokens."""
    now = time.time()
    if not UPLOAD_BASE.exists():
        return
    removed: list[str] = []
    for child in UPLOAD_BASE.iterdir():
        if child.is_dir():
            try:
                age = now - child.stat().st_mtime
                if age > SESSION_MAX_AGE_SECONDS:
                    shutil.rmtree(child, ignore_errors=True)
                    removed.append(child.name)
            except OSError:
                pass
    if removed:
        with _tokens_lock:
            registry = _load_json(_tokens_file(), {})
            registry = {k: v for k, v in registry.items() if v not in removed}
            _save_json(_tokens_file(), registry)


def _start_cleanup_timer():
    """Run periodic cleanup every 10 minutes."""
    _cleanup_old_sessions()
    t = threading.Timer(600, _start_cleanup_timer)
    t.daemon = True
    t.start()


# Start the cleanup timer when the module is loaded
_start_cleanup_timer()


def _get_system_debug_info() -> dict[str, Any]:
    """Collect system-level diagnostics useful for debugging processing failures."""
    info: dict[str, Any] = {
        "debug_mode": DEBUG_MODE,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Tesseract version
    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["tesseract_version"] = (result.stdout or result.stderr).split("\n")[0]
        info["tesseract_installed"] = True
    except FileNotFoundError:
        info["tesseract_version"] = "NOT INSTALLED"
        info["tesseract_installed"] = False
    except Exception as exc:
        info["tesseract_version"] = f"error: {exc}"
        info["tesseract_installed"] = False

    # Poppler / pdftoppm version
    try:
        result = subprocess.run(
            ["pdftoppm", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["poppler_version"] = (result.stderr or result.stdout).strip()
        info["poppler_installed"] = True
    except FileNotFoundError:
        info["poppler_version"] = "NOT INSTALLED"
        info["poppler_installed"] = False
    except Exception as exc:
        info["poppler_version"] = f"error: {exc}"
        info["poppler_installed"] = False

    # Disk space for uploads directory
    try:
        import shutil as _shutil

        usage = _shutil.disk_usage(str(UPLOAD_BASE))
        info["disk_free_mb"] = round(usage.free / (1024 * 1024))
        info["disk_total_mb"] = round(usage.total / (1024 * 1024))
    except Exception:
        pass

    # Python / PIL versions
    try:
        from PIL import __version__ as pil_ver

        info["pillow_version"] = pil_ver
    except Exception:
        pass

    try:
        import pdf2image

        info["pdf2image_version"] = getattr(pdf2image, "__version__", "unknown")
    except Exception:
        pass

    return info


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _send_notification_email(to_email: str, base_url: str, access_token: str) -> None:
    """Send a processing-complete notification with a unique document link.

    Silently logs and returns when SMTP is not configured so callers never
    have to worry about error handling.
    """
    review_url = f"{base_url.rstrip('/')}/r/{access_token}"

    if not SMTP_HOST or not SMTP_USER:
        logging.info(
            "Email notification skipped (SMTP not configured). Review link: %s",
            review_url,
        )
        return

    subject = "Your OCR Parse document is ready"

    text_body = (
        "Your document has finished processing and is ready for review.\n\n"
        f"Access your document here (valid for 24 hours):\n{review_url}\n\n"
        "Once you are done reviewing and exporting, you can delete the document "
        "from the review page.\n\n"
        "– OCR Parse"
    )

    html_body = (
        '<html><body style="font-family:Arial,sans-serif;max-width:600px;'
        'margin:0 auto;padding:20px">'
        '<h2 style="color:#2563eb">📄 Your document is ready</h2>'
        "<p>Your OCR Parse document has finished processing and is ready for review.</p>"
        '<p style="margin:24px 0">'
        f'<a href="{review_url}" style="background:#2563eb;color:white;padding:12px 24px;'
        'text-decoration:none;border-radius:6px;display:inline-block">'
        "📄 Open Your Document →</a>"
        "</p>"
        "<p><strong>⏰ This link will expire in 24 hours.</strong></p>"
        "<p>Once you are done reviewing and exporting your document, you can "
        "delete it from the review page.</p>"
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0">'
        '<p style="font-size:12px;color:#6b7280">OCR Parse – Free Tesseract OCR. '
        "Your document will be automatically deleted after 24 hours.</p>"
        "</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        logging.info("Notification email sent to %s", to_email)
    except Exception as exc:
        logging.warning("Failed to send notification email to %s: %s", to_email, exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    max_upload_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
    return render_template("index.html", max_upload_mb=max_upload_mb)


@app.errorhandler(413)
def request_entity_too_large(error):
    """Return a clear JSON error when the uploaded file exceeds MAX_CONTENT_LENGTH."""
    max_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
    msg = (
        f"Upload failed: the file is too large. "
        f"The maximum allowed upload size is {max_mb} MB. "
        f"Please reduce your file size and try again."
    )
    logging.warning(
        "Upload rejected – request entity too large (413). "
        "Content-Length header: %s bytes",
        request.content_length,
    )
    return jsonify({"error": msg, "error_type": "file_too_large"}), 413


@app.errorhandler(404)
def not_found_error(error):
    """Return JSON for API 404s; simple HTML for UI routes."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Resource not found.", "error_type": "not_found"}), 404
    return (
        "<h1>404 – Not Found</h1><p><a href='/'>Go back to the upload page</a></p>",
        404,
    )


@app.errorhandler(500)
def internal_server_error_handler(error):
    """Return JSON for API 500s so the frontend never sees an HTML error page."""
    if request.path.startswith("/api/"):
        logging.exception("Unhandled server error on %s", request.path)
        return (
            jsonify(
                {
                    "error": "An unexpected server error occurred. Please try again.",
                    "error_type": "server_error",
                }
            ),
            500,
        )
    return (
        "<h1>500 – Internal Server Error</h1>"
        "<p>Something went wrong. <a href='/'>Go back to the upload page</a></p>",
        500,
    )


@app.route("/health")
def health():
    """Health check endpoint for deployment platforms."""
    return jsonify({"status": "ok"})


@app.route("/upload", methods=["POST"])
def upload():
    """Receive uploaded files, start a session, redirect to review page."""
    debug_mode = request.form.get("debug_mode") == "1"

    if "annotated_pdf" not in request.files:
        _log_upload_failure(debug_mode, "No annotated PDF file provided.", "missing_file")
        return jsonify({"error": "No annotated PDF file provided."}), 400

    ann_file = request.files["annotated_pdf"]
    if not ann_file.filename or not _allowed(ann_file.filename, ALLOWED_PDF):
        _log_upload_failure(
            debug_mode,
            f"Invalid file type: '{ann_file.filename}'.",
            "invalid_file_type",
            {"filename": secure_filename(ann_file.filename or "")},
        )
        return jsonify({"error": "Please upload a PDF file for the annotated document."}), 400

    # Optional email for completion notification
    email = request.form.get("email", "").strip()
    if email and not _is_valid_email(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    session_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    sdir = _session_dir(session_id)

    # Save annotated PDF
    try:
        ann_file.save(str(sdir / "annotated.pdf"))
    except Exception as exc:
        _log_upload_failure(debug_mode, f"File save failed: {exc}", "save_error")
        return jsonify({"error": f"Failed to save uploaded file: {exc}"}), 500

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
        "email": email,
        "access_token": access_token,
        "base_url": request.host_url,
        "email_sent": False,
    }
    _save_json(sdir / "config.json", config)
    _save_json(sdir / "annotations.json", [])

    _register_token(access_token, session_id)

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
    return render_template("review.html", session_id=session_id, config=config, debug_mode=DEBUG_MODE)


@app.route("/r/<access_token>")
def token_redirect(access_token):
    """Redirect a unique email access token to the corresponding review session."""
    if not re.match(r"^[A-Za-z0-9_-]+$", access_token):
        abort(404)
    session_id = _get_session_for_token(access_token)
    if not session_id:
        abort(404)
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        abort(404)
    return redirect(url_for("review", session_id=session_id))


def _build_ocr_response(
    results: list,
    session_id: str,
    debug_info: "dict[str, Any] | None" = None,
    cached: bool = False,
) -> dict[str, Any]:
    """Build a standardised JSON-serialisable dict for a process response."""
    resp: dict[str, Any] = {
        "success": True,
        "num_pages": len(results),
        "results": results,
    }
    if cached:
        resp["cached"] = True
    if DEBUG_MODE:
        if debug_info is None:
            debug_info = _get_system_debug_info()
            debug_info["session_id"] = session_id
        if cached:
            debug_info["cached"] = True
        resp["debug"] = debug_info
    return resp


def _find_page_image(pages_dir: Path, page_num: int) -> "Path | None":
    """Return the PNG path for *page_num* in *pages_dir*, or ``None``."""
    for pad in range(1, 5):
        candidate = pages_dir / f"page{str(page_num).zfill(pad)}.png"
        if candidate.exists():
            return candidate
    # Fallback: sort all PNGs and return the N-th one (handles any naming convention)
    pngs = sorted(pages_dir.glob("*.png"))
    if 1 <= page_num <= len(pngs):
        return pngs[page_num - 1]
    return None


def _count_png_pages(pages_dir: Path) -> int:
    """Return the number of PNG images in *pages_dir*."""
    return len(list(pages_dir.glob("*.png")))


@app.route("/api/process/<session_id>", methods=["POST"])
def process(session_id):
    """Convert PDF to images and run OCR on each page.

    Results are cached: if OCR has already been run for this session the
    cached data is returned immediately without re-processing.
    """
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404

    config = _load_json(sdir / "config.json", {})
    pdf_path = sdir / "annotated.pdf"
    if not pdf_path.exists():
        return jsonify({"error": "Annotated PDF not found."}), 404

    # ------------------------------------------------------------------
    # Return cached results if OCR has already been run for this session
    # ------------------------------------------------------------------
    cached_path = sdir / "ocr_results.json"
    pages_dir = sdir / "pages"
    if cached_path.exists() and pages_dir.exists() and any(pages_dir.glob("*.png")):
        cached_results = _load_json(cached_path, [])
        if cached_results:
            return jsonify(_build_ocr_response(cached_results, session_id, cached=True))

    # Collect debug info when debug mode is active
    debug_info: dict[str, Any] = {}
    total_start = time.time()
    if DEBUG_MODE:
        debug_info = _get_system_debug_info()
        debug_info["session_id"] = session_id
        debug_info["pdf_file_size_bytes"] = pdf_path.stat().st_size
        debug_info["pdf_file_size_mb"] = round(pdf_path.stat().st_size / (1024 * 1024), 2)
        debug_info["ocr_engine"] = config.get("ocr_engine", "tesseract")
        debug_info["steps"] = []

    pages_dir = sdir / "pages"
    pages_dir.mkdir(exist_ok=True)

    # -- Convert PDF pages to PNG images --
    step_start = time.time()
    try:
        import pdf2image

        images = pdf2image.convert_from_path(
            str(pdf_path),
            dpi=150,
            output_folder=str(pages_dir),
            fmt="png",
            output_file="page",
        )
        if DEBUG_MODE:
            elapsed = round(time.time() - step_start, 2)
            debug_info["steps"].append({
                "step": "pdf_to_images",
                "status": "ok",
                "elapsed_sec": elapsed,
                "num_pages": len(images),
                "dpi": 150,
            })
    except Exception as exc:
        if DEBUG_MODE:
            elapsed = round(time.time() - step_start, 2)
            debug_info["steps"].append({
                "step": "pdf_to_images",
                "status": "error",
                "elapsed_sec": elapsed,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            return jsonify({
                "error": f"PDF conversion failed: {exc}",
                "debug": debug_info,
            }), 500
        return jsonify({"error": f"PDF conversion failed: {exc}"}), 500

    # -- OCR each page --
    try:
        from ocr_engine import OCREngine

        ocr = OCREngine(
            engine=config.get("ocr_engine", "tesseract"),
            api_key=config.get("api_key", "") or None,
        )

        ocr_results = []
        for idx, img in enumerate(images):
            page_start = time.time()
            try:
                blocks = ocr.extract_text_blocks(img)
                if DEBUG_MODE:
                    elapsed = round(time.time() - page_start, 2)
                    total_text = " ".join(b["text"] for b in blocks)
                    debug_info["steps"].append({
                        "step": f"ocr_page_{idx + 1}",
                        "status": "ok",
                        "elapsed_sec": elapsed,
                        "num_blocks": len(blocks),
                        "total_chars": len(total_text),
                        "image_size": f"{img.width}x{img.height}",
                    })
            except Exception as exc:
                blocks = [{"text": f"[OCR error: {exc}]", "confidence": 0, "block_num": 0}]
                if DEBUG_MODE:
                    elapsed = round(time.time() - page_start, 2)
                    debug_info["steps"].append({
                        "step": f"ocr_page_{idx + 1}",
                        "status": "error",
                        "elapsed_sec": elapsed,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    })

            ocr_results.append({"page": idx + 1, "blocks": blocks})

        _save_json(sdir / "ocr_results.json", ocr_results)

        # Send notification email once (in a daemon thread so it doesn't delay the response)
        email = config.get("email", "")
        if email and not config.get("email_sent"):
            config["email_sent"] = True
            _save_json(sdir / "config.json", config)
            _base_url = config.get("base_url", "")
            _token = config.get("access_token", "")
            t = threading.Thread(
                target=_send_notification_email,
                args=(email, _base_url, _token),
                daemon=True,
            )
            t.start()

        # Build response
        if DEBUG_MODE:
            debug_info["total_elapsed_sec"] = round(time.time() - total_start, 2)
        return jsonify(_build_ocr_response(ocr_results, session_id, debug_info=debug_info))

    except Exception as exc:
        logging.exception("Unexpected OCR processing error for session %s", session_id)
        if DEBUG_MODE:
            debug_info["steps"].append({
                "step": "ocr_processing",
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            return jsonify({
                "error": f"OCR processing failed: {exc}",
                "debug": debug_info,
            }), 500
        return jsonify({"error": f"OCR processing failed: {exc}"}), 500


@app.route("/api/process/<session_id>/init", methods=["POST"])
def process_init(session_id):
    """Convert PDF to page images (no OCR) and return the page count.

    This is the first step in the page-by-page OCR flow.  The images are
    written to ``<session_dir>/pages/`` and reused on subsequent calls so the
    conversion never happens twice for the same session.
    """
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404

    pdf_path = sdir / "annotated.pdf"
    if not pdf_path.exists():
        return jsonify({"error": "Annotated PDF not found."}), 404

    pages_dir = sdir / "pages"

    # Return cached page count when images already exist
    if pages_dir.exists():
        png_count = _count_png_pages(pages_dir)
        if png_count > 0:
            fully_cached = False
            cached_path = sdir / "ocr_results.json"
            if cached_path.exists():
                cached_results = _load_json(cached_path, [])
                fully_cached = bool(cached_results)
            return jsonify({
                "success": True,
                "num_pages": png_count,
                "fully_cached": fully_cached,
            })

    pages_dir.mkdir(exist_ok=True)

    debug_info: dict[str, Any] = {}
    if DEBUG_MODE:
        debug_info = _get_system_debug_info()
        debug_info["session_id"] = session_id

    step_start = time.time()
    try:
        import pdf2image

        images = pdf2image.convert_from_path(
            str(pdf_path),
            dpi=150,
            output_folder=str(pages_dir),
            fmt="png",
            output_file="page",
        )
        num_pages = len(images)

        if DEBUG_MODE:
            debug_info["steps"] = [{
                "step": "pdf_to_images",
                "status": "ok",
                "elapsed_sec": round(time.time() - step_start, 2),
                "num_pages": num_pages,
                "dpi": 150,
            }]

        resp: dict[str, Any] = {"success": True, "num_pages": num_pages}
        if DEBUG_MODE:
            resp["debug"] = debug_info
        return jsonify(resp)

    except Exception as exc:
        if DEBUG_MODE:
            debug_info["steps"] = [{
                "step": "pdf_to_images",
                "status": "error",
                "elapsed_sec": round(time.time() - step_start, 2),
                "error": str(exc),
                "error_type": type(exc).__name__,
            }]
            return jsonify({"error": f"PDF conversion failed: {exc}", "debug": debug_info}), 500
        return jsonify({"error": f"PDF conversion failed: {exc}"}), 500


@app.route("/api/process/<session_id>/page/<int:page_num>", methods=["POST"])
def process_page(session_id, page_num):
    """Run OCR on a single page and return the extracted text blocks.

    Requires that ``/api/process/<session_id>/init`` has already been called so
    page images exist on disk.  Results are cached per-page; once every page has
    been processed the combined ``ocr_results.json`` file is written so the
    legacy ``/api/process/<session_id>`` endpoint can still serve cached results.
    """
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404

    pages_dir = sdir / "pages"
    if not pages_dir.exists() or _count_png_pages(pages_dir) == 0:
        return jsonify({"error": "Pages not yet converted. Call /init first."}), 400

    num_pages = _count_png_pages(pages_dir)

    if page_num < 1 or page_num > num_pages:
        return jsonify({"error": f"Page {page_num} out of range (1–{num_pages})."}), 400

    # Per-page cache
    per_page_cache = sdir / f"ocr_page_{page_num}.json"
    if per_page_cache.exists():
        cached_data = _load_json(per_page_cache, None)
        if cached_data is not None:
            cached_data["cached"] = True
            cached_data["num_pages"] = num_pages
            return jsonify(cached_data)

    # Full-result cache (written when all pages are done)
    full_cache = sdir / "ocr_results.json"
    if full_cache.exists():
        full_results = _load_json(full_cache, [])
        page_data = next((r for r in full_results if r["page"] == page_num), None)
        if page_data:
            return jsonify({
                "success": True,
                "page": page_num,
                "num_pages": num_pages,
                "blocks": page_data["blocks"],
                "cached": True,
            })

    # Locate the page image on disk
    img_path = _find_page_image(pages_dir, page_num)
    if not img_path:
        return jsonify({"error": f"Image for page {page_num} not found."}), 404

    try:
        from PIL import Image as _PILImage
        img = _PILImage.open(str(img_path))
    except Exception as exc:
        return jsonify({"error": f"Failed to load page image: {exc}"}), 500

    config = _load_json(sdir / "config.json", {})

    page_start = time.time()
    try:
        from ocr_engine import OCREngine

        ocr = OCREngine(
            engine=config.get("ocr_engine", "tesseract"),
            api_key=config.get("api_key", "") or None,
        )
        blocks = ocr.extract_text_blocks(img)
    except Exception as exc:
        blocks = [{"text": f"[OCR error: {exc}]", "confidence": 0, "block_num": 0}]

    result: dict[str, Any] = {
        "success": True,
        "page": page_num,
        "num_pages": num_pages,
        "blocks": blocks,
    }
    if DEBUG_MODE:
        elapsed = round(time.time() - page_start, 2)
        total_text = " ".join(b["text"] for b in blocks)
        result["debug"] = {
            "ocr_elapsed_sec": elapsed,
            "num_blocks": len(blocks),
            "total_chars": len(total_text),
        }

    # Save per-page cache
    _save_json(per_page_cache, result)

    # When every page is done, write the combined ocr_results.json
    combined = []
    all_done = True
    for p in range(1, num_pages + 1):
        p_cache = sdir / f"ocr_page_{p}.json"
        if p_cache.exists():
            p_data = _load_json(p_cache, None)
            if p_data:
                combined.append({"page": p, "blocks": p_data.get("blocks", [])})
                continue
        all_done = False
        break

    if all_done and len(combined) == num_pages:
        _save_json(full_cache, combined)

        # Send notification email once (after all pages processed)
        email = config.get("email", "")
        if email and not config.get("email_sent"):
            config["email_sent"] = True
            _save_json(sdir / "config.json", config)
            _base_url = config.get("base_url", "")
            _token = config.get("access_token", "")
            t = threading.Thread(
                target=_send_notification_email,
                args=(email, _base_url, _token),
                daemon=True,
            )
            t.start()

    return jsonify(result)


@app.route("/api/page/<session_id>/<int:page_num>")
def get_page_image(session_id, page_num):
    """Serve a PNG image of a PDF page (1-indexed)."""
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        abort(404)
    pages_dir = sdir / "pages"

    img_path = _find_page_image(pages_dir, page_num)
    if img_path:
        return send_file(str(img_path), mimetype="image/png")

    abort(404)


@app.route("/api/ocr_results/<session_id>")
def get_ocr_results(session_id):
    sdir = UPLOAD_BASE / session_id
    return jsonify(_load_json(sdir / "ocr_results.json", []))


@app.route("/api/pdf_annotations/<session_id>")
def get_pdf_annotations(session_id):
    """Extract embedded annotations (sticky notes, highlights, etc.) from the PDF.

    Returns a list of annotation objects compatible with the frontend annotation
    format so they can be pre-populated in the review interface.
    """
    sdir = UPLOAD_BASE / session_id
    if not sdir.exists():
        return jsonify({"error": "Session not found."}), 404

    pdf_path = sdir / "annotated.pdf"
    if not pdf_path.exists():
        return jsonify({"error": "PDF not found."}), 404

    try:
        import pymupdf  # PyMuPDF

        extracted: list[dict[str, Any]] = []
        doc = pymupdf.open(str(pdf_path))

        # Map PyMuPDF annotation type numbers to readable names
        _TYPE_TEXT = 0        # sticky note / text annotation
        _TYPE_HIGHLIGHT = 8   # yellow highlight
        _TYPE_UNDERLINE = 9
        _TYPE_SQUIGGLY = 10
        _TYPE_STRIKEOUT = 11
        _TYPE_FREETEXT = 2    # free text annotation

        for page_num, page in enumerate(doc, start=1):
            for annot in page.annots():
                atype_num = annot.type[0]
                info = annot.info or {}
                content = (info.get("content") or "").strip()

                # For mark-up annotations (highlight, underline, etc.), try to
                # grab the underlying text when the content field is empty.
                if not content and atype_num in (
                    _TYPE_HIGHLIGHT, _TYPE_UNDERLINE, _TYPE_SQUIGGLY, _TYPE_STRIKEOUT
                ):
                    words = page.get_text("words", clip=annot.rect)
                    content = " ".join(w[4] for w in words if len(w) > 4).strip()

                if not content:
                    continue

                # Map annotation type to the export comment type
                if atype_num in (_TYPE_HIGHLIGHT, _TYPE_UNDERLINE):
                    export_type = "comment"
                elif atype_num == _TYPE_STRIKEOUT:
                    export_type = "delete"
                else:
                    export_type = "comment"

                title = (info.get("title") or "").strip()
                subject = (info.get("subject") or "").strip()
                label_parts = [p for p in [title, subject] if p]
                label = " – ".join(label_parts) if label_parts else ""

                extracted.append({
                    "id": f"pdf-annot-{page_num}-{annot.xref}",
                    "text": (label + ": " + content) if label else content,
                    "type": export_type,
                    "page": page_num,
                    "source": "pdf_annotation",
                })

        doc.close()
        return jsonify(extracted)

    except ImportError:
        return jsonify({"error": "PyMuPDF not installed – cannot extract PDF annotations."}), 501
    except Exception as exc:
        logging.exception("Failed to extract PDF annotations for session %s", session_id)
        return jsonify({"error": f"Failed to extract annotations: {exc}"}), 500


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


@app.route("/api/debug/log")
def get_debug_log():
    """Return stored debug log entries (upload failure metadata)."""
    return jsonify(_load_json(DEBUG_LOG_PATH, []))


@app.route("/api/debug/clear", methods=["POST"])
def clear_debug_log():
    """Clear all stored debug log entries."""
    with _debug_log_lock:
        _save_json(DEBUG_LOG_PATH, [])
    return jsonify({"success": True})


@app.route("/api/debug/info")
def debug_system_info():
    """Return system diagnostics (tesseract version, poppler, etc.)."""
    if not DEBUG_MODE:
        return jsonify({"debug_mode": False, "message": "Debug mode is disabled."})
    return jsonify(_get_system_debug_info())


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
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)  # nosec B104 – dev server only; production uses Gunicorn
