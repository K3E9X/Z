# noc/snmp-poller#61: uppercase hostnames are not polled

Reporter: Hugo Martin (NOC)

The new inventory export writes hostnames in uppercase (`CORE-PAR-01`). Those lines are silently skipped by
`parse_targets`, so these routers are no longer polled. Uppercase hostnames must be accepted (and returned in
lowercase as today).
