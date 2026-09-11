"""WSGI payment service. Production: gunicorn payment_webhook_server:app."""
import json
import logging
import os
from html import escape
from wsgiref.simple_server import make_server

from payment_webhook import WebhookError, handle_stripe_webhook
from public_payment import open_checkout

MAX_BODY = 1_000_000
logger = logging.getLogger(__name__)


def app(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET')

    def respond(status, body, content_type='application/json', extra=()):
        payload = body.encode('utf-8')
        start_response(status, [('Content-Type', content_type), ('Content-Length', str(len(payload))),
            ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'),
            ('X-Content-Type-Options', 'nosniff'), *extra])
        return [payload]

    def page(message, status='200 OK'):
        return respond(status, '<!doctype html><html lang="el"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Πληρωμή κοινοχρήστων</title><body style="background:#f5faf7;color:#111;'
            'font:20px system-ui;max-width:650px;margin:60px auto;padding:24px">'
            '<h1 style="color:#174e3c">Πληρωμή κοινοχρήστων</h1><p>' + escape(message) +
            '</p></body></html>', 'text/html; charset=utf-8')

    if method == 'GET' and path == '/healthz':
        return respond('200 OK', '{"status":"ok"}')
    if method == 'GET' and path in ('/payment/success', '/payment/cancelled'):
        message = ('Η διαδικασία στο Stripe ολοκληρώθηκε. Η ενημέρωση της πληρωμής γίνεται αυτόματα '
                   'μόλις επιβεβαιωθεί από το Stripe. Μπορείτε να κλείσετε αυτή τη σελίδα.'
                   if path.endswith('success') else
                   'Η διαδικασία πληρωμής ακυρώθηκε. Μπορείτε να δοκιμάσετε ξανά από τον σύνδεσμο του email.')
        return page(message)
    if method == 'GET' and path.startswith('/pay/'):
        try:
            result, url = open_checkout(path.removeprefix('/pay/'))
            if result == 'REDIRECT':
                return respond('303 See Other', '', extra=[('Location', url)])
            messages = {'PAID': 'Η πληρωμή έχει επιβεβαιωθεί. Δεν χρειάζεται νέα πληρωμή.',
                'REFUNDED': 'Έχει γίνει επιστροφή της πληρωμής. Επικοινωνήστε με την εταιρεία διαχείρισης.',
                'PENDING': 'Η πληρωμή βρίσκεται σε επεξεργασία. Δοκιμάστε ξανά σε λίγο.',
                'NOT_FOUND': 'Ο σύνδεσμος πληρωμής δεν βρέθηκε.'}
            return page(messages[result], '404 Not Found' if result == 'NOT_FOUND' else '200 OK')
        except ValueError:
            return page('Ο σύνδεσμος δεν είναι διαθέσιμος. Δοκιμάστε ξανά ή επικοινωνήστε με τη διαχείριση.', '400 Bad Request')
        except Exception as exc:
            logger.error('Checkout unavailable (%s)', type(exc).__name__)
            return page('Η υπηρεσία δεν είναι διαθέσιμη. Δοκιμάστε ξανά σε λίγο.', '503 Service Unavailable')
    if path != '/webhooks/stripe':
        return respond('404 Not Found', '{"error":"not found"}')
    if method != 'POST':
        return respond('405 Method Not Allowed', '{"error":"POST required"}', extra=[('Allow', 'POST')])
    try:
        length = int(environ.get('CONTENT_LENGTH') or '0')
        if length <= 0:
            raise WebhookError('Missing body')
        if length > MAX_BODY:
            return respond('413 Content Too Large', '{"error":"body too large"}')
        body = environ['wsgi.input'].read(length)
        if len(body) != length:
            raise WebhookError('Incomplete body')
        result = handle_stripe_webhook(body, environ.get('HTTP_STRIPE_SIGNATURE', ''))
        return respond('200 OK', json.dumps({'status': result}))
    except (WebhookError, ValueError):
        return respond('400 Bad Request', '{"error":"invalid webhook"}')
    except Exception as exc:
        # Do not log tokens, bodies, database messages, credentials or payment details.
        logger.error('Webhook unavailable (%s)', type(exc).__name__)
        return respond('503 Service Unavailable', '{"error":"webhook unavailable"}')


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    host = os.getenv('KOINOXRISTA_WEBHOOK_HOST', '127.0.0.1')
    port = int(os.getenv('PORT', os.getenv('KOINOXRISTA_WEBHOOK_PORT', '8787')))
    make_server(host, port, app).serve_forever()
