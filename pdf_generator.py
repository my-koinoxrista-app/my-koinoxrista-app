"""Analytical common-expense statements, using only engine-calculated charges.

The public create_pdf(result) API remains compatible with KoinoxristaAPP.py.
Optional source metadata is supported without changing the calculation engine.
"""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from functools import lru_cache
from io import BytesIO
import xml.etree.ElementTree as ET

from fpdf import FPDF

from expense_engine import money, number

DARK = (0, 0, 0)
MUTED = (32, 40, 32)
LINE = (209, 224, 215)
GREEN = (66, 111, 96)
PALE_GREEN = (230, 240, 233)
PAYER_LABELS = {'TENANT': 'Ενοίκου', 'OWNER': 'Ιδιοκτήτη', 'OTHER': 'Λοιπές'}


@lru_cache(maxsize=1)
def _background_svg():
    path = Path(__file__).resolve().parent / 'assets' / 'building_background.svg'
    if not path.is_file():
        return None
    root = ET.fromstring(path.read_bytes())
    # PDF supports the illustration's vector shapes and gradients, not SVG blur.
    for parent in root.iter():
        parent.attrib.pop('filter', None)
        for child in list(parent):
            if child.tag.endswith('}filter'):
                parent.remove(child)
        if parent.tag.endswith('}stop'):
            parent.attrib.setdefault('offset', '0')
    return ET.tostring(root).replace(b'ns0:', b'').replace(b'xmlns:ns0=', b'xmlns=')


