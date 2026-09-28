#!/usr/bin/env bash
# Refuse to touch any cluster that is not a local kind/k3d dev cluster.
# Usage: require-local-context.sh <context> [--allow-missing]
# The ambient current-context is never used.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != "--allow-missing" ) ]]; then
  echo "refusing: usage: $0 <context> [--allow-missing]" >&2
  exit 2
fi
ctx="$1"
if [[ ! "$ctx" =~ ^kind-[a-z0-9-]+$ ]]; then
  # k3d is refused until it gets the same published-port check as kind.
  echo "refusing: '$ctx' is not a local kind context name" >&2
  exit 3
fi
[[ "${2:-}" == "--allow-missing" ]] && exit 0

if ! kubectl config get-contexts -o name 2>/dev/null | grep -qx -- "$ctx"; then
  echo "refusing: context '$ctx' does not exist; run 'make kind-up'" >&2
  exit 4
fi
cluster="$(kubectl config view -o jsonpath="{.contexts[?(@.name==\"$ctx\")].context.cluster}")"
server="$(kubectl config view -o jsonpath="{.clusters[?(@.name==\"$cluster\")].cluster.server}")"
# Whole-URL match: no userinfo, path tricks, or other hosts.
if [[ ! "$server" =~ ^https://127\.0\.0\.1:([0-9]+)/?$ ]]; then
  echo "refusing: context '$ctx' points at a non-local API server" >&2
  exit 5
fi
port="${BASH_REMATCH[1]}"
# kind binds 127.0.0.1 only (apiServerAddress). The port must be the one Docker
# publishes for this cluster's control plane, so a same-named tunnel is refused too.
name="${ctx#kind-}"
if ! kind get clusters 2>/dev/null | grep -qx -- "$name"; then
  echo "refusing: no local kind cluster named '$name'" >&2
  exit 6
fi
published="$(docker port "${name}-control-plane" 6443/tcp 2>/dev/null | head -1 | sed 's/.*://')"
if [[ "$published" != "$port" ]]; then
  echo "refusing: '$ctx' API port does not match the local kind control plane" >&2
  exit 7
fi
