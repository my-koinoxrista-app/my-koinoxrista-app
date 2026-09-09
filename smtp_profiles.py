"""Company-scoped SMTP accounts configured by the operator, outside the database."""
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from dotenv import dotenv_values
from database import current_tenant

ENV_FILE = Path(__file__).resolve().parent / '.env'
_selected = ContextVar('smtp_sender_profile', default='default')


def profiles():
    values = dict(os.environ)
    if ENV_FILE.is_file():
        values.update({k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None})
    raw = values.get('KOINOXRISTA_SMTP_PROFILES', '{}')
    try:
        configured = json.loads(raw)
        if not isinstance(configured, dict):
            raise ValueError
        rows = configured.get(current_tenant(), [])
        if not isinstance(rows, list):
            raise ValueError
        result = {}
        for row in rows:
            pid, prefix = row['id'], row['prefix']
            if (not isinstance(pid, str) or not pid or pid == 'default' or pid in result
                    or not isinstance(prefix, str) or not re.fullmatch(r'KOINOXRISTA_[A-Z0-9_]+_', prefix)):
                raise ValueError
            result[pid] = {key: values.get(prefix + key, '') for key in (
                'SMTP_HOST', 'SMTP_PORT', 'SMTP_SECURITY', 'SMTP_USERNAME',
                'SMTP_PASSWORD', 'SMTP_FROM_EMAIL', 'SMTP_FROM_NAME')}
        return result
    except (ValueError, TypeError, KeyError):
        raise ValueError('Μη έγκυρη ρύθμιση λογαριασμών αποστολής της εταιρείας.') from None


def selected_values():
    pid = _selected.get()
    if pid == 'default':
        return {}
    available = profiles()
    if pid not in available:
        raise ValueError('Ο λογαριασμός αποστολής δεν είναι διαθέσιμος στην εταιρεία.')
    return {'KOINOXRISTA_' + key: value for key, value in available[pid].items()}


@contextmanager
def sender_scope(profile_id):
    if profile_id != 'default' and profile_id not in profiles():
        raise ValueError('Ο λογαριασμός αποστολής δεν είναι διαθέσιμος στην εταιρεία.')
    token = _selected.set(profile_id)
    try:
        yield
    finally:
        _selected.reset(token)