def _font_paths():
    regular = [os.getenv('KOINOXRISTA_FONT', ''),
               '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
               '/System/Library/Fonts/Supplemental/Arial.ttf',
               '/Library/Fonts/Arial Unicode.ttf',
               '/Library/Fonts/Arial.ttf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    bold = [os.getenv('KOINOXRISTA_FONT_BOLD', ''),
            '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
    normal = next((p for p in regular if p and Path(p).is_file()), None)
    if normal is None:
        raise RuntimeError('Δεν βρέθηκε Unicode γραμματοσειρά. Όρισε KOINOXRISTA_FONT στο .env.')
    strong = next((p for p in bold if p and Path(p).is_file()), normal)
    return normal, strong


def _money(value):
    return f'{money(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _number(value):
    value = number(value)
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _date(value):
    if not value:
        return 'Δεν έχει δηλωθεί'
    try:
        return date.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except ValueError:
        return str(value)


def _metadata(raw):
    """Read optional existing source fields; never infer a consumption period."""
    raw = raw or {}
    metadata = raw.get('metadata') or {}
    if not isinstance(metadata, dict):
        metadata = {}
    result = dict(metadata)
    for key in ('consumption_start', 'consumption_end', 'supplier',
                'invoice_number', 'invoice_date'):
        if key not in result and key in raw:
            result[key] = raw[key]
    return result


def _share(row, building, apartment_id):
    """Describe the effective rule; the actual charge comes from the result."""
    rule = row['rule']
    if rule.type == 'WEIGHTED':
        table = building.tables[rule.table_id]
        denominator = sum((number(v) for v in table.weights.values()), Decimal(0))
        return (table.name, _number(table.weights[apartment_id]),
                _number(denominator), table.source_reference)
    if rule.type == 'EQUAL':
        numerator = '1' if apartment_id in rule.participants else '0'
        return ('Ισόποσα', numerator, str(len(rule.participants)), '')
    return ('Απευθείας χρέωση', None, None, '')


def _validate_result(result):
    """Check the engine result without recalculating any allocation."""
    ids = [a.id for a in result['building'].apartments]
    totals = {aid: Decimal('0.00') for aid in ids}
    for row in result['rows']:
        if set(row['allocations']) != set(ids):
            raise ValueError('Η κατανομή δεν καλύπτει όλες τις ιδιοκτησίες.')
        charges = {aid: money(value) for aid, value in row['allocations'].items()}
        if sum(charges.values()) != money(row['amount']):
            raise ValueError('Η δαπάνη δεν συμφωνεί με τις χρεώσεις της.')
        for aid, charge in charges.items():
            totals[aid] += charge
    if totals != result['apartment_totals'] or sum(totals.values()) != result['grand_total']:
        raise ValueError('Τα σύνολα της εκκαθάρισης δεν συμφωνούν.')


class StatementPDF(FPDF):
    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='A4')
        normal, bold = _font_paths()
        self.add_font('Greek', '', normal)
        self.add_font('Greek', 'B', bold)
        self.set_margins(13, 15, 13)
        self.set_auto_page_break(auto=True, margin=16)
        self.set_title('Αναλυτική εκκαθάριση κοινοχρήστων')
        self.set_author('Koinoxrista')

    def header(self):
        background = _background_svg()
        if background:
            self.image(BytesIO(background), x=0, y=65, w=self.w)
            # Wash out the illustration before drawing any text or figures.
            with self.local_context(fill_opacity=.94):
                self.set_fill_color(255, 255, 255)
                self.rect(0, 0, self.w, self.h, style='F')
        self.set_fill_color(*GREEN)
        self.rect(0, 0, 3, self.h, style='F')
        self.set_draw_color(*LINE)
        self.line(13, 10, 197, 10)

    def footer(self):
        self.set_y(-12)
        self.set_font('Greek', '', 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f'Koinoxrista | Αναλυτική εκκαθάριση | Σελίδα {self.page_no()}', align='C')

    def paragraph(self, value, size=8, bold=False, color=DARK):
        size = max(size, 9)
        self.set_font('Greek', 'B' if bold else '', size)
        self.set_text_color(*color)
        self.multi_cell(0, max(4.8, size * .5), str(value or ''), new_x='LMARGIN', new_y='NEXT')

    def ensure_space(self, height):
        if self.get_y() + height > self.h - self.b_margin:
            self.add_page()

    def heading(self, title):
        self.ensure_space(13)
        self.paragraph(title, 10, True)
        self.ln(1)

    def total(self, label, value, large=False):
        self.ensure_space(11)
        self.set_fill_color(*PALE_GREEN)
        self.set_text_color(*DARK)
        self.set_font('Greek', 'B', 10 if large else 9)
        self.cell(125, 9, '  ' + label, fill=True)
        self.cell(59, 9, _money(value) + ' €  ', align='R', fill=True,
                  new_x='LMARGIN', new_y='NEXT')
        self.ln(2)

    def detail(self, row, building, apartment, raw):
        self.ensure_space(38)
        self.set_draw_color(*LINE)
        self.line(13, self.get_y(), 197, self.get_y())
        self.ln(2)
        self.paragraph(row['category'], 9, True)
        if row.get('description'):
            self.paragraph(row['description'])
        meta = _metadata(raw)
        if meta.get('consumption_start') or meta.get('consumption_end'):
            self.paragraph('Περίοδος κατανάλωσης: ' +
                           _date(meta.get('consumption_start')) + ' - ' +
                           _date(meta.get('consumption_end')), 7.5, color=MUTED)
        else:
            self.paragraph('Περίοδος κατανάλωσης: Δεν έχει δηλωθεί', 7.5, color=MUTED)
        details = []
        if meta.get('supplier'):
            details.append('Προμηθευτής: ' + str(meta['supplier']))
        if meta.get('invoice_number'):
            details.append('Παραστατικό: ' + str(meta['invoice_number']))
        if meta.get('invoice_date'):
            details.append('Ημερομηνία: ' + _date(meta['invoice_date']))
        if details:
            self.paragraph(' | '.join(details), 7.5, color=MUTED)
        label, numerator, denominator, source = _share(row, building, apartment.id)
        charge = money(row['allocations'][apartment.id])
        self.paragraph('Κανόνας κατανομής: ' + label)
        if numerator is None:
            self.paragraph('Απευθείας καταχωρισμένη χρέωση: ' + _money(charge) + ' €')
        else:
            self.paragraph(f"{_money(row['amount'])} € × {numerator} / {denominator} = {_money(charge)} €")
        if source:
            self.paragraph('Πηγή πίνακα: ' + source, 7.5, color=MUTED)
        if raw.get('override_reason'):
            self.paragraph('Αιτιολογία ειδικού κανόνα: ' + str(raw['override_reason']), 7.5, color=MUTED)
        self.paragraph('Σύνολο δαπάνης: ' + _money(row['amount']) + ' €', 8)
        self.paragraph('Χρέωση ιδιοκτησίας: ' + _money(charge) + ' €', 9, True)
        self.ln(2)


