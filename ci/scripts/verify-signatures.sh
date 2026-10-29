#!/bin/sh
# Verify cosign signatures (and SBOM attestations) of Z images before a deploy (docs/13 §1 "deploy", §3).
# Resolves each tag to the digest that was signed and writes it out, so the deploy uses digests only.
#
#   ci/scripts/verify-signatures.sh -p REGISTRY/PREFIX -t TAG [-o digests.env] IMAGE...
#   e.g. verify-signatures.sh -p registry.example.internal/z/z -t sha-1a2b3c4 -o digests.env z-server z-runner z-ui
#
# Exactly one trust mode:
#   COSIGN_PUBLIC_KEY=path            key pair (GitLab mirror: public key in a protected file variable)
#   COSIGN_CERT_IDENTITY_REGEXP=...   keyless (images built by GitHub Actions), with COSIGN_CERT_OIDC_ISSUER
#                                     (default https://token.actions.githubusercontent.com), e.g.
#     '^https://github\.com/K3E9X/Z/\.github/workflows/images\.yml@refs/(heads/main|tags/v[0-9.]+)$'
# COSIGN_IGNORE_TLOG=1 (key mode only): the signatures were made without a transparency log (an internal
# mirror that must not publish its digests to the public Rekor); the key is then the only trust root.
# VERIFY_SBOM=0 skips the SPDX attestation check (default: required). Registry credentials come from the
# usual docker config (`cosign login` / DOCKER_CONFIG). The output file holds lines `z_server=sha256:...`.
set -eu

die() { echo "verify-signatures: $*" >&2; exit 1; }

prefix="" tag="" out=""
while getopts p:t:o: opt; do
  case "$opt" in
    p) prefix=${OPTARG%/} ;;
    t) tag=$OPTARG ;;
    o) out=$OPTARG ;;
    *) die "usage: $0 -p REGISTRY/PREFIX -t TAG [-o FILE] IMAGE..." ;;
  esac
done
shift $((OPTIND - 1))
if [ -z "$prefix" ] || [ -z "$tag" ] || [ "$#" -eq 0 ]; then
  die "usage: $0 -p REGISTRY/PREFIX -t TAG [-o FILE] IMAGE..."
fi
command -v cosign >/dev/null 2>&1 || die "cosign not found (ci/scripts/install-scanners.sh cosign)"

if [ -n "${COSIGN_PUBLIC_KEY:-}" ] && [ -n "${COSIGN_CERT_IDENTITY_REGEXP:-}" ]; then
  die "set COSIGN_PUBLIC_KEY or COSIGN_CERT_IDENTITY_REGEXP, not both"
elif [ -n "${COSIGN_PUBLIC_KEY:-}" ]; then
  [ -s "$COSIGN_PUBLIC_KEY" ] || die "COSIGN_PUBLIC_KEY ($COSIGN_PUBLIC_KEY) is empty or missing"
elif [ -z "${COSIGN_CERT_IDENTITY_REGEXP:-}" ]; then
  die "no trust root: set COSIGN_PUBLIC_KEY or COSIGN_CERT_IDENTITY_REGEXP"
elif [ "${COSIGN_IGNORE_TLOG:-0}" = 1 ]; then
  die "COSIGN_IGNORE_TLOG applies to key mode only (keyless needs the transparency log)"
fi
issuer=${COSIGN_CERT_OIDC_ISSUER:-https://token.actions.githubusercontent.com}

trusted() { # cosign subcommand + args, with the trust options of the selected mode
  sub=$1
  shift
  if [ -n "${COSIGN_PUBLIC_KEY:-}" ] && [ "${COSIGN_IGNORE_TLOG:-0}" = 1 ]; then
    cosign "$sub" --key "$COSIGN_PUBLIC_KEY" --insecure-ignore-tlog=true "$@"
  elif [ -n "${COSIGN_PUBLIC_KEY:-}" ]; then
    cosign "$sub" --key "$COSIGN_PUBLIC_KEY" "$@"
  else
    cosign "$sub" --certificate-identity-regexp "$COSIGN_CERT_IDENTITY_REGEXP" \
      --certificate-oidc-issuer "$issuer" "$@"
  fi
}

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
[ -z "$out" ] || : > "$out"
for image in "$@"; do
  printf '%s' "$image" | grep -Eq '^[a-z0-9][a-z0-9._-]*$' || die "invalid image name $image"
  ref="$prefix/$image:$tag"
  trusted verify "$ref" > "$tmp" 2>/dev/null || die "$ref: no valid signature"
  digests=$(grep -o '"docker-manifest-digest":"sha256:[0-9a-f]\{64\}"' "$tmp" | cut -d'"' -f4 | sort -u)
  [ "$(printf '%s\n' "$digests" | grep -c .)" -eq 1 ] || die "$ref: expected one signed digest, got: $digests"
  if [ "${VERIFY_SBOM:-1}" != 0 ]; then
    trusted verify-attestation --type spdxjson "$prefix/$image@$digests" >/dev/null 2>&1 \
      || die "$prefix/$image@$digests: no valid SPDX SBOM attestation"
  fi
  echo "verified $prefix/$image@$digests ($tag)"
  [ -z "$out" ] || echo "$(echo "$image" | tr '-' '_')=$digests" >> "$out"
done
