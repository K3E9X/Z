# EBIOS RM study context: public recursive DNS resolvers (fixture)

Perimeter: the recursive DNS resolvers used by about 7 million fixed and mobile subscribers (anycast, 6 sites).
The company is an essential entity under NIS2 for this service.

Business values: name resolution for subscribers; availability of every subscriber internet service that
depends on it.

Supporting assets: resolver servers (open-source resolver software), anycast BGP announcements, the
configuration repository (GitLab), the operations team, query logs kept 7 days.

Security baseline (current state): DNSSEC validation enabled; response rate limiting only on 2 of 6 sites;
configuration changes deployed by CI after review; query logs contain subscriber IP addresses.
