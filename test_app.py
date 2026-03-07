"""
Test suite for OCR Parse.

Creates a fake PDF with known text, uploads it to the app,
verifies OCR extraction, and tests the full pipeline including export.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpdf import FPDF

# Ensure uploads go to a temp directory during tests
_test_upload_dir = tempfile.mkdtemp(prefix="ocr_parse_test_")

# Patch UPLOAD_BASE before importing app
import app as app_module

app_module.UPLOAD_BASE = Path(_test_upload_dir)

from app import app


def _make_test_pdf(text_lines=None):
    """Generate a simple PDF with known text content."""
    if text_lines is None:
        text_lines = [
            "INVOICE #12345",
            "Date: January 15, 2025",
            "",
            "Bill To: John Smith",
            "123 Main Street",
            "Springfield, IL 62701",
            "",
            "Item: Widget A - Qty: 10 - Price: $5.00",
            "Item: Widget B - Qty: 5 - Price: $12.50",
            "",
            "Subtotal: $112.50",
            "Tax (8%): $9.00",
            "Total: $121.50",
            "",
            "Notes: Please review and approve.",
        ]

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)

    for line in text_lines:
        if line == "":
            pdf.ln(8)
        else:
            pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf


class TestOCRParse(unittest.TestCase):
    """End-to-end tests for the OCR Parse application."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        # Clean up any session directories created during tests
        for child in Path(_test_upload_dir).iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    def test_index_page(self):
        """The upload page loads successfully."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"OCR Parse", resp.data)

    def test_health_check(self):
        """The health endpoint returns ok."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "ok")

    def test_upload_no_file(self):
        """Upload without a file returns an error."""
        resp = self.client.post("/upload")
        self.assertEqual(resp.status_code, 400)

    def test_upload_and_process(self):
        """Upload a PDF and run OCR – verify text is extracted."""
        pdf_buf = _make_test_pdf()

        # Upload
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        session_id = data["session_id"]
        self.assertTrue(session_id)
        self.assertIn("/review/", data["redirect"])

        # Process (OCR)
        resp = self.client.post(f"/api/process/{session_id}")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["num_pages"], 1)

        # Verify OCR extracted some text
        results = data["results"]
        self.assertTrue(len(results) > 0)
        all_text = " ".join(
            block["text"] for page in results for block in page["blocks"]
        )
        # The OCR should find at least some of these words
        found_keywords = sum(
            1
            for kw in ["INVOICE", "12345", "John", "Smith", "Widget", "Total"]
            if kw.lower() in all_text.lower()
        )
        self.assertGreaterEqual(
            found_keywords, 2, f"Expected to find keywords in OCR text: {all_text}"
        )

    def test_full_pipeline(self):
        """Full pipeline: upload → OCR → add annotations → export docx."""
        pdf_buf = _make_test_pdf()

        # Upload
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf")},
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        # Process
        resp = self.client.post(f"/api/process/{session_id}")
        self.assertEqual(resp.status_code, 200)

        # Get page image
        resp = self.client.get(f"/api/page/{session_id}/1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "image/png")

        # Save annotations
        annotations = [
            {"id": "test-1", "text": "Please fix this amount", "type": "comment", "page": 1},
            {"id": "test-2", "text": "New line item added", "type": "insert", "page": 1},
        ]
        resp = self.client.post(
            f"/api/annotations/{session_id}",
            data=json.dumps(annotations),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # Export
        resp = self.client.post(f"/api/export/{session_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("wordprocessingml", resp.content_type)
        self.assertGreater(len(resp.data), 1000)  # docx should be non-trivial

    def test_cleanup(self):
        """Cleanup endpoint removes session files."""
        pdf_buf = _make_test_pdf()

        # Upload
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf")},
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        sdir = Path(_test_upload_dir) / session_id
        self.assertTrue(sdir.exists())

        # Cleanup
        resp = self.client.post(f"/api/cleanup/{session_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(sdir.exists())

    def test_tesseract_engine_only(self):
        """Verify the app always uses Tesseract regardless of form data."""
        pdf_buf = _make_test_pdf()

        # Try to select openai engine – should be ignored
        resp = self.client.post(
            "/upload",
            data={
                "annotated_pdf": (pdf_buf, "test.pdf"),
                "ocr_engine": "openai",
                "api_key": "fake-key",
            },
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        config_path = Path(_test_upload_dir) / session_id / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        self.assertEqual(config["ocr_engine"], "tesseract")
        self.assertEqual(config["api_key"], "")

    def test_upload_too_large_returns_json_413(self):
        """When the upload exceeds MAX_CONTENT_LENGTH the 413 handler returns JSON."""
        # Temporarily set a very small limit so we can trigger it with a tiny payload
        original_limit = app.config["MAX_CONTENT_LENGTH"]
        app.config["MAX_CONTENT_LENGTH"] = 1  # 1 byte
        try:
            pdf_buf = _make_test_pdf()
            resp = self.client.post(
                "/upload",
                data={"annotated_pdf": (pdf_buf, "big.pdf")},
                content_type="multipart/form-data",
            )
            self.assertEqual(resp.status_code, 413)
            data = json.loads(resp.data)
            self.assertIn("error", data)
            self.assertIn("error_type", data)
            self.assertEqual(data["error_type"], "file_too_large")
            self.assertIn("too large", data["error"].lower())
        finally:
            app.config["MAX_CONTENT_LENGTH"] = original_limit

    def test_api_404_returns_json(self):
        """API 404 responses return JSON, not HTML."""
        resp = self.client.post("/api/process/nonexistent-session-id")
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_api_500_returns_json(self):
        """A forced 500 on an API route returns JSON, not HTML."""
        from unittest.mock import patch

        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf")},
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        # Patch _save_json to raise inside the process route to trigger the 500 handler
        with patch("app._save_json", side_effect=RuntimeError("forced error")):
            resp = self.client.post(f"/api/process/{session_id}")

        self.assertEqual(resp.status_code, 500)
        data = json.loads(resp.data)
        self.assertIn("error", data)
        # Must be parseable as JSON – not an HTML page
        self.assertEqual(resp.content_type, "application/json")

    def test_debug_log_endpoints(self):
        """Debug log is empty by default; upload failure with debug_mode stores an entry."""
        import app as app_module
        from pathlib import Path as _Path

        # Point the debug log to the test upload directory
        original_path = app_module.DEBUG_LOG_PATH
        app_module.DEBUG_LOG_PATH = _Path(_test_upload_dir) / "debug_log.json"
        # Ensure it starts empty
        if app_module.DEBUG_LOG_PATH.exists():
            app_module.DEBUG_LOG_PATH.unlink()

        try:
            # GET debug log – should be empty
            resp = self.client.get("/api/debug/log")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(json.loads(resp.data), [])

            # Trigger a failure with debug_mode=1 (wrong file type)
            resp = self.client.post(
                "/upload",
                data={
                    "annotated_pdf": (io.BytesIO(b"not a pdf"), "test.txt"),
                    "debug_mode": "1",
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(resp.status_code, 400)

            # Debug log should now have one entry
            resp = self.client.get("/api/debug/log")
            log = json.loads(resp.data)
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0]["event"], "upload_failed")
            self.assertIn("error_type", log[0])

            # Clear the log
            resp = self.client.post("/api/debug/clear")
            self.assertEqual(resp.status_code, 200)

            # Confirm it is empty again
            resp = self.client.get("/api/debug/log")
            self.assertEqual(json.loads(resp.data), [])

        finally:
            app_module.DEBUG_LOG_PATH = original_path

    def test_debug_mode_off_does_not_store_log(self):
        """Upload failure without debug_mode=1 does NOT write to debug log."""
        import app as app_module
        from pathlib import Path as _Path

        original_path = app_module.DEBUG_LOG_PATH
        app_module.DEBUG_LOG_PATH = _Path(_test_upload_dir) / "debug_log_off.json"
        if app_module.DEBUG_LOG_PATH.exists():
            app_module.DEBUG_LOG_PATH.unlink()

        try:
            # Wrong file type, debug_mode NOT set
            self.client.post(
                "/upload",
                data={"annotated_pdf": (io.BytesIO(b"not a pdf"), "test.txt")},
                content_type="multipart/form-data",
            )
            # Log file should not have been created
            self.assertFalse(app_module.DEBUG_LOG_PATH.exists())
        finally:
            app_module.DEBUG_LOG_PATH = original_path

    def test_process_returns_debug_info_when_enabled(self):
        """Processing returns debug info when DEBUG_MODE is on."""
        import app as app_module

        original_debug = app_module.DEBUG_MODE
        app_module.DEBUG_MODE = True
        try:
            pdf_buf = _make_test_pdf()
            resp = self.client.post(
                "/upload",
                data={"annotated_pdf": (pdf_buf, "test.pdf")},
                content_type="multipart/form-data",
            )
            data = json.loads(resp.data)
            session_id = data["session_id"]

            resp = self.client.post(f"/api/process/{session_id}")
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.data)
            self.assertTrue(data["success"])

            # Debug info should be present
            self.assertIn("debug", data)
            debug = data["debug"]
            self.assertTrue(debug["debug_mode"])
            self.assertIn("tesseract_version", debug)
            self.assertIn("poppler_version", debug)
            self.assertIn("steps", debug)
            self.assertGreater(len(debug["steps"]), 0)
            self.assertIn("session_id", debug)
            self.assertIn("pdf_file_size_bytes", debug)
            self.assertIn("total_elapsed_sec", debug)

            # Verify steps include pdf_to_images and at least one ocr page
            step_names = [s["step"] for s in debug["steps"]]
            self.assertIn("pdf_to_images", step_names)
            self.assertTrue(any(s.startswith("ocr_page_") for s in step_names))
        finally:
            app_module.DEBUG_MODE = original_debug

    def test_process_no_debug_info_when_disabled(self):
        """Processing omits debug info when DEBUG_MODE is off."""
        import app as app_module

        original_debug = app_module.DEBUG_MODE
        app_module.DEBUG_MODE = False
        try:
            pdf_buf = _make_test_pdf()
            resp = self.client.post(
                "/upload",
                data={"annotated_pdf": (pdf_buf, "test.pdf")},
                content_type="multipart/form-data",
            )
            data = json.loads(resp.data)
            session_id = data["session_id"]

            resp = self.client.post(f"/api/process/{session_id}")
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.data)
            self.assertTrue(data["success"])
            self.assertNotIn("debug", data)
        finally:
            app_module.DEBUG_MODE = original_debug

    def test_debug_info_endpoint(self):
        """The /api/debug/info endpoint returns system diagnostics."""
        import app as app_module

        original_debug = app_module.DEBUG_MODE
        app_module.DEBUG_MODE = True
        try:
            resp = self.client.get("/api/debug/info")
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.data)
            self.assertTrue(data["debug_mode"])
            self.assertIn("tesseract_version", data)
            self.assertIn("poppler_version", data)
        finally:
            app_module.DEBUG_MODE = original_debug

    def test_debug_info_endpoint_disabled(self):
        """The /api/debug/info endpoint indicates off when DEBUG_MODE is off."""
        import app as app_module

        original_debug = app_module.DEBUG_MODE
        app_module.DEBUG_MODE = False
        try:
            resp = self.client.get("/api/debug/info")
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.data)
            self.assertFalse(data["debug_mode"])
        finally:
            app_module.DEBUG_MODE = original_debug


    def test_upload_stores_email_and_token(self):
        """Upload with email stores email and access token in session config."""
        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf"), "email": "tester@example.com"},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        session_id = data["session_id"]

        config_path = Path(_test_upload_dir) / session_id / "config.json"
        with open(config_path) as f:
            config = json.load(f)

        self.assertEqual(config["email"], "tester@example.com")
        self.assertIn("access_token", config)
        self.assertGreater(len(config["access_token"]), 20)

    def test_upload_invalid_email_rejected(self):
        """Upload with an invalid email address returns 400."""
        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf"), "email": "not-an-email"},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("email", data["error"].lower())

    def test_upload_without_email_still_works(self):
        """Upload without email succeeds and config has empty email."""
        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        config_path = Path(_test_upload_dir) / data["session_id"] / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        self.assertEqual(config["email"], "")

    def test_access_token_redirect(self):
        """The /r/<token> route redirects to the review page."""
        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf"), "email": "user@example.com"},
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        config_path = Path(_test_upload_dir) / session_id / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        access_token = config["access_token"]

        resp = self.client.get(f"/r/{access_token}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/review/{session_id}", resp.headers.get("Location", ""))

    def test_access_token_invalid_returns_404(self):
        """An unknown or malformed access token returns 404."""
        self.assertEqual(self.client.get("/r/nonexistent-token").status_code, 404)
        self.assertEqual(self.client.get("/r/../../etc/passwd").status_code, 404)

    def test_cleanup_removes_token(self):
        """Cleanup removes the access token from the registry."""
        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf"), "email": "user@example.com"},
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        config_path = Path(_test_upload_dir) / session_id / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        access_token = config["access_token"]

        # Token works before cleanup
        resp = self.client.get(f"/r/{access_token}")
        self.assertEqual(resp.status_code, 302)

        # Cleanup
        self.client.post(f"/api/cleanup/{session_id}")

        # Token no longer resolves
        resp = self.client.get(f"/r/{access_token}")
        self.assertEqual(resp.status_code, 404)

    def test_process_returns_cached_results_on_second_call(self):
        """Second call to /api/process returns cached results instantly."""
        pdf_buf = _make_test_pdf()
        resp = self.client.post(
            "/upload",
            data={"annotated_pdf": (pdf_buf, "test.pdf")},
            content_type="multipart/form-data",
        )
        data = json.loads(resp.data)
        session_id = data["session_id"]

        # First call – processes normally
        resp = self.client.post(f"/api/process/{session_id}")
        self.assertEqual(resp.status_code, 200)
        first_data = json.loads(resp.data)
        self.assertTrue(first_data["success"])
        self.assertFalse(first_data.get("cached", False))

        # Second call – should serve cached results
        resp = self.client.post(f"/api/process/{session_id}")
        self.assertEqual(resp.status_code, 200)
        second_data = json.loads(resp.data)
        self.assertTrue(second_data["success"])
        self.assertTrue(second_data.get("cached", False))


class TestOCREngine(unittest.TestCase):
    """Unit tests for OCR engine."""

    def test_tesseract_extract(self):
        """Tesseract extracts text from a simple image."""
        from PIL import Image, ImageDraw, ImageFont
        from ocr_engine import OCREngine

        # Create a test image with text
        img = Image.new("RGB", (400, 100), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((20, 30), "Hello World Test 123", fill="black")

        engine = OCREngine(engine="tesseract")
        blocks = engine.extract_text_blocks(img)

        all_text = " ".join(b["text"] for b in blocks).lower()
        self.assertIn("hello", all_text)
        self.assertIn("world", all_text)


class TestWordExport(unittest.TestCase):
    """Unit tests for Word document export."""

    def test_create_document_with_annotations(self):
        """Word export creates a valid docx file."""
        from word_export import create_word_document

        annotations = [
            {"text": "Test comment", "type": "comment", "page": 1},
            {"text": "Inserted text", "type": "insert", "page": 1},
            {"text": "Deleted text", "type": "delete", "page": 1},
        ]

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name

        try:
            create_word_document(annotations, None, output_path)
            self.assertTrue(os.path.exists(output_path))
            self.assertGreater(os.path.getsize(output_path), 1000)
        finally:
            os.unlink(output_path)


if __name__ == "__main__":
    unittest.main()
