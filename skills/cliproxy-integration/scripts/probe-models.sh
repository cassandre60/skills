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
    --base-url)
      [[ $# -ge 2 ]] || { echo "error: --base-url requires a value" >&2; exit 2; }
      BASE_URL="$2"; shift 2 ;;
    --api-key)
      [[ $# -ge 2 ]] || { echo "error: --api-key requires a value" >&2; exit 2; }
      API_KEY="$2"; shift 2 ;;
    --model)
      [[ $# -ge 2 ]] || { echo "error: --model requires a value" >&2; exit 2; }
      PROBE_MODELS+=("$2"); shift 2 ;;
    --max-tokens)
      [[ $# -ge 2 ]] || { echo "error: --max-tokens requires a value" >&2; exit 2; }
      MAX_TOKENS="$2"; shift 2 ;;
    -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$BASE_URL" ]] || { echo "error: --base-url required" >&2; exit 2; }
[[ "$MAX_TOKENS" =~ ^[1-9][0-9]*$ ]] || { echo "error: --max-tokens must be a positive integer" >&2; exit 2; }
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

# Some gateways 401 on /models while serving chat fine; local gateways may serve
# /models with no --api-key at all. Parse .data whenever present.
if command -v jq >/dev/null && printf '%s' "$catalog" | jq -e '.data | type == "array"' >/dev/null 2>&1; then
  printf '%s' "$catalog" | jq -r '.data[] | "  \(.id // "?")\t[\(.owned_by // "?")]"' | sort
  mapfile -t ALL < <(printf '%s' "$catalog" | jq -r '.data[].id // empty')
elif [[ -z "$API_KEY" ]]; then
  echo "  (listing unavailable without --api-key - pass --api-key or --model to probe specific IDs)"
  ALL=()
else
  echo "  (listing unavailable - pass --model to probe specific IDs)"
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
  if command -v jq >/dev/null; then
    payload=$(jq -nc --arg model "$m" --argjson max_tokens "$MAX_TOKENS" \
      '{model: $model, messages: [{role: "user", content: "Reply with only: ok"}], max_tokens: $max_tokens}')
  else
    esc_m="${m//\\/\\\\}"
    esc_m="${esc_m//\"/\\\"}"
    payload="{\"model\":\"$esc_m\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with only: ok\"}],\"max_tokens\":$MAX_TOKENS}"
  fi
  body=$(curl -s --max-time 120 "${auth[@]}" -H 'Content-Type: application/json' \
    -d "$payload" \
    "$BASE_URL/chat/completions")

  if command -v jq >/dev/null; then
    verdict=$(printf '%s' "$body" | jq -r '
      if (.choices | type == "array" and length > 0) then
        "OK      " + (((.choices[0].message.content // "") | tostring)[0:24])
      elif (.error | type == "object") then
        ((.error.code // "err") | tostring) + " " + ((.error.message // "") | tostring)[0:70]
      elif .error != null then
        "err " + ((.error | tostring)[0:70])
      else
        "UNPARSED " + ((. | tostring)[0:70])
      end' 2>/dev/null)
    [[ -n "$verdict" ]] || verdict="UNPARSED ${body:0:70}"
  else
    if [[ "$body" == *'"choices"'* ]] && [[ "$body" != *'"choices":[]'* ]] && [[ "$body" != *'"choices": []'* ]]; then
      verdict="OK"
    else
      verdict="FAIL ${body:0:70}"
    fi
  fi

  case "$verdict" in OK*) ;; *) fail=1 ;; esac
  printf '  %-46s %s\n' "$m" "$verdict"
done

echo
[[ $fail -eq 0 ]] && echo "result: all models responded" || echo "result: failures present (see above)"
exit $fail