#!/usr/bin/env bash
set -euo pipefail

# PolicyGenerator deve estar instalado no diretório de plugins do Kustomize.
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
policy_output="$repo_root/policies/virtualization/policy-install-virtualization.yaml"
policy_tmp="$(mktemp)"
trap 'rm -f -- "$policy_tmp"' EXIT

if [[ "${1:-}" != "" && "${1:-}" != "--check" ]] || (( $# > 1 )); then
  echo "Uso: $0 [--check]" >&2
  exit 2
fi

oc kustomize "$repo_root/generators/virtualization" --enable-alpha-plugins > "$policy_tmp"
if [[ ! -s "$policy_tmp" ]]; then
  echo "O generator não produziu YAML; saída existente preservada." >&2
  exit 1
fi

if [[ "${1:-}" == "--check" ]]; then
  diff -u -- "$policy_output" "$policy_tmp"
  echo "A policy versionada corresponde ao PolicyGenerator."
else
  cp -- "$policy_tmp" "$policy_output"
  echo "Policy atualizada: policies/virtualization/policy-install-virtualization.yaml"
fi
