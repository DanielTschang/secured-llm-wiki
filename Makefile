# All cluster commands pass --kube-context/--context explicitly and go through the guard.
# The ambient current-context is never used.
CLUSTER      ?= kc
KUBE_CONTEXT := kind-$(CLUSTER)
NAMESPACE    ?= kc
CILIUM_VERSION ?= 1.18.2
MOCK_IMAGE   ?= kc/mock-platform:dev
GUARD        := scripts/require-local-context.sh
KUBECTL      := kubectl --context $(KUBE_CONTEXT)
HELM         := helm --kube-context $(KUBE_CONTEXT)

.PHONY: test leak leak-infra eval lint kind-up kind-down deploy image

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

image:
	docker build -f services/mock-platform/Dockerfile -t $(MOCK_IMAGE) .

deploy: image
	$(GUARD) $(KUBE_CONTEXT)
	kind load docker-image $(MOCK_IMAGE) --name $(CLUSTER)
	$(HELM) upgrade --install kc deploy/helm/kc --namespace $(NAMESPACE) --create-namespace --wait --timeout 5m

kind-down:
	$(GUARD) $(KUBE_CONTEXT) --allow-missing
	kind delete cluster --name $(CLUSTER)
