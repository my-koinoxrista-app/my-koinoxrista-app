# Streamlit Cloud + Neon + Render payments

The portal creates a permanent `/pay/<random-token>` link for each issued property statement. Render opens or reuses Stripe Checkout when the tenant follows that link. Signed Stripe notifications update Neon; the Payments tab refreshes every 15 seconds. Visiting the success page does not mark an invoice paid. There is no manual Paid/Unpaid control.

## 1. Neon

Use the existing database, companies, memberships and scope key. Do not reset or reimport the schema. First make a recoverable backup or a Neon branch. On a branch, verify migrations 001–010 are present, apply `migrations/011_payments.sql` if it has not already been applied, then apply **`migrations/012_payment_deployment.sql` once**, using the schema owner. The migration is transactional. Do not rerun 011 on an existing payment schema.

012 adds permanent checkout support, validates payment amounts against immutable issued property PDFs, removes the portal's ability to write payment statuses, and creates `koinoxrista_webhook`. It does not alter issued PDFs or existing statement totals. Existing payment amounts/statuses are preserved; audit any real payments processed by the old webhook before going live. Test and live payments must use separate databases/Neon branches: a request cannot switch Stripe mode after its first checkout.

Using an interactive owner `psql` session, set a unique password without putting it in command history:

```text
\password koinoxrista_webhook
```

Give Render the resulting **restricted role** connection URL, with `sslmode=require` (or `verify-full`). URL-encode special password characters. Never give Render the Neon owner credentials or the portal scope key. The role can execute payment functions; it cannot read building records or tenant PDFs. Retain the existing `koinoxrista_app` role for Streamlit.

## 2. Streamlit Community Cloud

Deploy the repository with entry point **`CompanyPortal.py`**, Python 3.12. `requirements.txt` installs the portal dependencies; `packages.txt` installs the Greek PDF font. Copy `deployment/streamlit-secrets.toml.example` into the Cloud Secrets editor and fill its placeholders. Preserve the existing SMTP profiles, Google authentication and company membership configuration.

The payment base URL is the **Render URL**, not the Streamlit URL. The company/account JSON maps your database company UUID to your Stripe `acct_...` ID. This server-side mapping controls which account users may activate.

Add `https://<your-portal>.streamlit.app/oauth2callback` to the Google OAuth client's authorized redirect URIs and use exactly that value in `[auth].redirect_uri`. Restart the Cloud app after changing secrets. Do not upload `.env`, `.env.compose`, `.env.tenancy`, or `.streamlit/secrets.toml` to GitHub.

## 3. Render

Create a Blueprint from this repository's `render.yaml`, or a Python Web Service with:

| Setting | Value |
| --- | --- |
| Build | `pip install -r requirements-webhook.txt` |
| Start | `gunicorn payment_webhook_server:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60` |
| Health check | `/healthz` |
| Runtime | Python 3.12 |

The Blueprint selects Render's paid **Starter** plan; creating it incurs Render charges. It has not been deployed automatically. An always-on service avoids cold-start delays on tenant payment links and webhook delivery.

Enter the keys shown in `deployment/render.env.example` in Render's Environment editor. Use the **same Neon database and company/account mapping** as the portal. Set `KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL` to the assigned HTTPS Render URL, without a path or trailing query.

Default `direct` mode supports your own Stripe account: `KOINOXRISTA_STRIPE_ACCOUNT_ID` must be the account owning the secret key, and the company's mapping must use that account. Stripe Connect is optional: use `connect` mode with a platform key and an approved mapping for each connected company, and register events for connected accounts. Do not share one account between independent companies unless that is your intended business setup.

The Render root URL returns 404 intentionally. Open `/healthz` to check that the process is running. Health is a liveness check, not proof that keys, migrations or payments work. Access logging is disabled by default to avoid logging private payment-link tokens.

## 4. Stripe (test mode first)

In Stripe Workbench, create a **snapshot webhook destination**, using your own account for direct mode, with API version **2025-02-24.acacia** and endpoint:

```text
https://<your-payment-service>.onrender.com/webhooks/stripe
```

Subscribe to:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `charge.refunded`

Copy that endpoint's `whsec_...` secret to Render. The Stripe secret key also belongs only in Render; the portal doesn't need it. Test and live destinations have different signing secrets. Unrelated events are ignored. Failed database operations return 503 so Stripe can retry.

The initial checkout offers **card payments**. A completed checkout with `payment_status=unpaid` stays unpaid until a success notification arrives. Full refunds display as refunded; partial refunds preserve paid status and show the refunded amount separately. Disputes and payments made outside this Stripe checkout are not automatically reconciled.

## 5. End-to-end acceptance

1. In a test database/branch, activate the approved Stripe account in **History → Payments** and create links for a test statement.
2. Open a property link in a private browser window (no portal login is required). Pay using Stripe test card `4242 4242 4242 4242`, a future expiry and any three-digit CVC.
3. Verify the matching property becomes **Πληρωμένο**, with the exact issued amount. The others remain unpaid. Check Stripe's event delivery received HTTP 200.
4. Resend the success event in Stripe: there must be no duplicate payment update. Reopen the email link: it must report that payment is already confirmed.
5. Test cancellation and a refund. Cancellation is not payment. Check another company cannot see the statement.
6. Only after these checks, use a clean live database/payment setup with live keys and its own live webhook secret. Do not convert synthetic test requests into real debts.

Existing issued statements can get permanent links via **Create/check payment links**. Already-sent emails retain their original text/link; resend using the updated link when necessary. Each statement revision is a separate payment request: do not issue a replacement revision for an already-collected debt without reconciling the earlier payment.

## Local verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_payments.py'
.venv/bin/python scripts/run_payment_tests.py
```

The integration runner creates and removes its own Docker PostgreSQL container with synthetic data. It never reads live secrets or connects to Neon, Stripe or SMTP. It tests database permissions, company isolation, exact amounts, concurrent checkout creation, expiry, signature processing, duplicates, delayed success, out-of-order events and refunds. Real Stripe delivery still requires the acceptance test above.

Official references: [Render web services](https://render.com/docs/web-services), [Stripe webhook setup](https://docs.stripe.com/webhooks), [Stripe Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment?payment-ui=stripe-hosted), [Streamlit secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).