def create_pdf(result, period_data=None, configuration=None, owner_names=None,
               issued_label=None, property_ids=None, tenant_names=None):
    """Render the engine result. Optional metadata does not affect amounts.

    period_data and configuration are optional for compatibility with the
    existing Streamlit call. owner_names contains optional PDF-only labels
    keyed by permanent apartment ID and never affects allocation.
    """
    _validate_result(result)
    building, period = result['building'], result['period']
    period_data = period_data or {}
    configuration = configuration or {}
    originals = {item['id']: item for item in period_data.get('expenses', [])}
    names = configuration.get('property_names', {})
    owner_names = owner_names or {}
    if not isinstance(owner_names, dict):
        raise ValueError('Τα ονόματα ιδιοκτητών πρέπει να είναι αντιστοίχιση ιδιοκτησίας και ονόματος.')
    if set(owner_names) - {a.id for a in building.apartments}:
        raise ValueError('Άγνωστη ιδιοκτησία στα ονόματα ιδιοκτητών.')
    tenant_names = tenant_names or {}
    if not isinstance(tenant_names, dict) or set(tenant_names) - {a.id for a in building.apartments}:
        raise ValueError('Μη έγκυρα ονόματα ενοίκων.')
    for value in tenant_names.values():
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError('Μη έγκυρο όνομα ενοίκου.')
    ids = {a.id for a in building.apartments}
    if property_ids is None:
        selected = ids
    else:
        if not isinstance(property_ids, (list, tuple, set)) or not property_ids:
            raise ValueError('Επίλεξε ιδιοκτησία για το PDF.')
        selected = set(property_ids)
        if len(selected) != len(property_ids) or selected - ids:
            raise ValueError('Άγνωστη ή διπλή ιδιοκτησία στο PDF.')
    pdf = StatementPDF()

    for apartment in building.apartments:
        if apartment.id not in selected:
            continue
        pdf.add_page()
        pdf.paragraph('ΑΝΑΛΥΤΙΚΗ ΕΚΚΑΘΑΡΙΣΗ', 15, True)
        pdf.paragraph(issued_label or 'Κοινόχρηστες δαπάνες | Προεπισκόπηση - μη οριστικοποιημένη', 8, color=MUTED)
        pdf.ln(3)
        pdf.heading(building.name)
        if building.address:
            pdf.paragraph(building.address)
        pdf.paragraph('Περίοδος εκκαθάρισης: ' + _date(period.start_date) +
                      ' - ' + _date(period.end_date))
        name = names.get(apartment.id, '')
        pdf.paragraph('Ιδιοκτησία: ' + apartment.code)
        if name:
            pdf.paragraph('Όνομα / περιγραφή: ' + str(name))
        owner_name = owner_names.get(apartment.id, '')
        if owner_name:
            if not isinstance(owner_name, str):
                raise ValueError('Το όνομα ιδιοκτήτη πρέπει να είναι κείμενο.')
            pdf.paragraph('Ιδιοκτήτης: ' + owner_name.strip())
        tenant_name = tenant_names.get(apartment.id, '').strip()
        if tenant_name:
            pdf.paragraph('Ένοικος: ' + tenant_name)
        details = []
        if apartment.floor is not None:
            details.append('Όροφος: ' + str(apartment.floor))
        if apartment.area_sqm is not None:
            details.append('Εμβαδόν: ' + _number(apartment.area_sqm) + ' m²')
        if details:
            pdf.paragraph(' | '.join(details), 8, color=MUTED)
        pdf.ln(4)

        groups = {payer: [] for payer in PAYER_LABELS}
        totals = {payer: Decimal('0.00') for payer in PAYER_LABELS}
        for row in result['rows']:
            payer = row['payer']
            if payer not in groups:
                raise ValueError('Μη αναγνωρισμένος υπόχρεος δαπάνης.')
            groups[payer].append(row)
            totals[payer] += money(row['allocations'][apartment.id])
        if sum(totals.values()) != result['apartment_totals'][apartment.id]:
            raise ValueError('Τα σύνολα υπόχρεων δεν συμφωνούν.')
        for payer, label in PAYER_LABELS.items():
            pdf.total('Σύνολο ' + label.lower(), totals[payer])
        pdf.total('ΣΥΝΟΛΙΚΗ ΧΡΕΩΣΗ', result['apartment_totals'][apartment.id], large=True)
        pdf.ln(2)

        for payer, label in PAYER_LABELS.items():
            if not groups[payer]:
                continue
            pdf.heading('ΑΝΑΛΥΣΗ ΧΡΕΩΣΕΩΝ ' + label.upper())
            for row in groups[payer]:
                pdf.detail(row, building, apartment, originals.get(row['expense_id'], {}))
        pdf.ensure_space(16)
        pdf.paragraph('Τα ποσά προέρχονται από τον calculation engine. Σε αναλογικές '
                      'κατανομές η τελική χρέωση περιλαμβάνει την κατανομή λεπτών '
                      'και ενδέχεται να διαφέρει από την απλή στρογγυλοποίηση του κλάσματος.',
                      7, color=MUTED)
    return bytes(pdf.output())
