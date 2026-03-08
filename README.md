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

## Persistent Storage (Recommended for Render)

Render's free tier uses **ephemeral containers** — uploaded files are wiped when
the service restarts or redeploys. To keep sessions alive across restarts, connect
an S3-compatible object store. **No sign-up is required for the app itself**; you
only need a free account with one of the storage providers below.

### Option A – Backblaze B2 (free 10 GB, recommended)

1. Sign up at [backblaze.com](https://www.backblaze.com/cloud-storage) (free, no credit card required for the free tier)
2. Go to **Buckets** → **Create a Bucket**
   - Bucket name: e.g. `ocr-parse-sessions`
   - Files in bucket: **Private**
3. Go to **Application Keys** → **Add a New Application Key**
   - Name: e.g. `ocr-parse`
   - Bucket: select the bucket you just created
   - Capabilities: **Read and Write**
   - Click **Create New Key** and copy the **keyID** and **applicationKey**
4. Note your bucket's **Endpoint** (shown on the Bucket page), e.g. `https://s3.us-west-004.backblazeb2.com`
5. Add these environment variables to your Render service (**Dashboard → Environment**):

   | Variable | Value |
   |---|---|
   | `S3_BUCKET` | your bucket name, e.g. `ocr-parse-sessions` |
   | `S3_ENDPOINT_URL` | your endpoint, e.g. `https://s3.us-west-004.backblazeb2.com` |
   | `AWS_ACCESS_KEY_ID` | the **keyID** from step 3 |
   | `AWS_SECRET_ACCESS_KEY` | the **applicationKey** from step 3 |
   | `AWS_DEFAULT_REGION` | `us-west-004` (the region part of your endpoint) |

6. Redeploy the service — sessions will now survive restarts.

### Option B – Cloudflare R2 (free 10 GB/month)

1. Sign up at [cloudflare.com](https://cloudflare.com) (free tier, requires a credit card for verification but is not charged)
2. Go to **R2 Object Storage** → **Create bucket**
   - Name: e.g. `ocr-parse-sessions`
3. Go to **Manage R2 API Tokens** → **Create API Token**
   - Permissions: **Object Read & Write** on the bucket you created
   - Copy the **Access Key ID** and **Secret Access Key**
4. Note your **Account ID** (shown on the R2 overview page)
5. Add these environment variables to Render:

   | Variable | Value |
   |---|---|
   | `S3_BUCKET` | your bucket name |
   | `S3_ENDPOINT_URL` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
   | `AWS_ACCESS_KEY_ID` | Access Key ID from step 3 |
   | `AWS_SECRET_ACCESS_KEY` | Secret Access Key from step 3 |
   | `AWS_DEFAULT_REGION` | `auto` |

### Option C – AWS S3

1. Sign in to [aws.amazon.com](https://aws.amazon.com) (requires a credit card; has a 12-month free tier for new accounts)
2. Go to **S3** → **Create bucket**
   - Note the bucket name and region
3. Go to **IAM** → **Users** → **Create user** → attach the **AmazonS3FullAccess** policy (or a scoped policy for the bucket)
4. Create an **Access Key** for the user and copy the ID and secret
5. Add these environment variables to Render:

   | Variable | Value |
   |---|---|
   | `S3_BUCKET` | your bucket name |
   | `AWS_ACCESS_KEY_ID` | IAM access key ID |
   | `AWS_SECRET_ACCESS_KEY` | IAM secret access key |
   | `AWS_DEFAULT_REGION` | your bucket's region, e.g. `us-east-1` |
   | `S3_ENDPOINT_URL` | *(leave unset — uses standard AWS endpoints)* |

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
- When S3 storage is enabled, session files are stored in your private bucket under your own account – the app never has access to other users' buckets
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
├── storage.py          # S3-compatible cloud storage backend (optional)
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
├── storage.py          # S3-compatible cloud storage backend (optional)
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
