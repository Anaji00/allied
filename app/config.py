"""Runtime configuration and environment settings for Allied Universal Bridge."""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with Pydantic V2 validation and .env parsing."""

    # Application
    APP_NAME: str = "Allied Universal Bridge"
    APP_ENV: str = "development"
    PORT: int = 8000
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "allied_universal_bridge_default_key"

    # Ollama Local LLM Agent
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    # Must be a model that is actually pulled (`ollama list`). The old default,
    # llama3.2, was not installed, so the copilot silently fell back to its
    # non-AI answers. qwen2.5 handles English, Vietnamese and Mandarin.
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # AI features (app/services/ai). Local Ollama is the default so nothing is
    # spent on inference; "claude" switches to the Anthropic API (needs
    # ANTHROPIC_API_KEY and `pip install anthropic`); "off" disables AI and
    # every feature reports that plainly instead of guessing.
    AI_PROVIDER: str = "ollama"
    AI_TEXT_MODEL: str = "qwen2.5:7b"
    # Reads drawings as images. Blank = drawings are read from their text
    # layer only. e.g. `ollama pull qwen2.5vl:7b` then set it here.
    AI_VISION_MODEL: str = ""
    AI_CLAUDE_MODEL: str = "claude-opus-5-5"
    AI_TIMEOUT_SECONDS: float = 300.0
    # Ollama's default context is 4096 tokens and longer prompts are cut off
    # silently, which loses the end of a long email or drawing. 16384 fits a
    # multi-page drawing's text; lower it if the machine runs short of memory.
    AI_CONTEXT_TOKENS: int = 16384
    # Upper bound on one answer, so a rambling model cannot run for minutes.
    AI_MAX_OUTPUT_TOKENS: int = 2048
    # Below this confidence an AI result goes to a person, never straight through.
    AI_CONFIDENCE_THRESHOLD: float = 0.8
    # When on, inbound email is sorted and read by AI before the pattern parser.
    AI_EMAIL_TRIAGE_ENABLED: bool = False
    # Server-side transcription of recordings (phone calls). Needs
    # `pip install faster-whisper`; e.g. "small". Blank = off. Live dictation
    # in the browser works without it.
    AI_WHISPER_MODEL: str = ""
    # The phone system posts transcripts to /ai/calls?token=<this>. Blank =
    # only signed-in operators can submit calls.
    PHONE_WEBHOOK_TOKEN: str = ""
    AI_GLOSSARY_PATH: Optional[Path] = None
    AI_GRADE_MAP_PATH: Optional[Path] = None

    # Monday.com v2 GraphQL
    MONDAY_API_URL: str = "https://api.monday.com/v2"
    MONDAY_API_KEY: str = ""
    # Pinned so a monday release cannot change response shapes underneath us.
    # Review quarterly against monday's version calendar.
    # 2024-01 (the old hardcoded value) and everything through 2025-01 have
    # been retired; 2026-07 is a supported release as of Oct 2026.
    MONDAY_API_VERSION: str = "2026-07"
    # Board updates are posted only when this is False AND an API key is set.
    # Previously this was tied to SAGE_SIMULATION_MODE, so going live on Sage
    # also silently started writing to the real board, and vice versa.
    MONDAY_SIMULATION_MODE: bool = True
    MONDAY_BOARD_ID: Optional[str] = None

    # Webhook authentication. monday does not HMAC-sign webhook bodies:
    #  - Webhooks created by a monday *app* (integration recipes, or the API
    #    with an app's OAuth token) carry an Authorization JWT signed with the
    #    app's Signing Secret -> set MONDAY_WEBHOOK_SECRET to that secret.
    #  - Plain board webhooks (Integrate -> Webhooks, or create_webhook with a
    #    personal token) carry no signature at all -> register the URL with a
    #    long random ?token=... and set MONDAY_WEBHOOK_TOKEN to the same value.
    # With neither configured the endpoint only accepts traffic outside
    # production (APP_ENV != "production").
    MONDAY_WEBHOOK_SECRET: str = ""
    MONDAY_WEBHOOK_TOKEN: str = ""

    # Event routing. Column ids and labels are board configuration, not code:
    # matching on substrings of whatever label changed ("SHIP" in label) meant
    # a label like "Not shipped yet" dispatched a shipment.
    MONDAY_STATUS_COLUMN_ID: str = "status"
    MONDAY_LABELS_CREATE_ORDER: str = "Ready for Sage,New Order"
    MONDAY_LABELS_RELEASE_PO: str = "Approved for Production,Send PO"
    MONDAY_LABELS_DISPATCH: str = "Shipped,Dispatched,Dispatched / Shipped"
    # A new item on the board creates an order only when this is on; most
    # teams create items before the PO details are filled in.
    MONDAY_CREATE_ORDER_ON_NEW_ITEM: bool = False
    # Optional column ids to read order details from. Blank falls back to the
    # item name convention "Customer - PO number - Item".
    MONDAY_CUSTOMER_COLUMN_ID: str = ""
    MONDAY_PO_COLUMN_ID: str = ""
    MONDAY_ITEM_COLUMN_ID: str = ""
    MONDAY_QTY_COLUMN_ID: str = ""
    MONDAY_ADDRESS_COLUMN_ID: str = ""
    # Write-back targets on the board. Blank skips that write.
    MONDAY_SO_COLUMN_ID: str = ""
    MONDAY_SYNC_STATUS_COLUMN_ID: str = ""
    # monday user id of the API token's owner. Events caused by our own
    # write-backs are ignored, which breaks the webhook -> write -> webhook loop.
    MONDAY_BOT_USER_ID: Optional[int] = None

    # Projects and milestones live on a monday board; Allied's system reads it
    # and does all the schedule checking and alerting. Two layouts:
    #   "subitems": each item is a project, its subitems are the milestones
    #   "groups":   each group is a project, its items are the milestones
    MONDAY_PROJECTS_BOARD_ID: str = ""
    MONDAY_PROJECTS_LAYOUT: str = "subitems"
    PROJECTS_POLL_SECONDS: int = 600
    PROJECT_OWNER_COLUMN_ID: str = "person"
    PROJECT_DUE_COLUMN_ID: str = "date"
    PROJECT_STATUS_COLUMN_ID: str = "status"
    MILESTONE_OWNER_COLUMN_ID: str = "person"
    MILESTONE_REVIEWER_COLUMN_ID: str = ""
    MILESTONE_DUE_COLUMN_ID: str = "date"
    MILESTONE_STATUS_COLUMN_ID: str = "status"
    # A checkbox column marking milestones that need design documents. Blank =
    # decided by name: milestones whose name contains one of these words.
    MILESTONE_SUBMISSION_COLUMN_ID: str = ""
    MILESTONE_SUBMISSION_KEYWORDS: str = "drawing,model,calc,submission,design package,design doc"
    MILESTONE_DONE_LABELS: str = "Done,Approved,Complete,Completed"
    # Written back to the milestone's status column as submissions move.
    MILESTONE_SUBMITTED_LABEL: str = "Submitted"
    MILESTONE_APPROVED_LABEL: str = "Approved"
    MILESTONE_CHANGES_LABEL: str = "Changes requested"
    SUBMISSION_MAX_MB: int = 50
    SUBMISSION_EXTENSIONS: str = "pdf,dwg,dxf,step,stp,igs,iges,sldprt,sldasm,slddrw,xlsx,xls,docx,doc,png,jpg,jpeg,zip"

    # Schedule rules (days are calendar days in BUSINESS_TZ).
    BUSINESS_TZ: str = "America/Chicago"
    DUE_SOON_DAYS: int = 2
    ESCALATE_AFTER_DAYS: int = 2
    REVIEW_WAIT_DAYS: int = 2
    SHIP_SLIP_DAYS: int = 3
    SHIP_SILENT_DAYS: int = 5          # no update while at sea
    SHIP_PORT_SILENT_DAYS: int = 2     # no update while sitting at the port
    # Demurrage runs roughly $75-300 a day once the port's free time ends.
    SHIP_FREE_TIME_WARN_DAYS: int = 2
    SCHEDULE_CHECK_SECONDS: int = 900
    # Freight forwarders' sending domains; their emails become shipment updates.
    FORWARDER_DOMAINS: str = ""

    # Notifications. The in-app inbox always gets every alert; email, text
    # and push are deliveries of it.
    LEADERSHIP_EMAILS: str = ""
    NOTIFY_COOLDOWN_HOURS: int = 24
    DIGEST_HOUR: int = 7
    # Texts and pushes wait until morning in these hours unless critical.
    QUIET_HOURS_START: int = 21
    QUIET_HOURS_END: int = 7
    QUIET_WEEKENDS: bool = True
    APP_BASE_URL: str = "http://localhost:3000"
    SMS_PROVIDER: str = ""  # "" (off) or "twilio"
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM: str = ""
    # Email-to-text gateways are gone (AT&T shut its down June 2025; Verizon by
    # March 2027), so texts need Twilio with A2P 10DLC or toll-free verification.
    # Free phone push via ntfy (ntfy.sh or self-hosted) is the cheaper default.
    NTFY_URL: str = ""
    NTFY_TOPIC: str = ""
    NTFY_TOKEN: str = ""

    # Inbox processing. Events are persisted before they are acknowledged and
    # retried with exponential backoff; after the last attempt they are parked
    # as DEAD and raised in the exceptions queue.
    WEBHOOK_MAX_ATTEMPTS: int = 6
    WEBHOOK_RETRY_BASE_SECONDS: int = 30
    WEBHOOK_SWEEP_SECONDS: int = 20
    # Allied's system, not monday, runs every automation; monday only holds
    # the job data. The board reader checks the trigger column this often
    # through the API (300s = 288 calls a day of the 1,000 allowed on the
    # cheaper plans) and spends no monday automation or integration actions.
    # Webhooks stay optional for instant reactions. 0 turns the reader off.
    MONDAY_POLL_SECONDS: int = 300

    # Sage 50 US Accounting (ODBC & COM SDK)
    SAGE_ODBC_DSN: str = "Peachtree"
    SAGE_COMPANY_PATH: str = r"C:\Sage\Peachtree\Company\AlliedWelding"
    SAGE_USER: str = "allied_api"
    SAGE_PASSWORD: str = ""
    SAGE_SIMULATION_MODE: bool = True

    # JWT Authentication
    JWT_SECRET_KEY: str = "allied_enterprise_jwt_secret_2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8-hour workday
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Serves authenticated endpoints as ADMIN when no token is supplied.
    # Deliberately separate from DEBUG so the development default never
    # disables authentication. Must stay False outside a local workstation.
    AUTH_DEV_BYPASS: bool = False

    # Pre-seeded Admin Credentials
    ADMIN_EMAIL: str = "admin@alliedweldingsystems.com"
    ADMIN_PASSWORD: str = "AlliedEnterprise2026!"
    ADMIN_FULL_NAME: str = "Allied System Administrator"

    # Demo fixtures (sample inbox emails, sample reconciliations, sample NCR).
    # Seeding runs real orchestration and writes real records, so it is opt-in.
    SEED_DEMO_DATA: bool = False

    # Business & Compliance Rules
    MIN_GROSS_MARGIN_PCT: float = 15.0
    DEFAULT_BLIND_SHIPPING: bool = True
    DEFAULT_KERF_WIDTH: float = 0.125  # 1/8" saw kerf
    SIMULATION_LATENCY_MS: int = 15
    VENDOR_PRICE_STALENESS_DAYS: int = 60  # Days before vendor price is stale

    # Multi-Facility Configuration
    FACILITIES: dict = {
        "HQ": {"name": "Allied Welding HQ", "city": "Lanett", "state": "AL", "country": "USA", "currency": "USD"},
        "EDMONTON": {"name": "Allied Canada Yard", "city": "Edmonton", "province": "AB", "country": "CAN", "currency": "CAD"},
        "ZIBO": {"name": "Zibo Supereal Industrial", "city": "Zibo", "province": "Shandong", "country": "CHN", "currency": "CNY"},
    }
    DEFAULT_FACILITY: str = "HQ"

    # Multi-Currency (USD / CAD / CNY)
    SUPPORTED_CURRENCIES: list = ["USD", "CAD", "CNY"]
    DEFAULT_CURRENCY: str = "USD"
    FX_RATES: dict = {"USD": 1.0, "CAD": 1.36, "CNY": 7.24}  # Baseline rates, updated operationally
    CAD_TO_USD_RATE: float = 0.735  # 1 CAD ~ 0.735 USD
    CNY_TO_USD_RATE: float = 0.138  # 1 CNY ~ 0.138 USD

    # Outbound Webhooks (Slack / Teams Notifications)
    SLACK_WEBHOOK_URL: str = ""
    TEAMS_WEBHOOK_URL: str = ""

    # Outbound Email (purchase orders, dispute letters, repricing RFQs).
    # Sending is off by default and stays off until a host is configured AND
    # EMAIL_SEND_ENABLED is set. Every draft is still recorded either way, so
    # turning this on changes who sees the mail, never whether it was produced.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    EMAIL_FROM: str = "operations@alliedweldingsystems.com"
    EMAIL_REPLY_TO: str = ""
    EMAIL_SEND_ENABLED: bool = False
    # Nothing leaves the building while this holds an address: every message is
    # redirected here instead of to the supplier. Set it during pilot runs.
    EMAIL_REDIRECT_ALL_TO: str = ""

    # Inbound mailbox polling (zero-touch order intake).
    # Off by default; the daemon's logic runs regardless when an email is
    # handed to it over the API.
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    IMAP_USERNAME: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_USE_SSL: bool = True
    IMAP_FOLDER: str = "INBOX"
    IMAP_POLL_ENABLED: bool = False
    IMAP_POLL_SECONDS: int = 120
    # Messages are marked seen once ingested so a restart does not re-process
    # the inbox. Order creation is idempotent on the customer PO number, so a
    # duplicate would be rejected anyway -- this just avoids the noise.
    IMAP_MARK_SEEN: bool = True

    # Predictive wear.
    # Service life is learned from Allied's own reorder history once there are
    # enough repeat purchases; below this many observations the catalogue's
    # published figure is used instead.
    WEAR_MIN_OBSERVATIONS: int = 2
    WEAR_WARNING_PCT: float = 75.0
    WEAR_CRITICAL_PCT: float = 95.0

    # Database
    # Opening a SQLite database on a network share risks corruption, so it is
    # refused at startup unless this is explicitly set.
    ALLOW_NETWORK_DB: bool = False
    DATABASE_URL: str = ""  # Auto-resolved to sqlite:///data/allied_enterprise.db

    # Browser origins allowed to call the API, comma-separated.
    # A wildcard is not permitted here: it cannot be combined with credentialed
    # requests, and the API now carries bearer tokens.
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Directory Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "app" / "data"
    OUTPUT_DIR: Path = BASE_DIR / "output"
    DB_DIR: Path = BASE_DIR / "data"
    MASTER_ITEMS_PATH: Path = DATA_DIR / "master_items.csv"
    MASTER_CUSTOMERS_PATH: Path = DATA_DIR / "master_customers.csv"
    MASTER_VENDORS_PATH: Path = DATA_DIR / "master_vendors.csv"
    ALIAS_DICT_PATH: Path = DATA_DIR / "alias_dictionary.json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


    @staticmethod
    def label_set(raw: str) -> set:
        """Parses a comma-separated label list into a case-folded set."""
        return {part.strip().casefold() for part in (raw or "").split(",") if part.strip()}

    @property
    def is_production(self) -> bool:
        return (self.APP_ENV or "").strip().lower() in {"production", "prod"}

    # Values shipped in this file or in .env.example. A production server
    # still using one of them would be signing tokens with a public key.
    _SHIPPED_SECRETS = {
        "SECRET_KEY": {
            "allied_universal_bridge_default_key",
            "allied_super_secret_bridge_key_change_in_production",
        },
        "JWT_SECRET_KEY": {
            "allied_enterprise_jwt_secret_2026",
            "change_me_to_a_long_random_string",
        },
        "ADMIN_PASSWORD": {
            "AlliedEnterprise2026!",
            "change_me_before_deploying",
        },
    }

    def insecure_secrets(self) -> list:
        """Names of secrets that are empty or still a shipped default."""
        insecure = []
        for name, shipped in self._SHIPPED_SECRETS.items():
            value = (getattr(self, name, "") or "").strip()
            if not value or value in shipped:
                insecure.append(name)
        return insecure

    def assert_production_secrets(self) -> None:
        """Refuses to run in production with default or empty secrets."""
        if not self.is_production:
            return
        insecure = self.insecure_secrets()
        if insecure:
            raise RuntimeError(
                "Refusing to start in production: "
                + ", ".join(insecure)
                + " must be set to unique values in the environment (they are empty "
                "or still the shipped defaults)."
            )

    @property
    def monday_live(self) -> bool:
        """True only when board writes will actually reach monday.com."""
        return bool(self.MONDAY_API_KEY) and not self.MONDAY_SIMULATION_MODE

    def cors_origins(self) -> list:
        """Parses CORS_ALLOWED_ORIGINS into a list, rejecting the wildcard."""
        origins = [o.strip() for o in (self.CORS_ALLOWED_ORIGINS or "").split(",") if o.strip()]
        return [o for o in origins if o != "*"]


settings = Settings()

# Ensure directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
