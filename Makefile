# All cluster commands pass --kube-context/--context explicitly and go through the guard.
# The ambient current-context is never used.
CLUSTER      ?= kc
# Validate the raw, unexpanded value with make functions only: no shell, no eval, so a
# crafted CLUSTER can neither run commands nor smuggle extra kubectl/helm flags.
_CLUSTER_RAW := $(value CLUSTER)
_ALLOWED := a b c d e f g h i j k l m n o p q r s t u v w x y z 0 1 2 3 4 5 6 7 8 9 -
_strip = $(if $1,$(call _strip,$(wordlist 2,$(words $1),$1),$(subst $(firstword $1),,$2)),$2)
ifneq ($(words $(_CLUSTER_RAW)),1)
$(error CLUSTER must match [a-z0-9-]+)
endif
ifneq ($(call _strip,$(_ALLOWED),$(_CLUSTER_RAW)),)
$(error CLUSTER must match [a-z0-9-]+)
endif
KUBE_CONTEXT := kind-$(CLUSTER)
NAMESPACE    ?= kc
CILIUM_VERSION ?= 1.18.2
MOCK_IMAGE   ?= kc/mock-platform:dev
GUARD        := scripts/require-local-context.sh
KUBECTL      := kubectl --context $(KUBE_CONTEXT)
HELM         := helm --kube-context $(KUBE_CONTEXT)

.PHONY: test leak leak-infra eval lint kind-up kind-down deploy images

test:
	uv run pytest packages services

leak:
	uv run pytest tests/leak -m "leak or infra" -rs

leak-infra: ; $(GUARD) $(KUBE_CONTEXT)
	KC_REQUIRE_INFRA=1 KC_KUBE_CONTEXT=$(KUBE_CONTEXT) uv run pytest tests/leak/infra -m infra -rs

eval:
	@echo "no eval yet: quality evaluation starts at M2 (gold/slides.json)"

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run lint-imports

kind-up:
	$(GUARD) $(KUBE_CONTEXT) --allow-missing
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --name $(CLUSTER) --config deploy/kind/kind-config.yaml
	$(GUARD) $(KUBE_CONTEXT)
	helm repo add cilium https://helm.cilium.io >/dev/null 2>&1 || true
	helm repo update cilium >/dev/null
	$(HELM) upgrade --install cilium cilium/cilium --version $(CILIUM_VERSION) \
	  --namespace kube-system --set ipam.mode=kubernetes --set image.pullPolicy=IfNotPresent \
	  --set operator.replicas=1 --wait --timeout 10m
	$(MAKE) deploy

MINIO_TAG    ?= RELEASE.2025-10-15T17-29-55Z
MINIO_IMAGE  := kc/minio:$(MINIO_TAG)
GENERATED    := deploy/.generated/values.yaml
HELM_KC       = $(HELM) upgrade --install kc deploy/helm/kc --namespace $(NAMESPACE) --wait --timeout 10m

images:
	docker build -f services/mock-platform/Dockerfile -t $(MOCK_IMAGE) .
	docker image inspect $(MINIO_IMAGE) >/dev/null 2>&1 || \
	  docker build --build-arg MINIO_TAG=$(MINIO_TAG) -t $(MINIO_IMAGE) deploy/images/minio

# Phase 1: infra (Vault, MinIO, MongoDB, Neo4j). Bootstrap writes secrets to Vault and
# hashes to $(GENERATED). Phase 2: NATS and applications. Then the NATS stream.
deploy: images
	$(GUARD) $(KUBE_CONTEXT)
	kind load docker-image $(MOCK_IMAGE) $(MINIO_IMAGE) --name $(CLUSTER)
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) label namespace $(NAMESPACE) --overwrite \
	  pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest
	$(HELM_KC) $(if $(wildcard $(GENERATED)),-f $(GENERATED))
	uv run python scripts/dev_bootstrap.py infra --context $(KUBE_CONTEXT)
	$(HELM_KC) -f $(GENERATED)
	uv run python scripts/dev_bootstrap.py post --context $(KUBE_CONTEXT)

# Vault runs in dev mode (in memory): if it restarts, keys are gone. Recreate the cluster.
kind-down:
	$(GUARD) $(KUBE_CONTEXT) --allow-missing
	kind delete cluster --name $(CLUSTER)
	rm -f $(GENERATED)
