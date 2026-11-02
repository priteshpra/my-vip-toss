# Toss Analytics & Tracking Admin Panel

A production-grade Python cricket toss analytics, verification, and tipster tracking admin panel engineered with strict scientific honesty principles, pre-match cryptographic locking, and empirical baseline validation.

---

## ⚖️ Scientific Honesty Rules & Principles (Non-Negotiable)

A cricket toss is an **independent, fair coin flip with theoretical probability \(p = 0.50\)**. This platform strictly enforces non-negotiable scientific honesty rules:
1. **No False Marketing Claims**: Words like `"guaranteed"`, `"99.99%"`, `"sure-shot"`, or `"sure shot"` are strictly forbidden across code, templates, UI components, background logs, and documentation.
2. **Real Measured Accuracy**: Every source, tipster channel, and algorithm displays real empirical accuracy with sample size \(n\), a **95% Wilson Score Confidence Interval**, and an **exact two-sided binomial test** against the 50% fair-coin baseline.
3. **Cryptographic Immutability**: Predictions and calls are locked with a UTC timestamp and **SHA-256 cryptographic seal** *strictly BEFORE* scheduled match start. The ledger is **append-only** (no updates, no deletions, no post-toss modifications).
4. **Permanent Disclaimer**: Displayed on every view and generated export:
   > *"Toss is a random event. Past stats do not guarantee results. Play responsibly."*
5. **Zero Hardcoded Secrets**: All sensitive tokens, credentials, and session keys are loaded via `.env` (managed with `pydantic-settings`). `.env.example` provides placeholders only. Secrets are automatically scrubbed from application logs.
6. **Timezone Discipline**: All database records store timestamps in **UTC**; displayed across the web interface in **IST (Asia/Kolkata)**.

---

## 🛠️ Tech Stack

- **Python 3.11+ / 3.14**
- **FastAPI**: Modern, high-performance asynchronous web framework
- **Jinja2 + HTMX + Tailwind CSS**: Responsive, server-rendered dynamic UI without heavy JS framework overhead
- **SQLAlchemy 2.0 (Async + Sync) & Alembic**: Database ORM and schema migration system (SQLite default, PostgreSQL-ready via `DATABASE_URL`)
- **APScheduler**: Automated background job scheduler with resilient intervals
- **Telethon**: Asynchronous Telegram client for live tipster tracking and audit verification
- **httpx**: Resilient asynchronous HTTP client with exponential backoff and timeout protection
- **RapidFuzz**: High-performance fuzzy string matching for cross-source entity normalization
- **SciPy / NumPy / Pandas**: Mathematical statistical functions (Wilson score interval, exact binomial test, Brier score)
- **Pytest & Pytest-AsyncIO**: Comprehensive test suite (127 automated tests passing)
- **Loguru**: Structured application logging with automated sensitive credential scrubbing

---

## 📂 Project Architecture

