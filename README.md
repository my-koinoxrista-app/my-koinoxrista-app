# Βήμα 3 — Πίνακες και κανόνες κατανομής

## Εγκατάσταση
Αντικατάστησε τα υπάρχοντα `expense_engine.py` και `configuration.py` με τα πλήρη αρχεία του ZIP. Πρόσθεσε το `allocation_configuration.py` στον βασικό φάκελο και το `test_allocation_configuration.py` στον υπάρχοντα φάκελο `tests/`. Δεν χρειάζεται να αντιγράψεις ολόκληρο το ZIP στον φάκελο του project.

Δεν αλλάζουμε `facilities.py`, PostgreSQL, repository, schema, Streamlit ή RAG. Τα παλιά JSONB παραμένουν αναγνώσιμα. Μην διαγράψεις υπάρχοντα δεδομένα ή volumes.

## Τι προστίθεται
- `AllocationTable.source_reference`: προαιρετικό πεδίο στο domain model για συμβατότητα με παλιά δεδομένα. Υποχρεωτικό κατά τη δημιουργία νέου πίνακα μέσω `configure_table`.
- `configure_table`: δημιουργία/ενημέρωση πίνακα με ρητό αναμενόμενο άθροισμα, μη αρνητικά βάρη και ακριβή συμφωνία όλων των ιδιοκτησιών.
- `configure_category`: σύνδεση κατηγορίας με ρητό κανόνα και υπόχρεο τύπο TENANT/OWNER/OTHER.
- `table_rule`, `equal_rule`, `direct_rule`: βοηθητικές συναρτήσεις για τους ήδη υποστηριζόμενους τύπους κανόνων.
- `CATEGORY_TEMPLATES`: ονόματα κατηγοριών, χωρίς αυτόματη ανάθεση χιλιοστών ή υπόχρεου.

Η πηγή είναι αναφορά που καταχωρίζει ο χρήστης, όχι επαλήθευση της εγκυρότητας ενός εγγράφου. Δεν υλοποιούνται ακόμη workflow έγκρισης, ιστορικό εκδόσεων ή ειδικός υπολογισμός θέρμανσης. Η ενημέρωση πίνακα στη μνήμη δεν αποθηκεύει από μόνη της αλλαγές στην PostgreSQL.

## Παράδειγμα με εικονικά δεδομένα
```python
from allocation_configuration import configure_table, configure_category, table_rule

building = configure_table(
    building,
    table_id='general',
    name='Γενικά χιλιοστά',
    weights={'a1': '600', 'a2': '400'},
    expected_total='1000',
    source_reference='Εικονικός πίνακας δοκιμής',
)
building = configure_category(
    building,
    category_id='cleaning',
    name='Καθαριότητα',
    rule=table_rule('general'),
    payer='TENANT',
)
```

## Έλεγχος
```bash
python3 -m unittest discover -s tests -v
```

Εκτελέστηκαν 33 δοκιμές σε απομονωμένο περιβάλλον Python 3.13: 23 προηγούμενες και 10 νέες. Όλες πέρασαν. Δεν έχει εκτελεστεί σύνδεση στη δική σου PostgreSQL ή το τοπικό Streamlit.

## Hosted πληρωμές

Μετά τη migration `011_payments.sql`, ρύθμισε στο deployment:

```bash
export KOINOXRISTA_STRIPE_SECRET_KEY='...'
export KOINOXRISTA_STRIPE_WEBHOOK_SECRET='...'
export KOINOXRISTA_PAYMENT_PUBLIC_BASE_URL='https://payments.example.com'
```

Η εταιρεία αποθηκεύει το δικό της Stripe Connect account id από το Ιστορικό → Πληρωμές. Μετά την έκδοση δημιουργείται ένα payment request ανά ιδιοκτησία, το email περιλαμβάνει το ατομικό PDF και κουμπί `Πληρωμή online`, και το status αλλάζει μόνο από verified Stripe webhook. Το webhook endpoint είναι `POST /webhooks/stripe` στον `payment_webhook_server.py`.
