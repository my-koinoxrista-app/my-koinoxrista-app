"""Public payment return pages and signed webhooks; run behind HTTPS."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from payment_webhook import WebhookError, handle_stripe_webhook

MAX_BODY = 1_000_000


class WebhookHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def _reply(self, status, body, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/healthz':
            self._reply(200, b'{"status":"ok"}')
            return
        pages = {
            '/payment/success': ('Επιστροφή από την πληρωμή',
                'Η τελική κατάσταση θα ενημερωθεί μόλις ληφθεί η επιβεβαίωση από τη Stripe. '
                'Μπορείτε να κλείσετε αυτή τη σελίδα.'),
            '/payment/cancelled': ('Η διαδικασία πληρωμής ακυρώθηκε',
                'Μπορείτε να επιστρέψετε στον σύνδεσμο του email για νέα προσπάθεια. '
                'Η σελίδα αυτή δεν αλλάζει την κατάσταση της οφειλής.'),
        }
        if path not in pages:
            self._reply(404, b'{"error":"not found"}')
            return
        title, message = pages[path]
        body = (f'<!doctype html><html lang="el"><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>{title}</title><main><h1>{title}</h1><p>{message}</p></main></html>').encode()
        self._reply(200, body, 'text/html; charset=utf-8')

    def do_POST(self):
        if self.path != '/webhooks/stripe':
            self._reply(404, b'{"error":"not found"}')
            return
        try:
            if self.headers.get('Transfer-Encoding'):
                raise WebhookError('Unsupported transfer encoding')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                self._reply(413, b'{"error":"invalid body size"}')
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise WebhookError('Incomplete body')
            result = handle_stripe_webhook(body, self.headers.get('Stripe-Signature', ''))
            self._reply(200, json.dumps({'status': result}).encode())
        except (WebhookError, ValueError):
            self._reply(400, b'{"error":"invalid webhook"}')
        except Exception:
            self._reply(500, b'{"error":"webhook unavailable"}')

    def log_message(self, format, *args):
        # Requests can contain private payment tokens. Never log request URLs.
        return


if __name__ == '__main__':
    host = os.environ.get('KOINOXRISTA_WEBHOOK_HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', os.environ.get('KOINOXRISTA_WEBHOOK_PORT', '8787')))
    ThreadingHTTPServer((host, port), WebhookHandler).serve_forever()
