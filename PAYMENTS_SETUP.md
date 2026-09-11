# Stripe payment service

Implementation prepared for sandbox validation. No real charge or live deployment
has been performed. Use a separate test database/company for test payments; never
mix sandbox confirmations with real issued balances.

## Database

Back up the target database before applying `migrations/012_payment_confirmation.sql`
with an offline administrator. It requires migration 011. It creates the restricted
`koinoxrista_webhook` login role; assign a generated password offline. Do not use the
Neon owner or the Streamlit application role for this service.

Deploy the updated Streamlit code together with migration 012. Existing requests
are classified as test requests by the migration; review any existing live requests
before migration. The deployed database previously contained zero payment requests.

## Separate HTTPS service

Build using `Dockerfile.payments`. Only the two webhook Python files enter the image;
no local secrets files are copied. Set these environment variables in the hosting
service's secret settings:

- `KOINOXRISTA_WEBHOOK_DSN`: PostgreSQL URL for `koinoxrista_webhook`, with SSL required.
- `KOINOXRISTA_STRIPE_WEBHOOK_SECRET`: the endpoint signing secret from Stripe.
- `KOINOXRISTA_PAYMENT_MODE`: `test` during sandbox validation.

The host-provided `PORT` is supported. Health check: `/healthz` (process liveness,
not a database or Stripe readiness check). Terminate HTTPS at the hosting provider.

In Stripe, create a webhook destination for **connected account** events:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `charge.refunded`

Destination: `https://YOUR-PAYMENT-SERVICE/webhooks/stripe`.
Use the signing secret for this specific sandbox endpoint, not an API key.

## Streamlit

Set `KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL` to the payment service HTTPS origin,
and keep `KOINOXRISTA_STRIPE_SECRET_KEY` above `[auth]` in Streamlit Secrets.
Configure the sandbox connected account in the company payment settings only after
Stripe enables payments for that account.

## Acceptance test before real payments

Use a synthetic statement in a separate test company/database. Create a Checkout
link, complete a Stripe test payment, and verify the webhook changes only the
matching request. Repeat delivery of the same event, test an unpaid completion,
late failure, and partial/full refund. The success return page does not itself
confirm payment. Return pages never read or disclose personal payment data.

## Remaining release work

- Provision the HTTPS service and endpoint signing secret, then run the full Stripe
  sandbox flow. Local tests do not prove deployed delivery.
- Add stable email payment URLs with safe renewal of expired Checkout sessions;
  the current stored Checkout URLs expire. Do not send these as permanent monthly
  collection links until renewal is implemented and tested.
- Choose how revised statements supersede existing requests before allowing live
  collection of amended statements.
- Test/live configuration must stay separate. Existing request account and mode
  cannot be silently changed by regenerating links.

## Local tests

```sh
.venv/bin/python -m unittest discover -s tests -p test_payments.py -v
.venv/bin/python scripts/run_payment_tests.py
```

The integration runner creates and removes its own PostgreSQL 17 Docker container.
It never connects to Neon or the existing application database. It tests the actual
payment migrations against minimal parent-table fixtures, not the full app schema.

## Render sandbox deployment

`render.yaml` defines a free sandbox service using the Dockerfile above. Create a
Render account, connect the repository and create a Blueprint from that file after
these changes are pushed. Enter the two private environment values when prompted.
The free service sleeps after inactivity; do not treat it as a production payment
availability guarantee. Choose an appropriate always-running plan before live use.

References:
- https://render.com/docs/blueprint-spec
- https://render.com/docs/free
- https://docs.stripe.com/checkout/fulfillment
