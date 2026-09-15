# Koinoxrista

A Python application for managing shared building expenses, from monthly expense entry and allocation to individual PDF statements, email delivery, and online payment tracking. The Greek-language interface supports property management companies working across multiple buildings, with company-scoped access to stored data.

## Application overview

The main workflow is:

1. Sign in through an OpenID Connect (OIDC) provider and select an authorized company.
2. Configure buildings, properties, facilities, allocation tables, and expense categories.
3. Enter monthly expenses manually or extract suggested entries from receipt uploads.
4. Calculate each property's share using weighted, equal, or direct allocation rules.
5. Preview and issue statements, retain revision history, and generate individual PDFs.
6. Deliver statements through SMTP and optionally create Stripe Checkout payment links.
7. Track delivery and payment status from the statement history interface.

The calculation engine uses decimal arithmetic and reconciles rounding differences so allocated charges match the original expense. Categories explicitly identify the responsible payer type: tenant, owner, or other. Allocation tables retain a user-entered source reference for traceability.

## Technology stack

| Area | Technologies and purpose |
| --- | --- |
| Language and domain modeling | Python, dataclasses, type annotations, and `Decimal` for financial calculations |
| Web interface | Streamlit with authentication support, custom CSS, and pandas for tabular views |
| Persistence | PostgreSQL 17, Psycopg 3, relational tables, JSONB snapshots, and versioned SQL migrations |
| Identity and authorization | OIDC login, explicit company memberships, PostgreSQL row-level security (RLS), and HMAC-SHA256 signed tenant context |
| Documents | fpdf2 with Unicode fonts for Greek-language PDF statements |
| Receipt extraction | OpenAI Python SDK for extracting structured suggestions from uploaded receipts |
| Email | Python `smtplib`, TLS, MIME attachments, SMTP profiles, and persisted delivery records |
| Payments | Stripe Checkout and Connect integration, signed webhooks, and a separate Python HTTP service |
| Configuration and deployment | python-dotenv, Streamlit secrets, Docker Compose for local PostgreSQL, a payment-service Dockerfile, and a Render deployment definition |
| Verification | Python `unittest`, mocks, and disposable PostgreSQL integration test runners |

Dependency ranges for the main application are maintained in [requirements.txt](requirements.txt).

### Standalone AI retrieval prototype

[rag_assistant.py](rag_assistant.py) also explores retrieval-augmented generation (RAG): retrieving relevant PDF passages before generating an answer. It uses LangChain, Hugging Face multilingual embeddings, FAISS similarity search, and a hosted Qwen model. Documents are split into overlapping chunks and the vector index is persisted locally.

This module is separate from the current portal workflow. Its LangChain, Hugging Face, and FAISS dependencies are not included in the main requirements file; the standard startup below does not enable it.

## System architecture

The main application is a layered Python application running inside Streamlit. The payment webhook receiver runs as a separate process with its own restricted database identity.

```mermaid
flowchart TD
    User[Browser] --> Portal[Streamlit company portal]
    Portal <--> OIDC[OIDC identity provider]
    Portal --> UI[Building and statement interface]
    UI --> Services[Application services]
    Services --> Domain[Expense allocation and validation]
    Services --> Storage[Repositories and statement stores]
    Storage --> Scope[Signed company context]
    Scope --> DB[(PostgreSQL with row-level security)]
    Services --> PDF[PDF generation]
    Services --> AI[OpenAI receipt extraction]
    Services --> SMTP[SMTP delivery]
    Services --> Stripe[Stripe Checkout / Connect]
    Stripe --> Webhook[Separate webhook service]
    Webhook --> DB
```

### Programming layers

| Layer | Main modules | Responsibility |
| --- | --- | --- |
| Presentation | `CompanyPortal.py`, `KoinoxristaAPP.py`, `configuration_forms.py`, `*_ui.py`, `ui_theme.py` | Authentication screens, forms, navigation, previews, and session state |
| Application services | `building_service.py`, `building_lifecycle.py`, `receipt_management.py`, `automatic_statement_email.py` | Coordinate workflows, lifecycle operations, and post-issue delivery |
| Domain logic | `expense_engine.py`, `allocation_configuration.py`, `facilities.py`, `property_removal.py` | Validate building configuration and calculate allocations independently of Streamlit |
| Persistence | `building_repository.py`, `monthly_store.py`, `statement_store.py`, `migrations/` | Store configuration, monthly drafts, issued snapshots, revisions, and PDF artifacts |
| Access control | `company_auth.py`, `database.py`, `company_admin.py`, migration `009` | Resolve identity, enroll memberships offline, and establish authorized database context |
| External integrations | `receipt_import.py`, `pdf_generator.py`, `statement_delivery.py`, `payment_service.py`, `payment_webhook*.py` | AI extraction, document output, email, Checkout creation, and payment confirmation |

