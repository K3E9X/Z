# EBIOS RM study context: managed SD-WAN for business customers (fixture)

Perimeter: the managed SD-WAN offer (orchestrator, controllers, customer edge devices) for 1,200 business
customers.

Business values: customer site connectivity; customer configuration changes on request; service supervision.

Supporting assets: SD-WAN orchestrator (vendor appliance, OSS zone), controllers, customer edge devices,
the B2B ticketing tool, the vendor support access.

Security baseline (current state):

| Measure | Status |
|---|---|
| Admin access to the orchestrator through the bastion | in place |
| MFA on orchestrator admin accounts | not in place (local accounts) |
| Edge device firmware updates | manual, last campaign 14 months ago |
| Vendor support access | permanent VPN, not logged |
| Orchestrator configuration backup | daily |
