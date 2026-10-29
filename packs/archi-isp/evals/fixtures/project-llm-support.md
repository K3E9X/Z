# Project: support reply assistant (fixture)

Purpose: help the 300 customer support agents answer tickets faster. The assistant reads the customer ticket
in Jira Service Management, summarises it and drafts a reply that the agent edits and sends.

- Data: full ticket text written by customers (names, postal addresses, contract numbers, sometimes photos of
  invoices). The project team classifies it C2.
- Hosting: the assistant web app runs on the internal k3s cluster (namespace support-assist).
- Model: for v1 the team wants to call an external SaaS LLM API "to go faster", then move to the internal
  inference cluster in v2. No data processing agreement has been reviewed yet.
- Integration: a Jira service account with read and write access to all support projects (the assistant
  posts the draft as an internal comment).
- Logging: every prompt and completion is stored in the shared Elasticsearch cluster for 2 years for quality
  analysis.
- Go-live wanted in 6 weeks.
