# Egress proxy for runners (docs/11 §1-2, docs/07): the only way out of the runners network, to the package
# mirrors in EGRESS_ALLOW (comma-separated host names, a leading dot allows subdomains). Everything else is
# denied and logged. Non-root, read-only root filesystem friendly (writes only to /tmp), no cache on disk.
ARG SQUID_IMAGE=ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c
FROM ${SQUID_IMAGE}
COPY deploy/images/egress-proxy/squid.conf.tmpl /etc/squid/squid.conf.tmpl
COPY --chmod=0755 deploy/images/egress-proxy/entrypoint.sh /usr/local/bin/z-egress-proxy
USER proxy
EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/z-egress-proxy"]
HEALTHCHECK --interval=15s --timeout=3s CMD squidclient -h 127.0.0.1 -p 3128 mgr:info >/dev/null 2>&1 || exit 1