```
my-vip-toss/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory, lifespan, and security middlewares
│   ├── config.py                # Pydantic BaseSettings with typed intervals and thresholds
│   ├── logger.py                # Structured logging with automated regex secret redaction
│   ├── db.py                    # Async/Sync engines, session makers, and UTC/IST datetime helpers
│   ├── models.py                # SQLAlchemy 2.0 declarative models & immutability guards
│   ├── schemas.py               # Pydantic v2 schemas for API, audit, and accuracy
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── security.py          # PBKDF2 password hashing & HMAC signed session cookies
│   │   ├── csrf.py              # Cryptographic CSRF token generation & validation
│   │   ├── rate_limiter.py      # In-memory sliding window login lockout limiter
│   │   └── dependencies.py      # Admin authentication dependencies
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── gaming_helper.py     # Resilient GamingHelper adapter with local fallback
│   │   ├── telegram_listener.py # Telethon tipster parser, reconnect loop & review queue
│   │   ├── cricsheet.py         # Cricsheet open data historical match parser & sync
│   │   ├── espn_cricbuzz.py     # Public fixture & live toss verification collector
│   │   └── external_sources.py  # Pluggable adapter pattern (HTML, JSON, Telegram, Manual)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── accuracy_service.py  # Wilson 95% CI, binomial null tests vs 50%, Brier calibration
│   │   ├── match_service.py     # Match queries, filtering, and detail hydration
│   │   ├── toss_service.py      # Official ground truth recording & auto-evaluation
│   │   ├── prediction_service.py# Pre-toss cryptographic lock & append-only validation
│   │   ├── analysis_service.py  # Longitudinal team toss stats & venue trends
│   │   ├── normalizer.py        # RapidFuzz entity normalizer with gender guarding
│   │   ├── backup_service.py    # Online SQLite hot backup with retention rotation
│   │   └── audit_service.py     # Admin audit logging and action history
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── scheduler.py         # APScheduler runner for production background tasks
│   │   └── tasks.py             # Match sync, toss verification, source poll & hot backups
│   └── web/
│       ├── __init__.py
│       ├── routes.py            # Dashboard, Match Centre, Ledger, Accuracy & Admin routes
│       ├── static/              # CSS & JS assets
│       └── templates/           # Tailwind + HTMX Jinja2 templates & partials
├── alembic/
│   ├── alembic.ini              # Alembic migration configuration
│   ├── env.py                   # Migration environment connected to Base.metadata
│   └── versions/                # Generated schema migration versions
├── data/                        # Historical and fallback cache datasets
├── tests/                       # Complete Pytest test suite (127 tests passing)
├── .env.example                 # Environment variables template with placeholders
├── run.py                       # Unified operations CLI entrypoint
├── Procfile                     # Process manager file for production deployments
├── docker-compose.yml           # Multi-service container orchestration
├── Dockerfile                   # Production container definition
├── requirements.txt             # Project dependencies
└── README.md                    # Project documentation
```

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.11 or higher (Python 3.14 fully supported)
- Git

### 2. Clone & Configure Environment
```bash
cp .env.example .env
```

Edit `.env` to configure your admin credentials and operational intervals:
```ini
ENVIRONMENT=development
LOG_LEVEL=INFO

# Database
DATABASE_URL=sqlite+aiosqlite:///./toss_analytics.db
SYNC_DATABASE_URL=sqlite:///./toss_analytics.db
APP_TIMEZONE=Asia/Kolkata

# Admin Auth & Security
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_this_secure_admin_password
SECRET_KEY=change_this_to_a_random_secret_key_at_least_32_chars
SESSION_EXPIRE_MINUTES=120
SESSION_COOKIE_SECURE=false

# GamingHelper Online Ingestion
GAMING_HELPER_BASE_URL=https://gaminglive.gaminghelperonline.com/api
GAMING_HELPER_LOGIN_URL=https://gaminglive.gaminghelperonline.com/api/users/login
GAMING_HELPER_USERNAME=your_gh_username
GAMING_HELPER_PASSWORD=your_gh_password
GAMING_HELPER_POLL_INTERVAL=90

# Telegram Tipster Listener
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHANNEL_URL=@your_channel_or_invite

# Operational Intervals
FIXTURES_POLL_INTERVAL=120
SOURCES_POLL_INTERVAL=300
HEALTH_CHECK_INTERVAL=300
CRICSHEET_SYNC_INTERVAL_HOURS=24
BACKUP_DIR=_backups
BACKUP_KEEP_COUNT=7
BACKUP_INTERVAL_HOURS=24

# Statistical Thresholds
MIN_SAMPLE=30
CONFIDENCE=0.95
SIGNIFICANCE_ALPHA=0.05
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run Database Migrations
```bash
python -m alembic upgrade head
```

### 5. Create or Reset the Admin User
To securely set your admin password interactively without logging it to shell history:
```bash
python run.py --create-admin
```

---

## 🖥️ Running the Application

The unified `run.py` CLI provides a single operational entrypoint for all services:

### Start the Web Application
```bash
python run.py --web --port 8000
```
Open your browser at `http://127.0.0.1:8000`. Default admin login: `admin` / your configured password.

