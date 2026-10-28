# ADR 0007 — One Helm chart for k3s and Kubernetes, one Compose file for Docker

Status: accepted

## Context
The team runs k3s; other teams may run vanilla Kubernetes; developers need a local stack.

## Decision
Ship `deploy/helm/z` with `values-k3s.yaml` and `values-k8s.yaml`, and `deploy/compose/docker-compose.yml`. Code uses only portable APIs; executors abstract Docker vs Kubernetes.

## Consequences
CI must test both a k3d and a kind install. Distribution specifics (ingress class, storage, gVisor) stay in values.
