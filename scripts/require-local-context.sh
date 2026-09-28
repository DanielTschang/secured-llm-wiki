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
if [[ ! "$ctx" =~ ^(kind|k3d)-[a-z0-9-]+$ ]]; then
  echo "refusing: '$ctx' is not a local kind/k3d context name" >&2
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
if [[ ! "$server" =~ ^https://(127\.0\.0\.1|localhost|\[::1\]):([0-9]+)/?$ ]]; then
  echo "refusing: context '$ctx' points at a non-local API server" >&2
  exit 5
fi
port="${BASH_REMATCH[2]}"
# For kind, the port must be the one Docker publishes for this cluster's control plane,
# so a same-named context tunnelled to a remote cluster is refused too.
if [[ "$ctx" == kind-* ]]; then
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
fi