### Start the Background Scheduler
Runs all 7 scheduled maintenance tasks (GamingHelper poll, public fixtures verification, external source poll, health check, daily Cricsheet sync, database hot backup, and cryptographic hash audit):
```bash
python run.py --scheduler
```

### Start the Telegram Tipster Listener
Connects to configured Telegram channels using Telethon to parse pre-toss calls and route ambiguous messages to the Review Queue:
```bash
python run.py --telegram
```

### Run a Hot Database Backup
Performs an instant online SQLite hot backup (`sqlite3.backup` API) and rotates old archives keeping the last \(N\) backups:
```bash
python run.py --backup
```

### Run an Incremental Cricsheet Historical Sync
```bash
python run.py --sync
```

### Run All Services Concurrently
Runs the Web UI and Background Scheduler in a single process:
```bash
python run.py --all
```

### Containerized Deployment (Docker Compose)
To launch the complete application with Docker:
```bash
docker compose up --build -d
```

---

## 📊 Core Features & Views

| Route | Feature Description |
|---|---|
| `/` | **Dashboard**: Live match overview, confirmed toss results, streak tracking, and audited source accuracy. |
| `/matches` | **Match Centre**: Filterable upcoming, live, and completed matches with league and format selection. |
| `/matches/{id}` | **Match Detail**: Comprehensive analysis report, Head-to-Head toss records, venue tendencies, locked pre-toss calls, and CSV/PDF export. |
| `/predictions` | **Prediction Ledger**: Cryptographically locked append-only ledger with real-time SHA-256 seal verification. |
| `/toss-history` | **Toss History**: Searchable archive of all historical tosses with filters and CSV export. |
| `/accuracy` | **Accuracy / Truth Dashboard**: Measured hit-rate with 95% Wilson CI, Brier score calibration table, rolling hit-rate charts, and 50% baseline test banner. |
| `/leagues-teams` | **Leagues & Teams**: Registry of recognized tournaments (IPL, BBL, WBBL, NPL, ICC, etc.) and alias mappings. |
| `/sources` | **Sources Leaderboard**: Tracked tipster feeds and algorithms ranked by measured accuracy (gated by \(n \ge 30\)). Add/edit sources dynamically. |
| `/telegram-reviews` | **Telegram Needs Review**: Queue of unparsable or ambiguous Telegram messages for manual admin mapping. |
| `/collector-health` | **Collector Health**: Real-time heartbeat, error logs, and latency monitoring across all collectors. |
| `/settings` | **System Settings**: Configured poll intervals, thresholds, and operational parameters. |
| `/logs` | **Application Logs**: Live structured logs with automated credential redaction. |
| `/audit` | **Audit Log**: Immutable record of every administrative action (who, what, when, IP address). |
| `/auth/login` | **Admin Authentication**: Secure login protected by bcrypt, CSRF tokens, and lockout rate-limiting. |

---

## 🧩 How to Extend the Platform

### 1. How to Add a League
- **Via Database / Admin UI**: Navigate to `/leagues-teams` and click **"Add League"**. Specify the name, alias keywords, and format (`T20`, `ODI`, `TEST`, `T10`).
- **Via Entity Normalizer**: Add aliases in `app/services/normalizer.py`:
  ```python
  KNOWN_LEAGUE_ALIASES = {
      "ipl": "IPL",
      "indian premier league": "IPL",
      "bbl": "BBL",
      "big bash league": "BBL",
      "npl": "NPL",
      "nepal premier league": "NPL",
      "the hundred": "The Hundred",
  }
  ```
- Any fixture whose league cannot be recognized automatically defaults to **"Local/Unknown"** (never dropped).

### 2. How to Add a Collector
To create a custom collector for a new sports data provider:
1. Subclass `BaseSourceAdapter` in `app/collectors/external_sources.py`:
   ```python
   from app.collectors.external_sources import BaseSourceAdapter

   class CustomApiCollector(BaseSourceAdapter):
       async def fetch_snapshots(self) -> List[Dict[str, Any]]:
           # Implement your authenticated or public HTTP client here
           # Must respect rate limits, timeouts, and robots.txt
           return [...]
   ```
