# OCR Parse – PDF Review Annotation Tool

Upload a PDF, review extracted text, and export a Word document with **tracked changes** and **comments**. Free, private, and zero config.

- **Free OCR** – Uses Tesseract (no API keys needed)
- **No document retention** – Files are deleted after your session ends
- **One-click deploy** – Docker or Render.com (free tier)

---

## Deploy to Render.com (Free – Recommended)

The fastest way to get running. No local install needed.

1. **Fork this repo** on GitHub
2. Go to [render.com](https://render.com) and sign up (free)
3. Click **New → Web Service**
4. Connect your GitHub account and select your forked `Ocr-parse` repo
5. Render auto-detects the `render.yaml` – click **Apply**
6. Wait for the build to finish (~3–5 minutes)
7. Open the provided URL and upload your PDF

> **That's it.** Render provides a free HTTPS URL. The app uses Tesseract OCR (pre-installed in Docker) and deletes all documents after your session ends.

---

## Deploy with Docker (Local)

```bash
# Build
docker build -t ocr-parse .

# Run (maps local port 5000 → container port 10000)
docker run -p 5000:10000 -e SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") ocr-parse
```

Open **http://localhost:5000** and upload your PDF.

---

## Run Locally (Without Docker)

### Prerequisites

| Dependency | Install |
|---|---|
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) |
| **Tesseract OCR** | `sudo apt install tesseract-ocr` (Linux) · [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) (Windows) · `brew install tesseract` (Mac) |
| **Poppler** | `sudo apt install poppler-utils` (Linux) · [oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases) (Windows) · `brew install poppler` (Mac) |

### Steps

```bash
git clone https://github.com/r987r/Ocr-parse.git
cd Ocr-parse
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000**.

---

## How to Use

1. **Upload** your PDF (the one with handwritten or printed annotations)
2. Optionally upload the **original document** (.docx) as the base
3. **Review** the OCR-extracted text side-by-side with the PDF pages
4. Click **➕ Add** on any text block to create an annotation, or add manually
5. Choose the annotation type: **Comment** (Word comment balloon), **Insert** (tracked insertion), or **Delete** (tracked deletion)
6. Click **💾 Make File** to download a `.docx` with all annotations
7. Open in Microsoft Word → **Review tab** to see comments and tracked changes

---

## Privacy & Security

- Uses **Tesseract OCR** – all processing happens on the server, no data sent to third-party APIs
- **No document retention** – uploaded files and session data are automatically deleted after export or when you leave the page. A background cleanup also removes any orphaned sessions after 30 minutes
- For shared deployments, set the `SECRET_KEY` environment variable (Render does this automatically)

---

## Running Tests

```bash
python -m unittest test_app -v
```

---

## File Structure

```
Ocr-parse/
├── app.py              # Flask web application
├── ocr_engine.py       # Tesseract OCR engine
├── word_export.py      # Word document export (comments & tracked changes)
├── test_app.py         # Test suite with fake PDF
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker image (Tesseract + Poppler pre-installed)
├── render.yaml         # Render.com deployment config
├── templates/
│   ├── index.html      # Upload page
│   └── review.html     # Review & annotation page
└── static/
    └── css/
        └── style.css   # Stylesheet
```
