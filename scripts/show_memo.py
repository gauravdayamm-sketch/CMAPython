"""Print a memo .docx in document order (paragraphs + tables interleaved)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

doc = Document(sys.argv[1])
skip_glossary = "--full" not in sys.argv

for el in doc.element.body:
    if el.tag.endswith("}p"):
        p = Paragraph(el, doc)
        t = p.text.strip()
        style = p.style.name
        if not t:
            continue
        if skip_glossary and t.startswith("Annexure A"):
            print("\n[Annexure A — 20-term glossary omitted here]")
            break
        if style == "Title":
            print(f"\n{'═'*70}\n{t}\n{'═'*70}")
        elif style.startswith("Heading"):
            print(f"\n──── {t} " + "─" * max(0, 60 - len(t)))
        elif style == "Intense Quote":
            print(f"   » {t}")
        else:
            print(t)
    elif el.tag.endswith("}tbl"):
        tb = Table(el, doc)
        widths = None
        for r in tb.rows:
            cells = [c.text.strip().replace("\n", " ")[:38] for c in r.cells]
            if widths is None:
                widths = [min(max(len(c), 8), 38) + 2 for c in cells]
            print("  " + "".join(c.ljust(w) for c, w in zip(cells, widths)))
