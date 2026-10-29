# Project: direct debit mandate management (fixture)

Summary: data class C1 (internal), low sensitivity.

Purpose: move SEPA direct debit mandate management from the legacy billing tool to a new web application.

- Users: customers (internet) sign their mandate online; back-office agents (internal) correct mandates.
- Data stored: customer name, postal address, IBAN, scanned mandate signatures, mandate history.
- Hosting: the shared web cluster in the DMZ, database on the same cluster.
- Flows: internet -> web app (HTTPS); web app -> billing (SOAP over HTTPS); nightly SFTP export to the bank.
