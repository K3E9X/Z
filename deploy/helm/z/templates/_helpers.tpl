{{/* ------------------------------------------------------------------ names */}}
{{- define "z.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "z.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* <fullname>-<component>, e.g. z-server, z-litellm */}}
{{- define "z.component" -}}
{{- printf "%s-%s" (include "z.fullname" .root) .name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "z.server.fullname" -}}{{ include "z.component" (dict "root" . "name" "server") }}{{- end -}}
{{- define "z.ui.fullname" -}}{{ include "z.component" (dict "root" . "name" "ui") }}{{- end -}}
{{- define "z.litellm.fullname" -}}{{ include "z.component" (dict "root" . "name" "litellm") }}{{- end -}}
{{- define "z.browser.fullname" -}}{{ include "z.component" (dict "root" . "name" "browser") }}{{- end -}}
{{- define "z.egress.fullname" -}}{{ include "z.component" (dict "root" . "name" "egress-proxy") }}{{- end -}}
{{- define "z.postgres.fullname" -}}{{ include "z.component" (dict "root" . "name" "postgres") }}{{- end -}}

{{/* ------------------------------------------------------------------ labels */}}
{{- define "z.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "z.labels" -}}
helm.sh/chart: {{ include "z.chart" . }}
app.kubernetes.io/name: {{ include "z.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: z
{{- end -}}

{{/* selector labels for a component: (dict "root" $ "component" "server") */}}
{{- define "z.selectorLabels" -}}
app.kubernetes.io/name: {{ include "z.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "z.componentLabels" -}}
{{ include "z.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* ------------------------------------------------------------------ images */}}
{{/* (dict "image" .Values.server.image "root" $) -> repo@digest, or repo:tag (default v<appVersion>) */}}
{{- define "z.image" -}}
{{- $img := .image -}}
{{- if $img.digest -}}
{{- printf "%s@%s" $img.repository $img.digest -}}
{{- else -}}
{{- $tag := default (printf "v%s" .root.Chart.AppVersion) $img.tag -}}
{{- if eq $tag "latest" -}}
{{- fail (printf "image %s: the tag `latest` is refused, pin a version or a digest" $img.repository) -}}
{{- end -}}
{{- printf "%s:%s" $img.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "z.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* ------------------------------------------------------------------ security contexts */}}
{{/* pod security context with an explicit uid: (dict "root" $ "uid" 10001) */}}
{{- define "z.podSecurityContext" -}}
{{- $ctx := deepCopy .root.Values.podSecurityContext -}}
{{- $_ := set $ctx "runAsUser" (int .uid) -}}
{{- $_ := set $ctx "runAsGroup" (int .uid) -}}
{{- $_ := set $ctx "fsGroup" (int .uid) -}}
{{- toYaml $ctx -}}
{{- end -}}

{{- define "z.containerSecurityContext" -}}
{{- toYaml .Values.containerSecurityContext -}}
{{- end -}}

{{/* ------------------------------------------------------------------ settings */}}
{{- define "z.publicUrl" -}}
{{- if .Values.publicUrl -}}
{{- .Values.publicUrl | trimSuffix "/" -}}
{{- else -}}
{{- printf "%s://%s" (ternary "https" "http" .Values.ingress.tls.enabled) .Values.ingress.host -}}
{{- end -}}
{{- end -}}

{{- define "z.migrateMode" -}}
{{- $mode := .Values.migrate.mode -}}
{{- if eq $mode "auto" -}}
{{- ternary "initContainer" "hook" .Values.postgres.embedded -}}
{{- else -}}
{{- $mode -}}
{{- end -}}
{{- end -}}

{{/* Memory-backed directory of the z-server container where one-time secrets are written (not logged) */}}
{{- define "z.secretsOutDir" -}}/run/z-secrets{{- end -}}

{{/* Secret holding Z_SECRET_KEY, LITELLM_MASTER_KEY, OIDC_CLIENT_SECRET, INFERENCE_API_KEY */}}
{{- define "z.secretName" -}}
{{- default (printf "%s-secrets" (include "z.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{/* Secret holding the database passwords (embedded) or DSNs (external) */}}
{{- define "z.dbSecretName" -}}
{{- default (printf "%s-db" (include "z.fullname" .)) .Values.database.existingSecret -}}
{{- end -}}

{{- define "z.postgresHost" -}}
{{- printf "%s.%s.svc" (include "z.postgres.fullname" .) .Release.Namespace -}}
{{- end -}}

{{/*
Database env for a container: (dict "root" $ "role" "app"|"migrate"|"litellm" "name" "DATABASE_URL").
Embedded: the DSN is composed from the password through Kubernetes $(VAR) expansion, so the password stays
in the Secret. External: the DSN comes from database.existingSecret.
*/}}
{{- define "z.dbEnv" -}}
{{- $root := .root -}}
{{- $secret := include "z.dbSecretName" $root -}}
{{- if $root.Values.postgres.embedded -}}
{{- $user := dict "app" $root.Values.database.appRole "migrate" "z" "litellm" "litellm" -}}
{{- $pwKey := dict "app" "Z_APP_DB_PASSWORD" "migrate" "POSTGRES_PASSWORD" "litellm" "LITELLM_DB_PASSWORD" -}}
{{- $db := dict "app" "z" "migrate" "z" "litellm" "litellm" -}}
- name: Z_DB_PASSWORD_{{ upper .role }}
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ get $pwKey .role }}
- name: {{ .name }}
  value: {{ printf "postgresql://%s:$(Z_DB_PASSWORD_%s)@%s:5432/%s" (get $user .role) (upper .role) (include "z.postgresHost" $root) (get $db .role) | quote }}
{{- else -}}
{{- $key := dict "app" $root.Values.database.keys.appUrl "migrate" $root.Values.database.keys.migrateUrl "litellm" $root.Values.database.keys.litellmUrl -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ get $key .role }}
{{- end -}}
{{- end -}}

{{/* Settings shared by z-server and the seed Job (everything `z-server` reads, see settings.py) */}}
{{- define "z.serverEnv" -}}
{{- $root := . -}}
{{- $secret := include "z.secretName" . -}}
{{- $ns := .Release.Namespace -}}
- name: Z_ENV
  value: {{ .Values.env | quote }}
{{ include "z.dbEnv" (dict "root" $root "role" "app" "name" "DATABASE_URL") }}
- name: Z_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: Z_SECRET_KEY
- name: LITELLM_MASTER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LITELLM_MASTER_KEY
- name: OIDC_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: OIDC_CLIENT_SECRET
      optional: true
- name: Z_METRICS_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: Z_METRICS_TOKEN
- name: OIDC_ISSUER_URL
  value: {{ .Values.oidc.issuerUrl | quote }}
- name: OIDC_CLIENT_ID
  value: {{ .Values.oidc.clientId | quote }}
- name: Z_AUTH_MODE
  value: {{ .Values.authMode | quote }}
- name: Z_PUBLIC_URL
  value: {{ include "z.publicUrl" . | quote }}
- name: Z_CONFIG
  value: /etc/z/z.yaml
- name: Z_PACKS
  value: {{ .Values.seed.packs | quote }}
- name: Z_ARTIFACTS_DIR
  value: {{ .Values.persistence.artifacts.mountPath | quote }}
- name: Z_LOG_LEVEL
  value: {{ .Values.server.logLevel | quote }}
- name: Z_EMBEDDING_DIM
  value: {{ .Values.database.embeddingDim | quote }}
- name: Z_SECRETS_BACKEND
  value: {{ .Values.server.secretsBackend | quote }}
- name: LITELLM_URL
  value: {{ printf "http://%s.%s.svc:%v" (include "z.litellm.fullname" .) $ns .Values.litellm.port | quote }}
- name: Z_RUNNER_LITELLM_URL
  value: {{ printf "http://%s.%s.svc:%v" (include "z.litellm.fullname" .) $ns .Values.litellm.port | quote }}
{{- if .Values.egressProxy.enabled }}
- name: EGRESS_PROXY_URL
  value: {{ printf "http://%s.%s.svc:%v" (include "z.egress.fullname" .) $ns .Values.egressProxy.port | quote }}
{{- end }}
{{- if .Values.browser.enabled }}
- name: Z_BROWSER_URL
  value: {{ printf "http://%s.%s.svc:%v" (include "z.browser.fullname" .) $ns .Values.browser.port | quote }}
- name: Z_BROWSER_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: Z_BROWSER_TOKEN
{{- end }}
- name: Z_INTERNAL_URL
  value: {{ printf "http://%s.%s.svc:%v" (include "z.server.fullname" .) $ns .Values.server.port | quote }}
- name: Z_EXECUTOR
  value: kubernetes
- name: Z_RUNNER_IMAGE
  value: {{ include "z.image" (dict "image" .Values.runners.image "root" $root) | quote }}
- name: Z_RUNNER_NAMESPACE
  value: {{ .Values.runners.namespace | quote }}
- name: Z_RUNNER_SERVICE_ACCOUNT
  value: {{ .Values.runners.serviceAccountName | quote }}
{{- with .Values.runners.runtimeClassName }}
- name: Z_RUNNER_RUNTIME_CLASS
  value: {{ . | quote }}
{{- end }}
- name: Z_RUNNER_JOB_TTL_S
  value: {{ .Values.runners.jobTTLSeconds | quote }}
- name: Z_GATEWAY_REPLICAS
  value: {{ .Values.server.replicas | quote }}
- name: Z_SHUTDOWN_GRACE_S
  value: {{ .Values.server.shutdownGraceSeconds | quote }}
{{- with (default .Values.cluster.podCIDR .Values.server.trustedProxies) }}
- name: Z_TRUSTED_PROXIES
  value: {{ . | quote }}
{{- end }}
{{- with .Values.server.clusterCIDRs }}
- name: Z_CLUSTER_CIDRS
  value: {{ . | quote }}
{{- end }}
{{- with .Values.server.outboundAllow }}
- name: Z_OUTBOUND_ALLOW
  value: {{ . | quote }}
{{- end }}
{{- with .Values.server.inferencePrometheusUrl }}
- name: INFERENCE_PROMETHEUS_URL
  value: {{ . | quote }}
{{- end }}
{{- with .Values.server.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* NetworkPolicy egress rule: DNS (dns.namespace / dns.podLabels), UDP and TCP 53 */}}
{{- define "z.dnsEgressRule" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: {{ .Values.dns.namespace }}
      podSelector:
        matchLabels:
          {{- toYaml .Values.dns.podLabels | nindent 10 }}
  ports:
    - { protocol: UDP, port: 53 }
    - { protocol: TCP, port: 53 }
{{- end -}}

{{/*
Egress proxy peers: egressProxy.egressTo (default 0.0.0.0/0). Every 0.0.0.0/0 peer gets `except` for
link-local / cloud metadata, the cluster pod and service CIDRs and RFC 1918, so the proxy cannot be used to
reach the cluster or internal networks; egressProxy.privateAllow adds private ranges back as their own peers.
*/}}
{{- define "z.egress.peers" -}}
{{- $except := list "169.254.0.0/16" -}}
{{- range (list .Values.cluster.podCIDR .Values.cluster.serviceCIDR) -}}
{{- if and . (contains "." .) }}{{ $except = append $except . }}{{ end -}}
{{- end -}}
{{- $except = concat $except (list "10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16") | uniq -}}
{{- $peers := list -}}
{{- range (default (list (dict "ipBlock" (dict "cidr" "0.0.0.0/0"))) .Values.egressProxy.egressTo) -}}
{{- $p := deepCopy . -}}
{{- if eq (dig "ipBlock" "cidr" "" $p) "0.0.0.0/0" -}}
{{- $_ := set $p.ipBlock "except" (concat (default (list) $p.ipBlock.except) $except | uniq) -}}
{{- end -}}
{{- $peers = append $peers $p -}}
{{- end -}}
{{- range .Values.egressProxy.privateAllow -}}
{{- $peers = append $peers (dict "ipBlock" (dict "cidr" .)) -}}
{{- end -}}
{{- toYaml $peers -}}
{{- end -}}

{{/* Production: LiteLLM egress must be pinned to the inference cluster (invariant 1/2) */}}
{{- define "z.validateLitellmEgress" -}}
{{- $pinned := false -}}
{{- range .Values.networkPolicy.litellmEgress -}}
{{- $rule := . -}}
{{- range (default (list) $rule.to) -}}
{{- $cidr := dig "ipBlock" "cidr" "" . -}}
{{- if has $cidr (list "0.0.0.0/0" "::/0") -}}
{{- fail (printf "env=production: networkPolicy.litellmEgress must not allow %s; list the inference cluster's CIDRs (invariant 1)" $cidr) -}}
{{- end -}}
{{- if and $cidr $rule.ports -}}
{{- $pinned = true -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not $pinned -}}
{{- fail "env=production needs networkPolicy.litellmEgress: at least one rule with an ipBlock CIDR and ports (the inferX gateway), e.g. [{to: [{ipBlock: {cidr: 10.20.0.0/16}}], ports: [{port: 443, protocol: TCP}]}]. LiteLLM is the only path to models and must reach nothing else (invariant 1)" -}}
{{- end -}}
{{- end -}}

{{/* Production: every image the release runs is pinned by digest (docs/11 §3) */}}
{{- define "z.validateDigests" -}}
{{- $v := .Values -}}
{{- $images := dict "server" $v.server.image "runners" $v.runners.image "litellm" $v.litellm.image -}}
{{- if $v.ui.enabled }}{{ $_ := set $images "ui" $v.ui.image }}{{ end -}}
{{- if $v.browser.enabled }}{{ $_ := set $images "browser" $v.browser.image }}{{ end -}}
{{- if $v.egressProxy.enabled }}{{ $_ := set $images "egressProxy" $v.egressProxy.image }}{{ end -}}
{{- if $v.postgres.embedded }}{{ $_ := set $images "postgres" $v.postgres.image }}{{ end -}}
{{- $missing := list -}}
{{- range $name, $img := $images -}}
{{- if not $img.digest }}{{ $missing = append $missing (printf "%s.image.digest" $name) }}{{ end -}}
{{- end -}}
{{- if $missing -}}
{{- fail (printf "env=production deploys images by digest only; set %s (sha256:...)" (join ", " $missing)) -}}
{{- end -}}
{{- end -}}

{{/* Validation that must stop the render (called from server-deployment.yaml) */}}
{{- define "z.validate" -}}
{{- if not (has .Values.env (list "production" "staging" "development")) -}}
{{- fail (printf "env must be production, staging or development (got %q)" .Values.env) -}}
{{- end -}}
{{- if eq .Values.env "production" -}}
{{- if not .Values.networkPolicy.enabled -}}
{{- fail "env=production: networkPolicy.enabled=false is refused (release namespace policies, docs/07 §3)" -}}
{{- end -}}
{{- if not .Values.runners.networkPolicy.enabled -}}
{{- fail "env=production: runners.networkPolicy.enabled=false is refused (agent pods must stay network-isolated, docs/07 §3)" -}}
{{- end -}}
{{- include "z.validateLitellmEgress" . -}}
{{- include "z.validateDigests" . -}}
{{- if not .Values.networkPolicy.serverEgress.enabled -}}
{{- fail "env=production: networkPolicy.serverEgress.enabled=false is refused; list what z-server must reach in networkPolicy.serverEgress.allow" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.networkPolicy.enabled .Values.networkPolicy.serverEgress.enabled (not .Values.postgres.embedded) (not .Values.networkPolicy.databaseEgress) -}}
{{- fail "postgres.embedded=false with networkPolicy.serverEgress.enabled needs networkPolicy.databaseEgress (the external Postgres address and port), or z-server cannot reach its database" -}}
{{- end -}}
{{- if .Values.server.splitComponents -}}
{{- fail "server.splitComponents=true is not implemented by this chart version; run the `all` entrypoint (false)" -}}
{{- end -}}
{{- $mode := include "z.migrateMode" . -}}
{{- if not (has $mode (list "hook" "initContainer")) -}}
{{- fail "migrate.mode must be auto, hook or initContainer" -}}
{{- end -}}
{{- if and .Values.postgres.embedded (eq $mode "hook") -}}
{{- fail "migrate.mode=hook cannot reach the embedded Postgres (it is created after pre-install hooks); use auto or initContainer" -}}
{{- end -}}
{{- if and (eq $mode "initContainer") (gt (int .Values.server.replicas) 1) -}}
{{- fail "migrate.mode=initContainer runs migrations in every z-server pod: keep server.replicas=1, or use an external Postgres with migrate.mode=hook" -}}
{{- end -}}
{{- if and (not .Values.postgres.embedded) (not .Values.database.existingSecret) -}}
{{- fail "postgres.embedded=false needs database.existingSecret (DATABASE_URL, MIGRATE_DATABASE_URL, LITELLM_DATABASE_URL)" -}}
{{- end -}}
{{- if and (gt (int .Values.server.replicas) 1) .Values.persistence.artifacts.enabled (not .Values.persistence.artifacts.existingClaim) (not (has "ReadWriteMany" .Values.persistence.artifacts.accessModes)) -}}
{{- fail "server.replicas > 1 shares the artifacts volume: set persistence.artifacts.accessModes [ReadWriteMany] (or an existingClaim that is RWX)" -}}
{{- end -}}
{{- if and .Values.secrets.existingSecret .Values.secrets.externalSecret.enabled -}}
{{- fail "secrets.existingSecret and secrets.externalSecret.enabled are exclusive" -}}
{{- end -}}
{{- if and .Values.secrets.externalSecret.enabled (not .Values.secrets.externalSecret.secretStoreRef.name) -}}
{{- fail "secrets.externalSecret.secretStoreRef.name is required" -}}
{{- end -}}
{{- end -}}

{{/* Generated value kept across upgrades: (dict "root" $ "secret" name "key" K "value" given "length" N "prefix" "") */}}
{{- define "z.keptSecretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .root.Release.Namespace .secret -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- index $existing.data .key | b64dec -}}
{{- else -}}
{{- printf "%s%s" (default "" .prefix) (randAlphaNum (int .length)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
