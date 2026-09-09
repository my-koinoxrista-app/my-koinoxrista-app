# Second company member and sender

Target account: `nicsofikitis13@gmail.com`.
Company: Αρχική εταιρεία (`11972922-ad86-4eb6-9b68-8b11eac140c9`).

## Sign-in access

The second person signs in with Google. Until membership is granted, the portal
shows “Δεν υπάρχει εταιρική πρόσβαση”. Under “Στοιχεία ταυτότητας για τον
διαχειριστή”, copy the verified `iss` and `sub` values.

Use the existing offline `company_admin.py` enrollment tool with those values and
the company UUID above. Its administrator credentials belong only in the offline
administration environment, never in the Streamlit runtime. A typed email alone
does not grant company access. Membership gives access to the same company data.

## Sender account

The existing SMTP account remains the default. Additional accounts are configured
in `.env` using `KOINOXRISTA_SMTP_PROFILES`, keyed by company UUID. Each entry has
an `id` and an environment-variable `prefix`.

The second account uses prefix `KOINOXRISTA_NICS_`. Complete
`KOINOXRISTA_NICS_SMTP_PASSWORD` locally with that account's SMTP app password.
Do not put passwords in chat or commit `.env`.

The second account appears under **Ιστορικό → Αποστολή email → Αποστολή από**
once its configuration is complete. Each account must be authorized by its SMTP
provider to send from its configured address. Both company members can select
any configured sender belonging to this company. A company-wide Reply-To, if
set, still determines where replies go.

The final confirmation shows the selected sender. Changing sender requires a new
confirmation. Prepared messages retain their original sender; accepted or uncertain
deliveries are not automatically resent. Automatic sending after issuance continues
to use the default account; the selector applies to manual sending from history.

No emails are sent by completing these configuration fields alone.

## Checks

```bash
python3 -m unittest discover -s tests -p test_smtp_profiles.py -v
```
