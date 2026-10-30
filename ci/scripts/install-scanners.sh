#!/bin/sh
# Install CI scanners from pinned, checksum-verified artifacts (docs/13 §3). Fails closed: an unknown tool, a
# missing pin, a checksum mismatch or a blocked version stops the job before anything runs.
#
#   ci/scripts/install-scanners.sh trivy gitleaks zizmor ...   # or `all-binaries`, `--list`
#
# Binary tools: ci/scripts/versions.lock (tool version arch sha256 kind url), checked with sha256sum.
# Python tools: ci/scripts/requirements/<tool>.txt, installed in their own venv with pip --require-hashes and
# binary wheels only. Installed commands land in "$SCANNERS_DIR/bin"; on GitHub Actions that directory is added
# to GITHUB_PATH, elsewhere add it to PATH yourself.
#
# Environment:
#   SCANNERS_DIR       install root (default: $RUNNER_TEMP/z-scanners, else $TMPDIR or /tmp)
#   SCANNERS_MIRROR    replaces the https://github.com/ prefix of every URL (internal artifact mirror; the
#                      pinned checksums still apply)
#   PIP_INDEX_URL      internal PyPI mirror for the Python tools (hashes still apply)
#   SCANNERS_BLOCKED_CHECKED=1  only where python3 is missing and an earlier job of the same pipeline ran
#                      ci/scripts/check_blocked.py (the GitLab `guard` job); otherwise python3 is required
set -eu

here=$(cd "$(dirname "$0")" && pwd)
lock="$here/versions.lock"
reqs="$here/requirements"
dir=${SCANNERS_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/z-scanners}
bin="$dir/bin"

die() { echo "install-scanners: $*" >&2; exit 1; }

case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) die "unsupported architecture $(uname -m)" ;;
esac

python_commands() {
  case "$1" in
    zizmor | semgrep | bandit | pip-audit | yamllint) echo "$1" ;;
    uv) echo "uv uvx" ;;
    *) return 1 ;;
  esac
}

if [ "${1:-}" = "--list" ]; then
  awk '!/^#/ && NF {print $1, $2}' "$lock" | sort -u
  for f in "$reqs"/*.txt; do
    t=$(basename "$f" .txt)
    echo "$t $(sed -n "s/^$t==\([^ ;]*\).*/\1/p" "$f") (python)"
  done
  exit 0
fi
[ "$#" -gt 0 ] || die "usage: $0 TOOL... | all-binaries | --list"
if [ "$1" = "all-binaries" ]; then
  # shellcheck disable=SC2046 # word splitting is intended: one argument per tool
  set -- $(awk '!/^#/ && NF {print $1}' "$lock" | sort -u)
fi

# Refuse blocked versions before downloading anything (a PR could pin a compromised release).
if command -v python3 >/dev/null 2>&1; then
  python3 "$here/check_blocked.py" --root "$here/../.." --lock "$lock" >/dev/null \
    || { python3 "$here/check_blocked.py" --root "$here/../.." --lock "$lock" >&2 || true; die "blocked version in $lock"; }
elif [ "${SCANNERS_BLOCKED_CHECKED:-}" != "1" ]; then
  die "python3 is needed to check $lock against security/blocked-versions.yaml"
fi

fetch() { # url dest
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-delay 2 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import shutil, sys, urllib.request
if not sys.argv[1].startswith("https://"): sys.exit("https only")
with urllib.request.urlopen(sys.argv[1], timeout=120) as r, open(sys.argv[2], "wb") as f: shutil.copyfileobj(r, f)' "$1" "$2"
  else
    die "no downloader (curl, wget or python3)"
  fi
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
trap 'exit 1' INT TERM
mkdir -p "$bin"

lock_line() { # tool -> its line for this architecture
  awk -v t="$1" -v a="$arch" '!/^#/ && $1 == t && $3 == a' "$lock"
}

install_binary() { # tool
  line=$(lock_line "$1")
  [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || die "$1: several $arch entries in $lock"
  # shellcheck disable=SC2086 # split the lock line into its fields
  set -- $line
  tool=$1 version=$2 sum=$4 kind=$5 url=$6
  printf '%s' "$sum" | grep -Eq '^[0-9a-f]{64}$' || die "$tool: malformed sha256 in $lock"
  case "$url" in https://*) ;; *) die "$tool: only https URLs are allowed" ;; esac
  if [ -n "${SCANNERS_MIRROR:-}" ]; then
    url="${SCANNERS_MIRROR%/}/${url#https://github.com/}"
  fi
  file="$tmp/$tool.download"
  fetch "$url" "$file" || die "$tool: download failed ($url)"
  echo "$sum  $file" | sha256sum -c - >/dev/null 2>&1 || {
    actual=$(sha256sum "$file" | cut -d' ' -f1)
    die "$tool $version: sha256 mismatch (expected $sum, got $actual) for $url"
  }
  case "$kind" in
    bin) cp "$file" "$tmp/$tool" ;;
    tgz:*)
      member=${kind#tgz:}
      mkdir -p "$tmp/$tool.x"
      tar -xzf "$file" -C "$tmp/$tool.x" "$member" || die "$tool: $member not found in the archive"
      cp "$tmp/$tool.x/$member" "$tmp/$tool"
      ;;
    *) die "$tool: unknown kind $kind" ;;
  esac
  chmod 0755 "$tmp/$tool"
  mv -f "$tmp/$tool" "$bin/$tool"
  echo "installed $tool $version ($arch, sha256 verified)"
}

install_python() { # tool
  commands=$(python_commands "$1")
  req="$reqs/$1.txt"
  [ -f "$req" ] || die "$1: $req is missing"
  command -v python3 >/dev/null 2>&1 || die "$1: python3 is required"
  venv="$dir/venv/$1"
  rm -rf "$venv"
  python3 -m venv "$venv" || die "$1: cannot create a venv (python3-venv missing?)"
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --no-input \
    --require-hashes --no-deps --only-binary ':all:' -r "$req" || die "$1: pip install failed (hash mismatch?)"
  for c in $commands; do
    [ -x "$venv/bin/$c" ] || die "$1: $c not installed"
    ln -sf "$venv/bin/$c" "$bin/$c"
  done
  echo "installed $1 $(sed -n "s/^$1==\([^ ;]*\).*/\1/p" "$req") (python, hashes verified)"
}

for tool in "$@"; do
  if [ -n "$(lock_line "$tool")" ]; then
    install_binary "$tool"
  elif python_commands "$tool" >/dev/null; then
    install_python "$tool"
  else
    die "$tool: not pinned for $arch in $lock, nor a Python tool in $reqs"
  fi
done

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "$bin" >> "$GITHUB_PATH"
fi
