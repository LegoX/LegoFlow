#!/usr/bin/env bash
# Align CI's shared-runtime repository links with the submodule gitlinks in
# the checked-out commit. Shared runtime caches are intentionally reusable, but
# a PR that advances a submodule must never test against an older checkout.
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel)}"
SHARED_RUNTIME="${SHARED_RUNTIME:-}"

if [[ -n "${CICD_SHARED:-}" && -f "$CICD_SHARED/.env" ]]; then
  # shellcheck disable=SC1090
  source "$ROOT/.github/scripts/load_shared_ci_env.sh"
fi

git_credentials=(git)
if [[ -n "${GITHUB_TOKENS:-}" ]]; then
  export GITHUB_TOKEN="${GITHUB_TOKENS%%,*}"
fi

remove_local_path() {
  local path="$1"
  local relative="${path#"$ROOT/"}"
  bash "$ROOT/.github/scripts/remove_workspace_paths.sh" "$relative"
}
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  git_credentials=(git -c 'credential.helper=!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f')
fi

align_one() {
  local block="$1" repo="$2" pin_source canonical_block use_canonical=0
  pin_source="${3:-blocks/$block/repos/$repo}"
  canonical_block="${4:-$block}"
  local relative="blocks/$block/repos/$repo"
  local local_path="$ROOT/$relative"
  local canonical=""
  local expected actual

  expected="$(git -C "$ROOT" ls-tree HEAD -- "$pin_source" | awk '{print $3}')"
  [[ "$expected" =~ ^[0-9a-f]{40}$ ]] || {
    echo "ERROR: no gitlink found for $pin_source (required by $relative)" >&2
    return 1
  }

  actual=""
  if [[ -z "$SHARED_RUNTIME" ]]; then
    canonical="$local_path"
  else
    canonical="${SHARED_RUNTIME%/}/$canonical_block/repos/$repo"
  fi
  if [[ -n "$SHARED_RUNTIME" && ( -e "$canonical/.git" || -f "$canonical/.git" ) ]]; then
    actual="$(git -C "$canonical" rev-parse HEAD 2>/dev/null || true)"
    use_canonical=1
  elif [[ -e "$local_path/.git" || -f "$local_path/.git" ]]; then
    actual="$(git -C "$local_path" rev-parse HEAD 2>/dev/null || true)"
  fi
  if [[ "$actual" == "$expected" ]]; then
    if [[ "$use_canonical" == "1" ]]; then
      remove_local_path "$local_path"
      mkdir -p "$(dirname "$local_path")"
      ln -s "$canonical" "$local_path"
      echo "INFO: $relative uses shared runtime pin $expected"
    else
      echo "INFO: $relative already matches pin $expected"
    fi
    return 0
  fi

  echo "INFO: shared runtime pin drift for $relative (have ${actual:-<missing>}, need $expected); checking out PR gitlink"
  target_mode="$(git -C "$ROOT" ls-tree HEAD -- "$relative" | awk '{print $1}')"
  if [[ "$target_mode" == "160000" ]]; then
    remove_local_path "$local_path"
    (cd "$ROOT" && "${git_credentials[@]}" submodule update --init --recursive --force -- "$relative")
  else
    checkout_path="$local_path"
    if [[ -n "$SHARED_RUNTIME" ]]; then
      checkout_path="$canonical"
      mkdir -p "$(dirname "$checkout_path")"
    fi
    if [[ ! -e "$checkout_path/.git" && ! -f "$checkout_path/.git" ]]; then
      echo "ERROR: managed checkout missing for $relative: $checkout_path" >&2
      return 1
    fi
    git -C "$checkout_path" -c "credential.helper=!f() { echo username=x-access-token; echo password=\$GITHUB_TOKEN; }; f" \
      fetch --depth 1 origin "$expected"
    git -C "$checkout_path" checkout --detach "$expected"
    if [[ -n "$SHARED_RUNTIME" ]]; then
      remove_local_path "$local_path"
      mkdir -p "$(dirname "$local_path")"
      ln -s "$checkout_path" "$local_path"
    fi
  fi
  actual="$(git -C "$local_path" rev-parse HEAD 2>/dev/null || true)"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: $relative resolved to ${actual:-<missing>}, expected $expected" >&2
    return 1
  }
}

targets=("${@:-all}")
for target in "${targets[@]}"; do
  case "$target" in
    all)
      align_one curator legoflow-curator
      align_one tracer harbor
      align_one tracer swe_data_process blocks/trainer/repos/swe_data_process trainer
      align_one evaluator harbor
      align_one trainer LLaMA-Factory
      align_one trainer swe_data_process
      ;;
    curator)
      align_one curator legoflow-curator
      ;;
    tracer)
      align_one tracer harbor
      align_one tracer swe_data_process blocks/trainer/repos/swe_data_process trainer
      ;;
    evaluator)
      align_one evaluator harbor
      ;;
    trainer)
      align_one trainer LLaMA-Factory
      align_one trainer swe_data_process
      ;;
    *)
      echo "ERROR: unknown block for submodule alignment: $target" >&2
      exit 2
      ;;
  esac
done
