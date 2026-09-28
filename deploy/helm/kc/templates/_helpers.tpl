{{- define "kc.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .uid | default 65532 }}
  runAsGroup: {{ .uid | default 65532 }}
  fsGroup: {{ .uid | default 65532 }}
  seccompProfile:
    type: RuntimeDefault
{{- end }}

{{- define "kc.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  privileged: false
  capabilities:
    drop: ["ALL"]
{{- end }}

{{/* sp_opc -> sp-opc (DNS-safe names for per-space resources) */}}
{{- define "kc.dns" -}}
{{- replace "_" "-" . -}}
{{- end }}

{{/* Allow ingress on a port from pods with the given kc.io/component values. */}}
{{- define "kc.ingressFrom" -}}
- fromEndpoints:
    - matchExpressions:
        - key: kc.io/component
          operator: In
          values: [{{ join ", " .from }}]
  toPorts:
    - ports: [{ port: "{{ .port }}", protocol: TCP }]
{{- end }}

{{/* Client components allowed into a storage service, plus the dev test runner. */}}
{{- define "kc.clients" -}}
{{- $root := index . 0 -}}
{{- $list := index . 1 -}}
{{- if $root.Values.devTestRunner }}{{ $list = append $list "test-runner" }}{{ end -}}
{{- toJson $list -}}
{{- end }}

{{/* Env shared by sync and ingest-worker. IDs and endpoints only. */}}
{{- define "kc.appEnv" -}}
- name: KC_SPACES
  value: {{ include "kc.spaceIds" . | quote }}
- { name: KC_VAULT_ADDR, value: "http://kc-vault:8200" }
- { name: KC_NATS_URL, value: "nats://kc-nats:4222" }
- { name: KC_MONGO_HOST, value: "kc-mongodb:27017" }
- { name: KC_SA_TOKEN_PATH, value: /var/run/secrets/kc/vault-token }
- { name: HOME, value: /tmp }
{{- end }}

{{- define "kc.spaceIds" -}}
{{- $ids := list -}}
{{- range .Values.spaces }}{{ $ids = append $ids .id }}{{ end -}}
{{- join "," $ids -}}
{{- end }}

{{/* ServiceAccount token for Vault's kubernetes auth (audience-bound, short-lived). */}}
{{- define "kc.vaultTokenVolume" -}}
- name: vault-token
  projected:
    sources:
      - serviceAccountToken:
          audience: vault
          expirationSeconds: 3600
          path: vault-token
{{- end }}
