"""
Adverse-media intelligence — Google News RSS per borrower, classified
locally (Ollama, falling back to a keyword screen), cached in SQLite,
and consumed by the EWS engine (indicator #24: negative news).

Nothing leaves the machine except the RSS fetch itself; headline
classification runs on the local model.
"""
import datetime
import pathlib
import re
import sqlite3
import urllib.parse

import feedparser

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB   = ROOT / "db" / "cma.sqlite"

NEWS_RSS = ("https://news.google.com/rss/search?"
            "q={query}&hl=en-IN&gl=IN&ceid=IN:en")

CLASSIFIER_MODEL = "qwen2.5:7b"     # local Ollama model for headline triage

# Deterministic fallback screen — these terms in a headline about the
# borrower are adverse with high confidence.
ADVERSE_KEYWORDS = (
    "fraud", "default", "npa", "insolvency", "nclt", "ibc", "liquidation",
    "bankrupt", "raid", "probe", "investigation", "arrest", "scam",
    "seized", "attach", "ed ", "cbi", "sebi penalty", "fine", "penalty",
    "wilful defaulter", "cheating", "fir ", "money laundering", "summons",
    "show cause", "blacklist", "downgrade", "stressed", "one-time settlement",
    "tax survey", "income tax survey", "i-t survey", "tax search",
    "sarfaesi", "drt ", "loan recall", "auction of assets", "possession",
    "garnishee", "lc devolve", "cheque bounce", "strike off",
)

CLASSIFY_PROMPT = """You classify news headlines about a bank borrower for credit risk at an Indian bank.
For the headline given, output ONLY JSON: {"classification": "ADVERSE" or "NEUTRAL" or "POSITIVE", "category": "one of fraud/insolvency/regulatory/financial-stress/litigation/operational/market/other"}
ADVERSE = anything a prudent lender must know: fraud, default, NPA, insolvency/NCLT, ED/CBI/Income-Tax raids SEARCHES OR SURVEYS, SEBI/regulatory action or penalties, litigation, losses or shrinking margins, rating downgrades, plant closures, strikes, key-customer loss, promoter disputes.
A tax-department survey or search at the borrower IS adverse. Margin compression or quarterly losses ARE adverse (financial-stress).
Output pure JSON, nothing else."""


LEGAL_SUFFIXES = re.compile(
    r"\b(private|pvt\.?|public|limited|ltd\.?|llp|india|industries)\b"
    r"|[()\.]", re.IGNORECASE)


def _simplify_name(name):
    """'ARROWIN METALTECH (INDIA) PRIVATE LIMITED' -> 'Arrowin Metaltech'."""
    core = LEGAL_SUFFIXES.sub(" ", name)
    return " ".join(core.split()).title()


def fetch_news(borrower_name, days=365, max_items=25):
    """Headlines mentioning the borrower from Google News RSS.
    Tries the exact legal name first, then the distinctive core of the
    name ('Pvt Ltd' etc. stripped) — news stories rarely use full styles."""
    feed = None
    for candidate in dict.fromkeys(
            [borrower_name, _simplify_name(borrower_name)]):
        if not candidate.strip():
            continue
        query = urllib.parse.quote(f'"{candidate}"')
        try:
            feed = feedparser.parse(NEWS_RSS.format(query=query))
        except Exception as e:
            return [], str(e)
        if feed.entries:
            break
    if feed is None:
        return [], "no usable search name"

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    items = []
    for entry in feed.entries[:max_items]:
        if getattr(entry, "published_parsed", None):
            pub = datetime.datetime(*entry.published_parsed[:6])
            if pub < cutoff:
                continue
            published = pub.isoformat()
        else:
            published = ""
        items.append({
            "title":     entry.get("title", ""),
            "link":      entry.get("link", ""),
            "source":    (entry.get("source") or {}).get("title", "")
                         if isinstance(entry.get("source"), dict) else "",
            "published": published,
        })
    return items, None


def _classify_keywords(title):
    low = " " + title.lower() + " "
    if any(kw in low for kw in ADVERSE_KEYWORDS):
        return {"classification": "ADVERSE", "category": "keyword-screen"}
    return {"classification": "NEUTRAL", "category": "other"}


