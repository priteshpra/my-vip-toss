# Toss Analytics & Tracking Admin Panel

A production-grade Python cricket toss analytics, verification, and tipster tracking admin panel engineered with strict scientific honesty principles, pre-match cryptographic locking, and empirical baseline validation.

---

## ⚖️ Scientific Honesty Rules & Principles

A cricket toss is an **independent, fair coin flip with theoretical probability \(p = 0.50\)**. This platform enforces non-negotiable honesty gates:
- **No False Claims**: Words like "guaranteed", "99.99%", or "sure-shot" are strictly forbidden across code, UI, logs, and documentation.
- **Real Measured Accuracy**: Every source and tipster channel displays real measured accuracy with sample size \(n\), a **95% Wilson Score Confidence Interval**, and an **exact two-sided binomial test** against the 50% baseline.
- **Cryptographic Immutability**: Predictions and calls are locked with a UTC timestamp and **SHA-256 cryptographic seal** *strictly BEFORE* scheduled match start. The ledger is **append-only** (no updates, no deletions, no post-toss entries).
- **Permanent Disclaimer**: Displayed on every view:
  > *"Toss is a random event. Past stats do not guarantee results. Play responsibly."*
- **Zero Hardcoded Secrets**: All sensitive tokens and keys are loaded via `.env` (using `pydantic-settings`). `.env.example` provides placeholders only.
- **Timezone Discipline**: All database records store timestamps in **UTC**; displayed across the UI in **IST (Asia/Kolkata)**.

---

## 🛠️ Tech Stack

- **Python 3.11+ / 3.14**
- **FastAPI**: Modern, high-performance async web framework
- **Jinja2 + HTMX + Tailwind CSS**: Responsive, server-rendered dynamic UI without heavy JS framework overhead
- **SQLAlchemy 2.0 (Async + Sync) & Alembic**: Database ORM and schema migration system (SQLite default, PostgreSQL-ready via `DATABASE_URL`)
- **APScheduler**: Automated background job scheduling for match sync and toss result verification
- **Telethon**: Asynchronous Telegram client for tracking and auditing tipster channels
- **httpx**: Resilient async HTTP client with retries and timeout protection
- **scipy / numpy / pandas**: Mathematical statistical functions (Wilson score interval, exact binomial test, Brier score)
- **pytest & pytest-asyncio**: Unit and integration test suite
- **loguru**: Structured application logging

---

## 📂 Project Architecture

```
my-vip-toss/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory, lifespan & scheduler startup
│   ├── config.py                # Pydantic BaseSettings with resolved DB URLs
│   ├── db.py                    # Async/Sync engines, session makers, UTC/IST helpers
│   ├── models.py                # SQLAlchemy 2.0 declarative models
│   ├── schemas.py               # Pydantic v2 schemas for API, audit, and accuracy
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── security.py          # PBKDF2 password hashing & HMAC signed session cookies
│   │   └── dependencies.py      # Admin authentication dependencies
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── gaming_helper.py     # Async GamingHelper client with local fallback
│   │   ├── telegram_listener.py # Telethon tipster parser & pre-toss audit validator
│   │   ├── cricsheet.py         # Cricsheet open data historical match parser
│   │   ├── espn_cricbuzz.py     # Cricbuzz/CREX live toss verification collector
│   │   └── external_sources.py  # Generic tipster webhook & feed collector
│   ├── services/
│   │   ├── __init__.py
│   │   ├── accuracy_service.py  # Wilson 95% CI, binomial null tests vs 50%, Brier score
│   │   ├── match_service.py     # Match query, filtering, and detail hydration
│   │   ├── toss_service.py      # Official ground truth recording & auto-evaluation
│   │   ├── prediction_service.py# Pre-toss cryptographic lock & append-only validation
│   │   ├── analysis_service.py  # Longitudinal team toss stats & H2H analysis
│   │   └── source_service.py    # Multi-source leaderboard & update health logs
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── scheduler.py         # AsyncIOScheduler runner
│   │   └── tasks.py             # Match sync, toss verification, and hash audit tasks
│   └── web/
│       ├── __init__.py
│       ├── routes.py            # Dashboard, match center, ledger, analytics, & auth routes
│       ├── static/              # CSS & JS assets
│       └── templates/           # Tailwind + HTMX Jinja2 templates & partials
├── alembic/
│   ├── alembic.ini              # Alembic migration configuration
│   ├── env.py                   # Migration environment connected to Base.metadata
│   └── versions/                # Generated migration versions
├── data/                        # Historical and fallback cache datasets
├── tests/                       # Complete Pytest test suite (42 tests passing)
├── .env.example                 # Environment variables template with placeholders
├── requirements.txt             # Project dependencies
└── README.md                    # Project documentation
```

---

## 🚀 Quickstart & Setup

### 1. Clone & Configure Environment
```bash
cp .env.example .env
```
Edit `.env` to configure your admin credentials and optional collector tokens:
```ini
ENVIRONMENT=development
LOG_LEVEL=INFO

DATABASE_URL=sqlite+aiosqlite:///./toss_analytics.db
SYNC_DATABASE_URL=sqlite:///./toss_analytics.db
APP_TIMEZONE=Asia/Kolkata

ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_this_secure_admin_password
SECRET_KEY=change_this_to_a_random_secret_key_at_least_32_chars
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run Database Migrations
```bash
python -m alembic upgrade head
```

### 4. Start the Application Server
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser at `http://127.0.0.1:8000`.

---

## 📊 Core Features & Views

| Route | Feature Description |
|---|---|
| `/` | **Dashboard**: Live match overview, confirmed toss results, and audited tipster performance. |
| `/matches` | **Match Centre**: Filterable and searchable live/upcoming cricket fixtures powered by HTMX. |
| `/matches/{id}` | **Match Detail**: Head-to-Head toss records, venue tendencies, locked pre-toss calls, and official results. |
| `/predictions` | **Prediction Ledger**: Cryptographically locked append-only ledger with real-time hash verification. |
| `/analytics` | **Statistical Validation**: Deep analysis with 95% Wilson intervals, sample size flags (\(n \ge 30\)), and binomial \(p\)-values against the 50% fair baseline. |
| `/sources` | **Sources Leaderboard**: Tracked tipster channels ranked by objective measured accuracy. |
| `/audit` | **Integrity Tool**: Interactive tool to audit the SHA-256 seal of any prediction record. |
| `/auth/login` | **Admin Authentication**: Secure login issuing HMAC-signed session cookies. |

---

## 🧪 Testing & Verification

Run the entire test suite:
```bash
python -m pytest tests/ -v
```

The test suite validates:
- **Accuracy Math**: Wilson score intervals (\(n=0\), \(n=1\), fair coins, boundaries), exact binomial tests (\(H_0: p=0.50\)), and Brier score calibration.
- **Cryptographic Immutability**: SHA-256 seal generation, avalanche effect, and append-only enforcement.
- **Pre-Toss Locking**: Automatic disqualification for calls submitted after scheduled start time.
- **Ground Truth Evaluation**: Automatic grading (HIT, MISS, SKIP) upon official toss verification.
- **Collectors & Fallbacks**: Resilient parsing for Telegram calls, GamingHelper cache, and Cricsheet data.
- **Web Interface & HTMX**: All routes, templates, partials, and admin authentication flows.

All **42 automated tests pass** with zero errors.

