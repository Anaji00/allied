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

    # AI features (app/services/ai). "groq" (default) uses Groq's free tier:
    # fast, no cost, but it is rate limited and prompts leave the building
    # (Groq keeps no inputs by default; turn on Zero Data Retention in the
    # Groq console). "ollama" runs local models; "claude" uses the Anthropic
    # API (billed, needs ANTHROPIC_API_KEY and `pip install anthropic`); "off"
    # disables AI and every feature reports that plainly instead of guessing.
    AI_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    # Production models on Groq as of Oct 2026 (the Llama 3.x models were shut
    # down in Aug 2026). gpt-oss-120b supports strict JSON-schema answers.
    GROQ_TEXT_MODEL: str = "openai/gpt-oss-120b"
    # Reads scanned drawings. Preview on Groq: may change without notice.
    GROQ_VISION_MODEL: str = "qwen/qwen3.8-27b"
    GROQ_SPEECH_MODEL: str = "whisper-large-v3-turbo"
    # gpt-oss thinks before it answers; its thinking counts against the
    # free tier's tokens-per-minute, so keep it short.
    GROQ_REASONING_EFFORT: str = "low"
    # When the main engine is rate limited or down, try this one instead
    # (e.g. "ollama" if a local model is installed). Blank = say AI is busy.
    AI_FALLBACK_PROVIDER: str = ""
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
    # Server-side transcription of recordings (phone calls). With
    # AI_PROVIDER=groq and a GROQ_API_KEY, Groq's Whisper is used. Otherwise
    # a local model: `pip install faster-whisper`, then e.g. "small". Blank =
    # off. Live dictation in the browser works without either.
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
    # Reads only. With this on (the default), no change of any kind is sent
    # to monday, even with simulation off: every GraphQL mutation is blocked
    # and logged in the client. Turn it off only for a test board.
    MONDAY_READ_ONLY: bool = True
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
    # Full re-read of every board: a safety net once change checks are on.
    PROJECTS_POLL_SECONDS: int = 3600
    # A one-call check of each board's last change; only changed boards are read.
    # 0 turns it off (then boards are only read every PROJECTS_POLL_SECONDS).
    MONDAY_CHANGE_CHECK_SECONDS: int = 90
    MONDAY_CHANGE_CHECK_OFF_HOURS_SECONDS: int = 900
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
    # Open, but waiting on someone else (the customer reviewing, work paused): shown, never chased.
    MILESTONE_HOLD_LABELS: str = "Paused,On Hold"
    # Tell owners (and leadership for unowned work) about open milestones with no due date or owner.
    MILESTONE_UNPLANNED_ALERTS: bool = True
    # Written back to the milestone's status column as submissions move.
    MILESTONE_SUBMITTED_LABEL: str = "Submitted"
    MILESTONE_APPROVED_LABEL: str = "Approved"
    MILESTONE_CHANGES_LABEL: str = "Changes requested"
    SUBMISSION_MAX_MB: int = 50
    MAX_REQUEST_MB: int = 60  # any request body over this is refused before it is read
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

    # Working hours for business-day clocks (RFQ deadlines, vendor chasers).
    BUSINESS_DAY_START: int = 8
    BUSINESS_DAY_END: int = 17
    BUSINESS_HOLIDAYS: str = "2026-11-26,2026-11-27,2026-12-24,2026-12-25,2027-01-01,2027-05-31,2027-07-05,2027-09-06"

    # Vendor jobs: one item per vendor PO, read from a monday board (or added
    # here). Each needs a ship-by date worked back from the project due date:
    # due - transit (door to door) - buffer. The defaults are China to the US.
    MONDAY_VENDOR_BOARD_ID: str = ""
    VENDOR_VENDOR_COLUMN_ID: str = ""      # text, dropdown or connect-boards; blank = group title
    VENDOR_PROJECT_COLUMN_ID: str = ""     # connect-boards column to the projects board
    VENDOR_PO_COLUMN_ID: str = ""
    VENDOR_DESCRIPTION_COLUMN_ID: str = ""  # added after the item name (Allied: the item name is the customer)
    VENDOR_OWNER_COLUMN_ID: str = ""
    VENDOR_STATUS_COLUMN_ID: str = ""
    VENDOR_ORDERED_COLUMN_ID: str = ""     # PO placed
    VENDOR_REQUESTED_COLUMN_ID: str = ""   # ship date Allied asked for
    VENDOR_ACK_COLUMN_ID: str = ""         # vendor confirmed the PO
    VENDOR_PROMISED_COLUMN_ID: str = ""    # vendor's current promised ship date
    VENDOR_SHIPPED_COLUMN_ID: str = ""     # actually shipped / ready
    VENDOR_LEAD_DAYS_COLUMN_ID: str = ""   # vendor's quoted lead time in days
    VENDOR_MODE_COLUMN_ID: str = ""        # Sea / Air / Truck
    # The customer's date, for jobs not linked to a project: a date column, or
    # a start date column plus a number of weeks (Allied: Vendor PO Issued + Cust Lead).
    VENDOR_CUSTOMER_DUE_COLUMN_ID: str = ""
    VENDOR_CUSTOMER_FROM_COLUMN_ID: str = ""
    VENDOR_CUSTOMER_WEEKS_COLUMN_ID: str = ""
    # True: the customer's date is delivery, so transit is taken off. False: it is the ship date.
    VENDOR_CUSTOMER_DUE_IS_DELIVERY: bool = True
    # Only items in these groups are open vendor work; other groups count as shipped. Blank = all.
    VENDOR_OPEN_GROUPS: str = ""
    VENDOR_DONE_LABELS: str = "Shipped,Done,Complete,Received"
    VENDOR_TRANSIT_DAYS_SEA: int = 45      # vendor door to US customer: ~5-6 weeks West Coast, 7-9 East
    VENDOR_TRANSIT_DAYS_AIR: int = 10
    VENDOR_TRANSIT_DAYS_TRUCK: int = 5
    # Vendors that ship within the US, by the name used on the board. With no mode
    # column on the board, these get truck transit and every other vendor gets sea.
    VENDOR_DOMESTIC_VENDORS: str = ""
    VENDOR_BUFFER_DAYS: int = 7            # safety margin before the need date
    VENDOR_ACK_BUSINESS_DAYS: int = 2      # PO not confirmed after this: chase
    VENDOR_CHECKIN_DAYS: int = 7           # ask the vendor to reconfirm this long before the ship date
    VENDOR_FOLLOWUP_DAYS: int = 30         # not shipped this long after its PO (no quoted lead time): follow the vendor up
    VENDOR_HISTORY_MIN_JOBS: int = 5       # fewer shipped jobs: plan on quote + 20%
    VENDOR_OTD_TARGET: float = 0.95

    # Autopilot: what Allied's system does on its own, on a timer. Each play is
    # off, suggest (anything sent outside Allied waits for a person to confirm
    # it) or auto (done straight away; email still goes out only when sending
    # is switched on). Nothing is ever written to monday by these plays.
    AUTOPILOT_ENABLED: bool = True
    AUTOPILOT_MODES: str = "rfq_board=auto,orders_watch=auto,vendor_chaser=suggest,rfq_vendor_chaser=suggest,quote_followup=suggest"
    AUTOPILOT_SECONDS: int = 3600
    AUTOPILOT_DRAFT_HOURS: int = 72          # drafts waiting for a person expire after this
    # Event-driven reactions (things that happen, not the timer), each off / suggest / auto.
    AUTOPILOT_REACTIONS: str = ("rfq_acknowledge=suggest,rfq_ask_vendors=suggest,rfq_won=auto,"
                                "order_shipped=auto,order_photos=auto,vendor_late_chase=suggest")
    AUTOPILOT_EVENTS_ASYNC: bool = True      # reactions run in the background, never slowing the request
    # Drafts people must have sent or cancelled before the scorecard gives advice on an automation.
    AUTOPILOT_SCORECARD_MIN: int = 5
    # Each new draft is pushed to the ntfy topic with Send / Review buttons (when push is set up).
    # The buttons need APP_BASE_URL to be an address the phone can reach (not localhost).
    AUTOPILOT_PUSH_DRAFTS: bool = True
    # Customer RFQ form (/api/v1/rfqs/form): a shared token the form page sends; empty switches the form off.
    RFQ_FORM_TOKEN: str = ""
    RFQ_FORM_PER_HOUR: int = 10
    # Who confirms drafts: "management" (the reviewers below) or "owner" (the job's owner when
    # it has one, else the reviewers).
    AUTOPILOT_REVIEW_BY: str = "management"
    AUTOPILOT_REVIEWER_EMAILS: str = ""      # blank = leadership on the Alerts screen, else ADMIN_EMAIL
    # Vendor contacts, as name=email pairs, for names on the boards ("HELEN=helen@...").
    # Names are also matched against the vendor master (id, name, or the part before @).
    VENDOR_EMAILS: str = ""
    # Contact names used on the boards in place of the vendor, as NAME=VENDOR pairs
    # (Open RFQs lists "KWS (Max)" and "Eutectic (Vicki)"; 2026 Orders writes MAX).
    VENDOR_ALIASES: str = ""
    # Boards shown in the app as they are, with no tracking logic (history, logs), and
    # monday Docs whose tables are shown the same way. Read only, like every board.
    MONDAY_REFERENCE_BOARD_IDS: str = ""
    MONDAY_DOC_IDS: str = ""
    # The monday Open RFQs board, read into the RFQ tracker. Columns by id or title.
    MONDAY_RFQ_BOARD_ID: str = ""
    RFQ_BOARD_CUSTOMER_COLUMN_ID: str = "Customer"
    RFQ_BOARD_STATUS_COLUMN_ID: str = "Status"
    RFQ_BOARD_VENDORS_COLUMN_ID: str = "Bidding vendors"
    RFQ_BOARD_FOLLOWUP_COLUMN_ID: str = "Last follow up"
    RFQ_BOARD_STATUS_MAP: str = ("waiting on vendor quote=WAITING_VENDOR,waiting on freight=WAITING_VENDOR,"
                                 "waiting on drawing=CLARIFYING,need quote checked=PRICING,need markup=PRICING,"
                                 "need revised quote=PRICING,stuck=ACKNOWLEDGED")
    QUOTE_FOLLOWUP_DAYS: str = "2,7"         # working days after a quote is sent
    # Orders board (MONDAY_VENDOR_BOARD_ID) watch: unpaid orders and vendor photos waiting.
    ORDERS_PAYMENT_GROUPS: str = "Payment"
    ORDERS_PAYMENT_STATUS_COLUMN_ID: str = "Payment Status"
    ORDERS_PAID_LABELS: str = "Paid in Full"
    ORDERS_PHOTO_LABELS: str = "EXPECTING PICS,NEED PICS APPROVED"

    # Customer RFQs. The clock starts when the request arrives and counts
    # business hours; waiting on a vendor's price still counts.
    RFQ_ACK_BUSINESS_HOURS: int = 4
    RFQ_QUOTE_DAYS_SIMPLE: int = 1         # repeat or catalogue parts
    RFQ_QUOTE_DAYS_STANDARD: int = 2
    RFQ_QUOTE_DAYS_COMPLEX: int = 5        # new design or vendor pricing needed
    RFQ_VENDOR_CHASE_DAYS: int = 2         # business days waiting on a vendor price before a chaser
    RFQ_VENDOR_ESCALATE_DAYS: int = 4
    RFQ_TEAM_EMAILS: str = ""              # who is told about new RFQs; blank = leadership

    # Assistant (the chat widget). It reads through tools and only proposes
    # changes; a person confirms each one. MCP servers add third-party tools:
    # a JSON list of {"name", "url", "headers": {...}, "read_only_tools": [...], "min_role"}.
    # Header values written as "env:NAME" are read from that environment variable.
    ASSISTANT_ENABLED: bool = True
    ASSISTANT_MAX_STEPS: int = 5
    ASSISTANT_RESULT_CHARS: int = 4000
    ASSISTANT_HISTORY_MESSAGES: int = 10
    ASSISTANT_ACTION_TTL_MINUTES: int = 15
    ASSISTANT_MCP_SERVERS: str = ""

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
    # Connect to the Sage 50 company on this machine (ODBC reads). Off, the
    # supported mode, means Sage is reached through import files only. Direct
    # writes are not built, so leave this off until the Sage-PC helper exists.
    SAGE_LIVE_CONNECTION: bool = False
    # Old name for the inverse of SAGE_LIVE_CONNECTION, still honoured when set.
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
    # Every folder to read, comma-separated (e.g. "INBOX,[Gmail]/Sent Mail"); blank = IMAP_FOLDER.
    IMAP_FOLDERS: str = ""
    # How far back each read looks, and how new a message must be to be acted on. Older ones
    # (the backlog on first start) are only logged, so they can't raise orders or RFQs today.
    IMAP_LOOKBACK_DAYS: int = 3
    IMAP_ACT_ON_HOURS: int = 24
    # Allied's own domains: mail from them is "internal". Blank = the domain of EMAIL_FROM.
    MAIL_OUR_DOMAINS: str = ""
    # Domains customers order from, besides the one on their record: "domain=customer_id" for one
    # customer (knaufinsulation.com=10148), or a bare domain for any. An order email from any other
    # domain is held for a person instead of raised.
    ORDER_TRUSTED_SENDER_DOMAINS: str = ""
    # Extra or replacement topic tags, JSON {"tag": ["keyword", ...]} (a "re:" prefix makes it a regex).
    MAIL_TAG_RULES: str = ""
    # Senders or domains whose mail is logged quietly as noise (e.g. "monday.com,linkedin.com").
    MAIL_QUIET_SENDERS: str = ""
    # Sorted mail that alerts at once; the rest that needs a person goes in the daily summary.
    MAIL_ALERT_NOW: str = "complaint,delivery_question,urgent"
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
        env_ignore_empty=True,  # KEY= in .env means "use the default", not an empty value
        extra="ignore",
        case_sensitive=False,
    )


    @staticmethod
    def label_set(raw: str) -> set:
        """Parses a comma-separated label list into a case-folded set."""
        return {part.strip().casefold() for part in (raw or "").split(",") if part.strip()}

    @property
    def sage_live(self) -> bool:
        """True when connected to the Sage company on this machine (see SAGE_LIVE_CONNECTION)."""
        if "SAGE_LIVE_CONNECTION" in self.model_fields_set:
            return self.SAGE_LIVE_CONNECTION
        if "SAGE_SIMULATION_MODE" in self.model_fields_set:
            return not self.SAGE_SIMULATION_MODE
        return self.SAGE_LIVE_CONNECTION

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
