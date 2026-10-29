# cpe/provisioning-api#318: MAC addresses with dashes are rejected

Reporter: Julien Faure (CPE support)

The label printed on the new Fibre box shows the MAC with dashes (`AA-BB-CC-00-11-22`). The provisioning API
answers `invalid MAC address` for that format, so the hotline has to retype it. Dashes must be accepted like
colons.
