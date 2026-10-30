#!/bin/sh
# Render the allowlist into /tmp/squid.conf and run squid in the foreground. Refuses to start without an
# allowlist: an empty list would mean "deny all", which is also what not running the proxy gives you.
set -eu
: "${EGRESS_ALLOW:?set EGRESS_ALLOW to the mirror host names (comma-separated)}"
acl=""
for host in $(echo "$EGRESS_ALLOW" | tr ',' ' '); do
  case "$host" in
    *[!A-Za-z0-9.-]*|"") echo "egress-proxy: invalid host name: $host" >&2; exit 64 ;;
  esac
  acl="$acl $host"
done
sed "s|@ALLOW@|$acl|" /etc/squid/squid.conf.tmpl > /tmp/squid.conf
exec squid -N -f /tmp/squid.conf
