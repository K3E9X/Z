# noc/snmp-poller#57: negative throughput on 10G core interfaces

Reporter: Hugo Martin (NOC)

Grafana shows negative inbound throughput on core-par-02 Te0/0/1 a few times per hour, then a normal value
again. The interface runs at 6 to 8 Gbit/s. The poller reads ifInOctets (Counter32) every 300 s; at that rate
the 32-bit counter wraps between two samples and `rate_bps` returns a negative value.

Expected: a wrap between two samples gives the real positive rate.
