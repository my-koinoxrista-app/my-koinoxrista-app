"""Small production-facing webhook process: python3 payment_webhook_server.py."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from payment_webhook import WebhookError, handle_stripe_webhook


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/webhooks/stripe':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length)
            result = handle_stripe_webhook(body, self.headers.get('Stripe-Signature', ''))
            payload = ('{"status":"' + result + '"}').encode()
            self.send_response(200)
        except (WebhookError, ValueError):
            payload = b'{"error":"invalid webhook"}'
            self.send_response(400)
        except Exception:
            payload = b'{"error":"webhook unavailable"}'
            self.send_response(500)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    host = os.environ.get('KOINOXRISTA_WEBHOOK_HOST', '127.0.0.1')
    port = int(os.environ.get('KOINOXRISTA_WEBHOOK_PORT', '8787'))
    ThreadingHTTPServer((host, port), WebhookHandler).serve_forever()
