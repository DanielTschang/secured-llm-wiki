#!/usr/bin/env bash
# Refuse to touch any cluster that is not a local kind/k3d dev cluster.
# Usage: require-local-context.sh <context>   (never falls back to current-context)
set -euo pipefail
ctx="${1:-}"
if [[ -z "$ctx" ]]; then
  echo "refusing: no kube context given (current-context is never used)" >&2
  exit 2
fi
if [[ ! "$ctx" =~ ^(kind|k3d)-[a-z0-9-]+$ ]]; then
  echo "refusing: '$ctx' is not a local kind/k3d context" >&2
  exit 3
fi
if [[ "${2:-}" != "--allow-missing" ]]; then
  if ! kubectl config get-contexts -o name 2>/dev/null | grep -qx "$ctx"; then
    echo "refusing: context '$ctx' does not exist; run 'make kind-up'" >&2
    exit 4
  fi
  cluster="$(kubectl config view -o jsonpath="{.contexts[?(@.name==\"$ctx\")].context.cluster}")"
  server="$(kubectl config view -o jsonpath="{.clusters[?(@.name==\"$cluster\")].cluster.server}")"
  if [[ ! "$server" =~ ^https://(127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0): ]]; then
    echo "refusing: context '$ctx' points at a non-local API server" >&2
    exit 5
  fi
fi
