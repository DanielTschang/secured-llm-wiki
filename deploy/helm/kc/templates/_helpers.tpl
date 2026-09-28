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