These are module-level boundaries, with some workflow services also performing persistence operations. The portal does not expose a separate general-purpose REST API.

### Important design decisions

- **Company isolation:** the portal selects companies from verified memberships. Database connections carry short-lived signed claims, and PostgreSQL enforces access through RLS. Runtime connections use `koinoxrista_app`, a restricted role that does not own the schema.
- **Reproducible statements:** issued statements retain snapshots and revisions, allowing history and PDFs to be associated with the configuration and results used at issuance.
- **Explicit payment confirmation:** the UI creates payment requests; verified webhook events update payment status through restricted database functions. A browser return page does not mark a payment as paid.
- **Reviewable AI output:** receipt extraction supplies suggestions for the expense-entry workflow; the deterministic allocation engine performs the calculations.

## Step-by-step local startup

These instructions target a **fresh local database** on macOS or Linux, using a POSIX-compatible shell. Existing installations should review which migrations have already been applied instead of rerunning initialization.

### 1. Install prerequisites and clone

Use Python 3.13, Docker with Compose, Git, and a Greek-capable Unicode TrueType font. You also need an OIDC client for login; the interface is labeled for Google sign-in.

```bash
git clone https://github.com/my-koinoxrista-app/my-koinoxrista-app.git
cd my-koinoxrista-app
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Configure and start PostgreSQL

Create `.env.compose` with a local database administrator password:

```dotenv
POSTGRES_PASSWORD=replace-with-a-local-admin-password
```

```bash
docker compose up -d postgres
docker compose ps
```

Wait for the database to report healthy. Compose exposes PostgreSQL at `127.0.0.1:5433`, creates the `koinoxrista` database and administrator, and stores data in a named volume.

Create `.env` for application configuration:

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=koinoxrista
```

Keep the administrator password in `.env.compose`; application database access deliberately rejects `POSTGRES_PASSWORD` and `KOINOXRISTA_ADMIN_DSN` in its runtime environment.

### 3. Initialize the schema and restricted application role

Apply the base schema and migrations through `008`, stopping on any error:

```bash
for migration in \
  migrations/schema.sql \
  migrations/002_apartments.sql \
  migrations/003_allocation_tables.sql \
  migrations/004_expense_categories.sql \
  migrations/005_building_facilities.sql \
  migrations/006_composite_keys.sql \
  migrations/007_issued_statements.sql \
  migrations/008_companies.sql
do
  docker compose exec -T postgres psql -U koinoxrista -d koinoxrista \
    -v ON_ERROR_STOP=1 < "$migration" || break
done
```

After all eight files succeed, run the tenancy installer:

```bash
python scripts/apply_tenancy.py
```

Enter `MIGRATE` and the administrator password from step 2 when prompted. The installer applies migration `009`, creates the restricted application role, and writes generated runtime credentials to `.env.tenancy`. Copy all three values from `.env.tenancy` into `.env`:

```dotenv
POSTGRES_APP_USER=koinoxrista_app
POSTGRES_APP_PASSWORD=generated-value-from-env-tenancy
KOINOXRISTA_SCOPE_KEY=generated-value-from-env-tenancy
```

Then apply the remaining numbered migrations:

```bash
for migration in \
  migrations/010_statement_delivery.sql \
  migrations/011_payments.sql \
  migrations/012_payment_confirmation.sql
do
  docker compose exec -T postgres psql -U koinoxrista -d koinoxrista \
    -v ON_ERROR_STOP=1 < "$migration" || break
done
```

Continue only after all three succeed. Migration `012` is required by the current payment code and creates the separate webhook role. Configuring that role's password and service is covered in [PAYMENTS_SETUP.md](PAYMENTS_SETUP.md).

### 4. Configure login

Register `http://localhost:8501/oauth2callback` as an authorized redirect URI in your OIDC provider. Create `.streamlit/secrets.toml`:

