# OCR Parse – PDF Review Annotation Tool

A local web application that reads a PDF containing **handwritten** or typed review comments, lets you review and edit the extracted annotations, and exports a **Word document (.docx)** with real Word **comments** and **tracked changes**.

---

## Features

| Feature | Description |
|---|---|
| **PDF upload** | Upload an annotated PDF (with handwritten or printed review marks) |
| **Original document** | Optionally upload the original `.docx` to use as the base |
| **Multiple OCR engines** | Tesseract (free, local), OpenAI GPT-4o Vision, or Google Cloud Vision |
| **Side-by-side review** | PDF page image on the left, extracted text blocks on the right |
| **Editable annotations** | Add, edit, and delete annotations; choose type: Comment / Insert / Delete |
| **Word export** | Exports `.docx` with real Word comment balloons and tracked changes |
| **Runs locally** | No data leaves your machine (unless you use a paid OCR engine) |

---

## Quick Start (Windows)

### 1 – Install Python 3.10+

Download from [python.org](https://www.python.org/downloads/windows/).  
During setup, check **"Add Python to PATH"**.

### 2 – Install Tesseract OCR (free engine)

1. Download the installer from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) (choose the latest `.exe`).
2. Run the installer. Note the installation path (default: `C:\Program Files\Tesseract-OCR\`).
3. Add Tesseract to your system PATH:
   - Open **Start → System Properties → Advanced → Environment Variables**
   - Under *System Variables*, find **Path** and click **Edit**
   - Click **New** and add `C:\Program Files\Tesseract-OCR`
4. Verify: open a new Command Prompt and run `tesseract --version`.

### 3 – Install Poppler (needed for PDF-to-image conversion)

1. Download the latest Windows build from [oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases).
2. Extract the ZIP to a folder, e.g. `C:\poppler\`.
3. Add `C:\poppler\Library\bin` to your PATH (same way as above).
4. Verify: open a new Command Prompt and run `pdfinfo --version`.

### 4 – Download this project

```bat
git clone https://github.com/r987r/Ocr-parse.git
cd Ocr-parse
```

Or download the ZIP from GitHub and extract it.

### 5 – Create a virtual environment and install dependencies

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 6 – Run the app

```bat
python app.py
```

Open your browser and go to **http://localhost:5000**.

---

## OCR Engine Options

| Engine | Cost | Best for | Setup |
|---|---|---|---|
| **Tesseract** | ✅ Free | Printed/typed text; basic handwriting | Install Tesseract (Step 2 above) |
| **OpenAI GPT-4o Vision** | 💳 Paid (~$0.01–0.05/page) | Excellent handwriting, context-aware | Get API key at [platform.openai.com](https://platform.openai.com/api-keys); `pip install openai` |
| **Google Cloud Vision** | 💳 Paid (free tier: 1,000 units/month) | High-accuracy handwriting detection | Create service-account JSON at [Google Cloud Console](https://console.cloud.google.com); `pip install google-cloud-vision` |

To use a paid engine, select it on the upload page and enter your API key (or file path for Google credentials).

---

## How to Use

1. **Upload page** – Select your annotated PDF and optionally your original document. Choose an OCR engine and click **Upload & Process**.

2. **Review page** – The app converts each PDF page to an image and runs OCR.
   - **Left panel**: Browse PDF pages with ← → navigation. Below each page image, the extracted text blocks are listed with confidence scores.
   - Click **➕ Add** next to any text block to add it as an annotation.
   - Click **+ Add Manually** to type an annotation by hand.

3. **Edit annotations** – In the right panel, each annotation shows:
   - A text editor (edit the OCR'd text if needed)
   - A type selector:
     - **💬 Comment** → appears as a Word comment balloon in the margin
     - **➕ Insert** → appears as a tracked change insertion (new text, shown in colour)
     - **🗑 Delete** → appears as a tracked change deletion (strikethrough text)

4. **Export** – When you're happy, click **💾 Make File (Export .docx)**. The browser downloads `reviewed_document.docx`.

5. **Open in Word** – Open the file in Microsoft Word. Go to the **Review** tab to see all comments and track changes. Accept or reject changes as needed.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `tesseract is not installed` | Follow Step 2 above and make sure Tesseract is in your PATH |
| `pdf2image` fails | Follow Step 3 above and make sure Poppler is in your PATH |
| Handwriting not recognised | Switch to OpenAI or Google Cloud Vision for better handwriting accuracy |
| `pip install` fails | Make sure your virtual environment is activated and you're using Python 3.10+ |
| Port 5000 already in use | Change the port in `app.py`: `app.run(port=5001)` |

---

## File Structure

```
Ocr-parse/
├── app.py              # Flask web application
├── ocr_engine.py       # OCR engine abstraction (Tesseract / OpenAI / Google)
├── word_export.py      # Word document export with comments & tracked changes
├── requirements.txt    # Python dependencies
├── templates/
│   ├── index.html      # Upload page
│   └── review.html     # Review & annotation page
├── static/
│   └── css/
│       └── style.css   # Stylesheet
└── uploads/            # Session files (created automatically, not committed)
```

---

## Privacy & Security

- All file processing happens **on your local machine**.
- Uploaded files are stored in the `uploads/` folder (auto-created, not committed to git).
- If you use OpenAI or Google Cloud Vision, page images are sent to their respective APIs. Review their privacy policies before uploading confidential documents.
- The `SECRET_KEY` in `app.py` is only for development. For any shared deployment, set `SECRET_KEY` as an environment variable.
- Debug mode is **off** by default. To enable it (for development only), set `FLASK_DEBUG=1` before running:
  ```bat
  set FLASK_DEBUG=1
  python app.py
  ```
