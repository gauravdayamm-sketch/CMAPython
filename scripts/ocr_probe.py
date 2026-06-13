"""Measure OCR quality on a scanned/native PDF before we trust it.

Tries the embedded text layer first (free, perfect when present); falls
back to rasterise+Tesseract page by page. Prints per-page char counts and
a sample so we can judge whether extraction is trustworthy.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

path = sys.argv[1]
max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 6

from pypdf import PdfReader
reader = PdfReader(path)
print(f"FILE: {pathlib.Path(path).name}  ({len(reader.pages)} pages)")

embedded = "".join((p.extract_text() or "") for p in reader.pages[:max_pages])
print(f"\nEmbedded text layer: {len(embedded)} chars over first {max_pages} pages")
if len(embedded) > 400:
    print("→ native PDF, no OCR needed. Sample:")
    print(" ".join(embedded.split())[:800])
    sys.exit(0)

print("→ little/no embedded text — running OCR (rasterise @300dpi)...\n")
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument(path)
for i in range(min(max_pages, len(pdf))):
    bitmap = pdf[i].render(scale=300 / 72)
    img = bitmap.to_pil()
    text = pytesseract.image_to_string(img)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    confs = [int(c) for c in data["conf"] if c not in ("-1", -1)]
    avg = sum(confs) / len(confs) if confs else 0
    digits = sum(ch.isdigit() for ch in text)
    print(f"── page {i+1}: {len(text)} chars, avg confidence {avg:.0f}%, "
          f"{digits} digits")
    print("   " + " ".join(text.split())[:280])
    print()
