#!/usr/bin/env bash
set -euo pipefail

# Simple helper to send an HTTP request through the local `interceptor` proxy
# Usage examples:
#  ./scripts/proxy_request.sh --url https://api.openai.com/v1/models --method GET
#  ./scripts/proxy_request.sh --url https://httpbin.org/post --method POST --data-file payload.json
#  ./scripts/proxy_request.sh --url https://api.openai.com/v1/chat/completions --method POST --data-file body.json --auth "Bearer $OPENAI_API_KEY"

usage(){
  cat <<EOF
Usage: $0 --url URL [--method METHOD] [--data-file FILE | --data JSON] [--header 'Key: Val'] [--auth 'Bearer TOKEN']

This runs `curl` in a short-lived container attached to the compose network so requests are routed through the interceptor proxy service by name.
If ./mitmproxy-ca.pem exists it will be copied into the helper container and used as --cacert; otherwise TLS verification is disabled (-k).
EOF
  exit 1
}

URL=""
METHOD=GET
DATA_FILE=""
DATA_PAYLOAD=""
AUTH_HEADER=""
HEADERS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2;;
    --method) METHOD="$2"; shift 2;;
    --data-file) DATA_FILE="$2"; shift 2;;
    --data) DATA_PAYLOAD="$2"; shift 2;;
    --auth) AUTH_HEADER="$2"; shift 2;;
    --header) HEADERS+=("$2"); shift 2;;
    -h|--help) usage;;
    *) echo "Unknown arg: $1" >&2; usage;;
  esac
done

if [ -z "$URL" ]; then
  usage
fi

# network name used by docker-compose
NETWORK=loc-ai-storage_default

# start a helper container and keep it alive briefly
CID=$(docker run -d --network "$NETWORK" curlimages/curl:latest sleep 300)

# copy CA if present
if [ -f ./mitmproxy-ca.pem ]; then
  docker cp ./mitmproxy-ca.pem ${CID}:/mitmproxy-ca.pem || true
  CACERT_OP=(--cacert /mitmproxy-ca.pem)
else
  # best-effort: skip verification if no CA available
  CACERT_OP=(-k)
fi

# assemble header args
HDR_ARGS=()
if [ -n "$AUTH_HEADER" ]; then
  HDR_ARGS+=( -H "$AUTH_HEADER" )
fi
for h in "${HEADERS[@]}"; do
  HDR_ARGS+=( -H "$h" )
done

# data handling
DATA_ARGS=()
if [ -n "$DATA_FILE" ]; then
  DATA_ARGS+=( --data-binary "@/app/$DATA_FILE" )
  # copy file into container
  docker cp "$DATA_FILE" ${CID}:/app/$(basename "$DATA_FILE") || true
elif [ -n "$DATA_PAYLOAD" ]; then
  DATA_ARGS+=( --data-raw "$DATA_PAYLOAD" )
fi

echo "Sending $METHOD $URL via interceptor proxy (container $CID)"

# run curl inside the helper container
docker exec ${CID} sh -c "curl -v -X '$METHOD' -x http://interceptor:8080 ${CACERT_OP[*]} ${HDR_ARGS[*]} ${DATA_ARGS[*]} '$URL'"

# clean up
docker rm -f ${CID} >/dev/null 2>&1 || true

exit 0
