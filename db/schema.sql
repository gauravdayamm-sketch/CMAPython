-- CMA Analytics — SQLite schema
-- Apply with: python scripts/init_db.py  (idempotent)

CREATE TABLE IF NOT EXISTS borrower (
    borrower_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    cin           TEXT,
    industry      TEXT,
    constitution  TEXT,
    rbi_sector    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS financials (
    fin_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id         INTEGER NOT NULL REFERENCES borrower(borrower_id),
    fy_end              DATE    NOT NULL,
    audited             INTEGER NOT NULL DEFAULT 1,
    total_assets        REAL,
    total_liabilities   REAL,
    current_assets      REAL,
    current_liabilities REAL,
    working_capital     REAL,
    net_income          REAL,
    ebit                REAL,
    ebitda              REAL,
    ffo                 REAL,
    depreciation        REAL,
    sga                 REAL,
    cogs                REAL,
    sales               REAL,
    receivables         REAL,
    ppe                 REAL,
    securities          REAL,
    long_term_debt      REAL,
    tnw                 REAL,
    tol                 REAL,
    dscr                REAL,
    interest_cost       REAL,
    UNIQUE(borrower_id, fy_end)
);

CREATE TABLE IF NOT EXISTS cashflow_weekly (
    cf_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id     INTEGER NOT NULL REFERENCES borrower(borrower_id),
    week_end        DATE    NOT NULL,
    inflow          REAL,
    outflow         REAL,
    net             REAL,
    opening_balance REAL,
    closing_balance REAL,
    UNIQUE(borrower_id, week_end)
);

CREATE TABLE IF NOT EXISTS forecast_run (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id     INTEGER NOT NULL REFERENCES borrower(borrower_id),
    run_ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model           TEXT,
    horizon_wks     INTEGER,
    rmse_backtest   REAL
);

CREATE TABLE IF NOT EXISTS forecast_point (
    run_id      INTEGER NOT NULL REFERENCES forecast_run(run_id),
    week_end    DATE,
    point       REAL,
    lo_80       REAL,
    hi_80       REAL,
    lo_95       REAL,
    hi_95       REAL,
    PRIMARY KEY(run_id, week_end)
);

CREATE TABLE IF NOT EXISTS anomaly_result (
    anom_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id INTEGER NOT NULL REFERENCES borrower(borrower_id),
    detector    TEXT NOT NULL,
    run_ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    score       REAL,
    threshold   REAL,
    is_flagged  INTEGER,
    notes       TEXT
);

-- One row per borrower+detector. The analytics modules write with
-- INSERT OR REPLACE, which needs this index to replace rather than append.
CREATE UNIQUE INDEX IF NOT EXISTS idx_anomaly_borrower_detector
    ON anomaly_result(borrower_id, detector);

CREATE TABLE IF NOT EXISTS narrative (
    narr_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id     INTEGER NOT NULL REFERENCES borrower(borrower_id),
    run_ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent           TEXT NOT NULL,
    model           TEXT,
    prompt_hash     TEXT,
    output_text     TEXT,
    parent_narr_id  INTEGER REFERENCES narrative(narr_id)
);

CREATE TABLE IF NOT EXISTS macro_indicators (
    indicator_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_name  TEXT NOT NULL,
    value_date      DATE NOT NULL,
    value           REAL,
    source          TEXT,
    UNIQUE(indicator_name, value_date)
);

CREATE TABLE IF NOT EXISTS rbi_cache (
    link        TEXT PRIMARY KEY,
    title       TEXT,
    published   TEXT,
    summary     TEXT,
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── CMA workbook ingestion ───────────────────────────────────────────────────
-- One row per year-column of the CMA workbook (SBI LLMS layout).
-- The prior FY appears twice when the dual column is active: once as the
-- borrower's earlier estimate (is_dual=1) and once as audited actuals —
-- their variance measures projection credibility.

CREATE TABLE IF NOT EXISTS cma_statement (
    stmt_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id    INTEGER NOT NULL REFERENCES borrower(borrower_id),
    fy_label       TEXT NOT NULL,             -- '2024-25'
    fy_end         DATE NOT NULL,             -- '2025-03-31'
    statement_type TEXT NOT NULL CHECK (statement_type IN
                        ('audited','provisional','estimated','projected')),
    is_dual        INTEGER NOT NULL DEFAULT 0,
    col_index      INTEGER,                   -- 1-based workbook column position
    source_file    TEXT,
    imported_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(borrower_id, fy_end, statement_type)
);

-- Long-format line items: raw inputs AND Python-derived subtotals.
CREATE TABLE IF NOT EXISTS cma_line (
    stmt_id   INTEGER NOT NULL REFERENCES cma_statement(stmt_id) ON DELETE CASCADE,
    line_code TEXT NOT NULL,                  -- e.g. 'os_net_sales', 'bs_tca'
    value     REAL,
    PRIMARY KEY (stmt_id, line_code)
);

-- Proposal limits and thresholds captured from 0_Setup at ingest time,
-- so assessments are reproducible against the inputs they used.
CREATE TABLE IF NOT EXISTS cma_proposal (
    borrower_id INTEGER NOT NULL REFERENCES borrower(borrower_id),
    facility    TEXT NOT NULL,                -- 'fb_wc' | 'fb_tl' | 'nfb'
    existing    REAL,
    proposed    REAL,
    PRIMARY KEY (borrower_id, facility)
);

CREATE TABLE IF NOT EXISTS cma_threshold (
    borrower_id INTEGER NOT NULL REFERENCES borrower(borrower_id),
    key         TEXT NOT NULL,                -- 'min_current_ratio', ...
    value       REAL,
    PRIMARY KEY (borrower_id, key)
);

-- Free-form setup metadata (project-finance overlay, data-source mode, ...)
CREATE TABLE IF NOT EXISTS cma_setup_meta (
    borrower_id INTEGER NOT NULL REFERENCES borrower(borrower_id),
    key         TEXT NOT NULL,                -- 'project_finance', 'project_phase', ...
    value       TEXT,
    PRIMARY KEY (borrower_id, key)
);

-- ── Market & registry intelligence ───────────────────────────────────────────

-- Adverse-media monitoring: news headlines per borrower, classified locally.
CREATE TABLE IF NOT EXISTS news_cache (
    borrower_id    INTEGER NOT NULL REFERENCES borrower(borrower_id),
    link           TEXT NOT NULL,
    title          TEXT,
    source         TEXT,
    published      TEXT,
    classification TEXT,            -- ADVERSE | NEUTRAL | POSITIVE
    category       TEXT,            -- fraud / insolvency / regulatory / ...
    classified_by  TEXT,            -- model name or 'keywords'
    fetched_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (borrower_id, link)
);

-- Public-registry due diligence: one row per borrower per registry.
-- Most Indian portals are captcha-walled, so checks are recorded manually
-- (or by future API connectors); staleness drives committee queries.
CREATE TABLE IF NOT EXISTS registry_check (
    borrower_id INTEGER NOT NULL REFERENCES borrower(borrower_id),
    registry    TEXT NOT NULL,      -- 'mca' | 'gst' | 'ibbi' | 'epfo' | 'ecourts'
    checked_on  DATE NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('clear', 'adverse', 'pending')),
    remarks     TEXT,
    reference   TEXT,               -- case no. / GSTIN / URL consulted
    PRIMARY KEY (borrower_id, registry)
);
