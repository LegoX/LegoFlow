#!/usr/bin/env bash
# Source the runner-managed CI environment and register sensitive values with
# GitHub Actions before another command can echo them.

: "${CICD_SHARED:?CICD_SHARED must point to the shared CI directory}"
# shellcheck disable=SC1090
source "$CICD_SHARED/.env"

if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  _legoflow_mask_ci_value() {
    local value="${1:-}" escaped part
    [[ -n "$value" ]] || return 0
    escaped="${value//'%'/'%25'}"
    escaped="${escaped//$'\r'/'%0D'}"
    escaped="${escaped//$'\n'/'%0A'}"
    printf '::add-mask::%s\n' "$escaped"

    while IFS= read -r part; do
      [[ -n "$part" && "$part" != "$value" ]] || continue
      escaped="${part//'%'/'%25'}"
      escaped="${escaped//$'\r'/'%0D'}"
      escaped="${escaped//$'\n'/'%0A'}"
      printf '::add-mask::%s\n' "$escaped"
    done < <(printf '%s' "$value" | tr ',' '\n')
  }

  while IFS= read -r variable_name; do
    case "$variable_name" in
      *_TOKEN|*_TOKENS|*_KEY|*_SECRET|*_PASSWORD|*_CREDENTIAL|*_CREDENTIALS|PAT)
        _legoflow_mask_ci_value "${!variable_name:-}"
        ;;
    esac
  done < <(compgen -v)

  unset -f _legoflow_mask_ci_value
  unset variable_name
fi