2. Register the adapter in the factory method `get_adapter_for_source(source_model)`.
3. Add a background task in `app/jobs/tasks.py` and register it in `app/jobs/scheduler.py`.

### 3. How to Add an External Source (No Code Required)
Admins can dynamically add and manage external tipsters, websites, and feeds directly from the web panel:
1. Navigate to `/sources` in the Admin Panel.
2. Click **"Add New Source"**.
3. Choose the source type:
   - `public_html`: Scrapes public tables/pages using CSS selectors or regex patterns.
   - `json_api`: Polls a REST JSON endpoint.
   - `telegram_channel`: Listens to a public or invite-only Telegram channel.
   - `manual`: For manual admin entry of market sentiment or volume odds.
4. Set the polling interval and enable the source. Snapshots are stored append-only and automatically scored PASS/FAIL once the official toss is declared.

---

## 📐 How to Read the Accuracy Dashboard

The Accuracy Dashboard is designed according to rigorous statistical methodology:

1. **Sample Size Gate (\(n \ge 30\))**:
   - Small sample sizes are statistically noisy. Any source or model with \(n < 30\) is explicitly labeled **"Insufficient Data (\(n < 30\))"** and disqualified from leaderboard ranking.
2. **95% Wilson Score Confidence Interval**:
   - Rather than displaying a misleading single percentage (e.g. "60%"), the panel displays the true 95% Wilson Confidence Interval: \([p_{\text{low}}, p_{\text{high}}]\).
   - If the interval spans \(0.50\) (e.g. \([0.44, 0.62]\)), a clear badge is displayed: **"Not Statistically Distinguishable from 50% Fair Baseline"**.
3. **Exact Two-Sided Binomial Test (\(p\)-value)**:
   - Evaluates the null hypothesis \(H_0: p = 0.50\). If \(p > 0.05\), there is no evidence of predictive power beyond chance.
4. **Brier Calibration Score**:
   - Measures probabilistic calibration:
     \[
     \text{Brier} = \frac{1}{N}\sum_{t=1}^N (f_t - o_t)^2
     \]
   - A score of \(0.25\) corresponds to random 50/50 guessing; lower scores indicate superior probabilistic calibration.
5. **Combined Panel Lean Default ("NO EDGE - skip")**:
   - Unless verified sources show statistically significant confluence beyond the 50% baseline, the panel recommendation defaults to **"NO EDGE - skip"**.

---

## 🧪 Testing & Verification

Run the complete test suite:
```bash
python -m pytest -v
```

The test suite consists of **127 automated tests** covering all phases:
- **Phase 1 & 2**: PBKDF2 hashing, session cookies, rate-limiting lockout, CSRF protection, secure headers, admin audit logs.
- **Phase 3**: GamingHelper ingestion, local fallbacks, Telethon message parsing (Hinglish/Hindi/English/emojis), idempotency, review queue, and manual fallback entry.
- **Phase 4**: Cricsheet historical import, Wilson confidence intervals, venue and team toss stats, and pluggable market entries.
- **Phase 5**: Pre-match cryptographic locking (SHA-256), immutability guards (blocking post-toss creation or modification), Brier calibration tables, and streak tracking.
- **Phase 6**: Multi-source adapter pattern, cross-source entity normalization, append-only snapshots, automated post-toss scoring, and audited leaderboards.
- **Phase 7**: Structured logging with automated secret redaction, SQLite hot backups with retention rotation, APScheduler job registration, and automated codebase scans for forbidden marketing language.

**Current Test Status: 127 Passed, 0 Failed, 0 Skipped.**

---

## 🔒 Security & Codebase Honesty Scan

An automated check in `tests/test_phase7_reliability_and_ops.py::test_codebase_zero_forbidden_words` scans every file in the repository to guarantee:
- Zero occurrences of `"guaranteed"`, `"99.99%"`, or `"sure-shot"`.
- Zero hardcoded credentials or API secrets.
- All mutating POST endpoints enforce valid CSRF tokens.
- All session cookies carry `HttpOnly`, `SameSite=Lax`, and configurable `Secure` flags.
