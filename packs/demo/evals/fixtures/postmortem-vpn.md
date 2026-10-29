# Post-incident review: remote-access VPN outage (fixture)

Date of incident: 2026-09-18, 07:42 to 10:05 (2 h 23 min).
Service: staff remote-access VPN, gateways vpn-gw-01 and vpn-gw-02.example.internal.

## What happened
At 07:42 every new VPN connection started to fail with a TLS handshake error.
About 1,900 staff could not connect; sessions opened before 07:42 kept working.

## Root cause
The server certificate of both gateways expired at 07:42. The renewal reminder went
to a shared mailbox that nobody reads since the team reorganisation in June.

## Resolution
The network on-call engineer issued a new certificate from the internal PKI and
deployed it on both gateways at 10:05. Connections recovered immediately.

## Follow-up actions
- Move certificate expiry alerts to the monitoring platform (owner: Network Core team).
- Add the VPN certificates to the certificate inventory.
