# EBIOS RM study context: FTTH ordering and line activation (fixture)

Perimeter: the chain that takes an FTTH order and activates the customer line.

Missions and business processes:
- order taking (customer web shop and shops);
- line activation (push of the line profile to the OLT and the ONT);
- technician appointment scheduling with subcontracted field technicians.

Supporting assets: ordering portal (APP zone), OSS activation engine, wholesale partner API (used by other
operators that resell lines), appointment scheduling SaaS, corporate LDAP directory.

Stakeholders: wholesale operators, subcontracted field technicians, the appointment SaaS vendor.

Security baseline (current state):
- MFA for shop and call-centre agents: in place.
- MFA for OSS activation engine administrators: not in place.
- Wholesale partner API: mutual TLS, no rate limiting.
- Backups of the activation engine: weekly, restore never tested.