def classify_headline(title, use_llm=True):
    """Classify one headline. LLM first, keyword screen as fallback/floor:
    a keyword hit is ADVERSE regardless of what the model says."""
    keyword_view = _classify_keywords(title)
    if not use_llm:
        return keyword_view | {"classified_by": "keywords"}
    try:
        import json
        from ollama import Client
        client = Client(host="http://localhost:11434")
        resp = client.chat(
            model=CLASSIFIER_MODEL,
            messages=[{"role": "system", "content": CLASSIFY_PROMPT},
                      {"role": "user", "content": title}],
            options={"temperature": 0.0},
            format="json",
        )
        parsed = json.loads(resp["message"]["content"])
        out = {
            "classification": str(parsed.get("classification", "NEUTRAL")).upper(),
            "category": str(parsed.get("category", "other")),
            "classified_by": CLASSIFIER_MODEL,
        }
        if out["classification"] not in ("ADVERSE", "NEUTRAL", "POSITIVE"):
            out["classification"] = "NEUTRAL"
        # Keyword screen is the floor — a hard adverse term wins
        if keyword_view["classification"] == "ADVERSE":
            out["classification"] = "ADVERSE"
        return out
    except Exception:
        return keyword_view | {"classified_by": "keywords"}


def refresh_borrower_news(borrower_name, db_path=None, days=365, use_llm=True):
    """Fetch + classify + cache news for one borrower.
    Returns {"fetched": n, "new": n, "adverse": n, "error": ...}."""
    db = db_path or DB
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT borrower_id FROM borrower WHERE name = ?",
            (borrower_name,)).fetchone()
        if not row:
            return {"fetched": 0, "new": 0, "adverse": 0,
                    "error": f"Borrower not found: {borrower_name}"}
        bid = row[0]

    items, err = fetch_news(borrower_name, days=days)
    if err:
        return {"fetched": 0, "new": 0, "adverse": 0, "error": err}

    new = adverse = 0
    with sqlite3.connect(db) as conn:
        for item in items:
            known = conn.execute(
                "SELECT 1 FROM news_cache WHERE borrower_id=? AND link=?",
                (bid, item["link"])).fetchone()
            if known:
                continue
            verdict = classify_headline(item["title"], use_llm=use_llm)
            conn.execute("""
                INSERT OR IGNORE INTO news_cache
                (borrower_id, link, title, source, published,
                 classification, category, classified_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (bid, item["link"], item["title"], item["source"],
                  item["published"], verdict["classification"],
                  verdict["category"], verdict["classified_by"]))
            new += 1
            if verdict["classification"] == "ADVERSE":
                adverse += 1

    total_adverse = count_adverse(bid, db_path=db)
    return {"fetched": len(items), "new": new, "adverse": adverse,
            "total_adverse": total_adverse, "error": ""}


def count_adverse(borrower_id, db_path=None, days=365):
    """Adverse headlines on record within the window (used by EWS #24)."""
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path or DB) as conn:
        return conn.execute("""
            SELECT COUNT(*) FROM news_cache
            WHERE borrower_id = ? AND classification = 'ADVERSE'
              AND (published >= ? OR published = '')
        """, (borrower_id, cutoff)).fetchone()[0]


def get_news(borrower_id, db_path=None, limit=20):
    with sqlite3.connect(db_path or DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM news_cache WHERE borrower_id = ?
            ORDER BY classification = 'ADVERSE' DESC, published DESC
            LIMIT ?
        """, (borrower_id, limit)).fetchall()
    return [dict(r) for r in rows]


def sweep_all_borrowers(db_path=None, use_llm=True):
    """Daily job: refresh news for every borrower with CMA data."""
    db = db_path or DB
    with sqlite3.connect(db) as conn:
        names = [r[0] for r in conn.execute("""
            SELECT DISTINCT b.name FROM borrower b
            JOIN cma_statement s ON s.borrower_id = b.borrower_id
        """)]
    results = {}
    for name in names:
        results[name] = refresh_borrower_news(name, db_path=db, use_llm=use_llm)
    return results
