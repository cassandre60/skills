#!/usr/bin/env bash
# Probe a gateway: list what it serves, then prove each model actually answers.
#
# Answers the two questions that matter and that a config file cannot:
#   1. which model IDs does this gateway really serve?
#   2. does each one actually return a completion?
#
# Usage:
#   ./probe-models.sh --base-url http://localhost:8317/v1 --api-key sk-...
#   ./probe-models.sh --base-url ... --api-key ... --model antigravity/foo --model bar
#
# Exit codes: 0 all probed models worked | 1 at least one failed | 2 usage/connection error

set -uo pipefail

# 30 tokens by default, not 5: thinking models return null content when the budget
# truncates them, which reads as failure at max_tokens=5. Choices-present still
# counts as OK (see verdict below); the larger budget just makes `content` visible.
BASE_URL=""; API_KEY=""; PROBE_MODELS=(); MAX_TOKENS=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)  BASE_URL="${2:-}"; shift 2 ;;
    --api-key)   API_KEY="${2:-}";  shift 2 ;;
    --model)     PROBE_MODELS+=("${2:-}"); shift 2 ;;
    --max-tokens) MAX_TOKENS="${2:-5}"; shift 2 ;;
    -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$BASE_URL" ]] || { echo "error: --base-url required" >&2; exit 2; }
command -v curl >/dev/null || { echo "error: curl required" >&2; exit 2; }

# Normalise: accept host:port, with or without /v1
BASE_URL="${BASE_URL%/}"
[[ "$BASE_URL" == */v1 ]] || BASE_URL="$BASE_URL/v1"

auth=()
[[ -n "$API_KEY" ]] && auth=(-H "Authorization: Bearer $API_KEY")

# ---- 1. catalog -------------------------------------------------------------
echo "== catalog: $BASE_URL/models =="
catalog=$(curl -s --max-time 20 "${auth[@]}" "$BASE_URL/models") || {
  echo "error: gateway unreachable" >&2; exit 2; }

if [[ -n "$API_KEY" ]]; then
  # Some gateways 401 on /models while serving chat fine; don't treat that as fatal.
  if command -v jq >/dev/null && echo "$catalog" | jq -e '.data' >/dev/null 2>&1; then
    echo "$catalog" | jq -r '.data[] | "  \(.id)\t[\(.owned_by // "?")]"' | sort
    mapfile -t ALL < <(echo "$catalog" | jq -r '.data[].id')
  else
    echo "  (listing unavailable - pass --model to probe specific IDs)"
    ALL=()
  fi
else
  echo "  (no --api-key; pass one to list the catalog)"
  ALL=()
fi

# ---- 2. probe ---------------------------------------------------------------
TARGETS=("${PROBE_MODELS[@]}")
[[ ${#TARGETS[@]} -eq 0 ]] && [[ ${#ALL[@]} -gt 0 ]] && TARGETS=("${ALL[@]}")

[[ ${#TARGETS[@]} -eq 0 ]] && { echo; echo "nothing to probe"; exit 2; }

echo
echo "== probing ${#TARGETS[@]} model(s), max_tokens=$MAX_TOKENS =="
fail=0
for m in "${TARGETS[@]}"; do
  body=$(curl -s --max-time 120 "${auth[@]}" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with only: ok\"}],\"max_tokens\":$MAX_TOKENS}" \
    "$BASE_URL/chat/completions")

  if command -v jq >/dev/null; then
    verdict=$(printf '%s' "$body" | jq -r '
      if .choices then "OK      " + (((.choices[0].message.content // "") | tostring)[0:24])
      elif .error then (.error.code // "err") + " " + ((.error.message // "") | tostring)[0:70]
      else "UNPARSED " + ((. | tostring)[0:70]) end' 2>/dev/null)
  else
    [[ "$body" == *'"choices"'* ]] && verdict="OK" || verdict="FAIL ${body:0:70}"
  fi

  case "$verdict" in OK*) ;; *) fail=1 ;; esac
  printf '  %-46s %s\n' "$m" "$verdict"
done

echo
[[ $fail -eq 0 ]] && echo "result: all models responded" || echo "result: failures present (see above)"
exit $fail