```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "replace-with-a-long-random-secret"
client_id = "your-oidc-client-id"
client_secret = "your-oidc-client-secret"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Generate a cookie secret with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. The metadata URL above is for Google.

### 5. Start the portal and grant company access

```bash
streamlit run CompanyPortal.py
```

Open `http://localhost:8501` and sign in. A new account initially has no company membership. Expand the identity details on that screen to obtain the verified `iss` and `sub` values.

In a **separate administration terminal**, activate the virtual environment and run:

```bash
export KOINOXRISTA_ADMIN_DSN='postgresql://koinoxrista:URL_ENCODED_ADMIN_PASSWORD@127.0.0.1:5433/koinoxrista'
python company_admin.py
unset KOINOXRISTA_ADMIN_DSN
```

Use a URL-encoded password in the DSN. Select the initial company UUID printed by the script, enter the verified identity values, and confirm the membership. Migration `008` creates the initial company automatically. Refresh the portal after enrollment.

### 6. Configure PDF fonts and try the workflow

The PDF generator searches common macOS and Linux font locations. If it cannot find a suitable font, add absolute paths to `.env`:

```dotenv
KOINOXRISTA_FONT=/absolute/path/to/DejaVuSans.ttf
KOINOXRISTA_FONT_BOLD=/absolute/path/to/DejaVuSans-Bold.ttf
```

Restart the portal after changing configuration. Create a building, add its properties, configure allocation tables and categories, enter an expense, and calculate a period. Preview the PDF and issue a statement to explore the history workflow.

## Optional integrations

### Receipt extraction

Add `OPENAI_API_KEY` to `.env` to enable AI receipt extraction. `OPENAI_RECEIPT_MODEL` overrides the code's default model, `gpt-4.1-mini`. Manual expense entry does not require this integration.

### Statement email

Configure these `.env` values for your SMTP provider:

```dotenv
KOINOXRISTA_SMTP_HOST=smtp.example.com
KOINOXRISTA_SMTP_PORT=587
KOINOXRISTA_SMTP_SECURITY=starttls
KOINOXRISTA_SMTP_USERNAME=your-smtp-user
KOINOXRISTA_SMTP_PASSWORD=your-smtp-password
KOINOXRISTA_SMTP_FROM_EMAIL=statements@example.com
KOINOXRISTA_SMTP_FROM_NAME=Building Management
KOINOXRISTA_EMAIL_SENDING_ENABLED=true
```

Configure company sender details and property contacts in the portal. `KOINOXRISTA_AUTO_EMAIL_AFTER_ISSUE=true` opts into automatic delivery for eligible recipients; that flow also requires a payment link and matching contact details.

### Stripe payments

Follow [PAYMENTS_SETUP.md](PAYMENTS_SETUP.md) for Checkout configuration, Connect accounts, the separate webhook database role, Docker deployment, and sandbox acceptance checks. The repository includes `Dockerfile.payments` and `render.yaml` for that service.

The payment integration still requires an end-to-end deployed sandbox validation. Stable renewal of expired payment links and handling payment requests for revised statements remain release work.

### Hosted PostgreSQL and Streamlit

`scripts/bootstrap_neon.py` initializes an empty hosted database using `NEON_ADMIN_DSN` and prints the restricted runtime values for Streamlit secrets. It currently applies migrations **through `011`**; apply `012_payment_confirmation.sql` separately with administrator access before running the current application. It refuses a database that already contains application tables.

For Streamlit hosting, use `CompanyPortal.py` as the entry point. Put the generated database values and optional integration values at the top level of Streamlit secrets, **above `[auth]`**, and configure the deployed OIDC callback URL. Local `.env*` and Streamlit secrets files are excluded from Git.

## Tests

Run the unit test suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The following integration runners require Docker and create disposable PostgreSQL containers:

```bash
python scripts/run_email_delivery_tests.py
python scripts/run_payment_tests.py
```

Coverage includes allocation rules and rounding, building configuration, archiving, database access guards, statement-history performance, SMTP configuration, and payment event handling. The payment integration runner uses minimal parent-table fixtures rather than the full application schema.

`scripts/run_tenancy_tests.py` is a legacy runner that requires a separate `koinoxrista_tenancy_candidate.zip` archive; that archive is not included in a standard checkout.
