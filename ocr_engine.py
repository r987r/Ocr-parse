"""
OCR Engine abstraction supporting multiple backends.

Supported engines:
  - tesseract  : Free, local (requires Tesseract installed)
  - openai     : OpenAI GPT-4o Vision (paid, excellent for handwriting)
  - google     : Google Cloud Vision API (paid, excellent for handwriting)
"""

import io
import base64


class OCREngine:
    """OCR Engine abstraction with multiple backend support."""

    def __init__(self, engine="tesseract", api_key=None):
        self.engine = engine
        self.api_key = api_key

    def extract_text_blocks(self, image):
        """Extract text blocks from a PIL Image.

        Returns a list of dicts:
          { 'text': str, 'confidence': int (0-100), 'block_num': int }
        """
        if self.engine == "tesseract":
            return self._tesseract_extract(image)
        elif self.engine == "openai":
            return self._openai_extract(image)
        elif self.engine == "google":
            return self._google_extract(image)
        else:
            raise ValueError(f"Unknown OCR engine: {self.engine}")

    # ------------------------------------------------------------------
    # Tesseract (free, local)
    # ------------------------------------------------------------------
    def _tesseract_extract(self, image):
        try:
            import pytesseract
        except ImportError:
            raise ImportError(
                "pytesseract not installed. Run: pip install pytesseract"
            )

        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            config="--psm 1",
        )

        img_w, img_h = image.size

        blocks = {}
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            if not text:
                continue
            conf = int(data["conf"][i])
            if conf < 0:
                conf = 0
            block_num = data["block_num"][i]

            left = data["left"][i]
            top = data["top"][i]
            width = data["width"][i]
            height = data["height"][i]

            if block_num not in blocks:
                blocks[block_num] = {
                    "text": text,
                    "confidence": conf,
                    "block_num": block_num,
                    "_left": left,
                    "_top": top,
                    "_right": left + width,
                    "_bottom": top + height,
                }
            else:
                blocks[block_num]["text"] += " " + text
                if conf > 0:
                    blocks[block_num]["confidence"] = (
                        blocks[block_num]["confidence"] + conf
                    ) // 2
                blocks[block_num]["_left"] = min(blocks[block_num]["_left"], left)
                blocks[block_num]["_top"] = min(blocks[block_num]["_top"], top)
                blocks[block_num]["_right"] = max(blocks[block_num]["_right"], left + width)
                blocks[block_num]["_bottom"] = max(blocks[block_num]["_bottom"], top + height)

        result = []
        for b in blocks.values():
            if not b["text"].strip():
                continue
            bbox = {
                "x": round(b["_left"] / img_w * 100, 2) if img_w else 0,
                "y": round(b["_top"] / img_h * 100, 2) if img_h else 0,
                "w": round((b["_right"] - b["_left"]) / img_w * 100, 2) if img_w else 0,
                "h": round((b["_right"] - b["_left"]) / img_h * 100, 2) if img_h else 0,
            }
            result.append({
                "text": b["text"],
                "confidence": b["confidence"],
                "block_num": b["block_num"],
                "bbox": bbox,
            })
        return result

    # ------------------------------------------------------------------
    # OpenAI GPT-4o Vision (paid)
    # ------------------------------------------------------------------
    def _openai_extract(self, image):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

        if not self.api_key:
            raise ValueError("OpenAI API key is required for the 'openai' engine.")

        client = OpenAI(api_key=self.api_key)

        img_b64 = self._image_to_base64(image)

        prompt = (
            "You are analyzing a document page that may contain printed text AND "
            "handwritten annotations, margin notes, or inline corrections.\n\n"
            "Please extract ALL text visible on the page, grouping by text region.\n"
            "Return a JSON array where each element has:\n"
            "  - \"text\": the extracted text\n"
            "  - \"source_type\": \"printed\" or \"handwritten\"\n"
            "  - \"location\": approximate location such as "
            "\"main\", \"margin-left\", \"margin-right\", \"top\", \"bottom\", or \"inline\"\n\n"
            "Return ONLY valid JSON, no markdown fences or other text."
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            max_tokens=4096,
        )

        import json
        import re

        raw = response.choices[0].message.content or ""
        # Strip markdown code fences if present
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: wrap entire response as one block
            parsed = [{"text": raw, "source_type": "handwritten", "location": "unknown"}]

        return [
            {
                "text": b.get("text", "").strip(),
                "confidence": 90,
                "block_num": idx,
                "source_type": b.get("source_type", "unknown"),
                "location": b.get("location", "unknown"),
            }
            for idx, b in enumerate(parsed)
            if b.get("text", "").strip()
        ]

    # ------------------------------------------------------------------
    # Google Cloud Vision (paid)
    # ------------------------------------------------------------------
    def _google_extract(self, image):
        try:
            from google.cloud import vision as gv
        except ImportError:
            raise ImportError(
                "google-cloud-vision not installed. "
                "Run: pip install google-cloud-vision"
            )

        if not self.api_key:
            raise ValueError(
                "Path to Google credentials JSON is required for the 'google' engine. "
                "Enter the full path to your service-account key file."
            )

        import os

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.api_key

        client = gv.ImageAnnotatorClient()
        img_bytes = self._image_to_bytes(image)
        vi = gv.Image(content=img_bytes)

        response = client.document_text_detection(image=vi)

        blocks = []
        block_num = 0
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                lines = []
                confidence_sum = 0
                word_count = 0
                for para in block.paragraphs:
                    words = []
                    for word in para.words:
                        word_text = "".join(s.text for s in word.symbols)
                        words.append(word_text)
                        confidence_sum += word.confidence
                        word_count += 1
                    lines.append(" ".join(words))
                block_text = "\n".join(lines).strip()
                if block_text:
                    avg_conf = int((confidence_sum / word_count * 100) if word_count else 0)
                    blocks.append(
                        {
                            "text": block_text,
                            "confidence": avg_conf,
                            "block_num": block_num,
                        }
                    )
                    block_num += 1

        return blocks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _image_to_base64(image):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    @staticmethod
    def _image_to_bytes(image):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
