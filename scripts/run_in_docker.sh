#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

echo "Bringing up interceptor and mcp services..."
docker-compose up -d interceptor mcp

echo "Waiting for containers to initialize..."
sleep 3

CID_INTERCEPTOR=$(docker-compose ps -q interceptor)
if [ -z "$CID_INTERCEPTOR" ]; then
  echo "Could not find interceptor container id" >&2
  exit 1
fi

echo "Copying mitmproxy CA from interceptor container to repo: mitmproxy-ca.pem"
docker cp ${CID_INTERCEPTOR}:/root/.mitmproxy/mitmproxy-ca.pem ./mitmproxy-ca.pem || {
  echo "Warning: failed to copy CA from container" >&2
}

NETWORK=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$CID_INTERCEPTOR")
echo "Using Docker network: $NETWORK"

echo "Testing proxy+TLS by curling OpenAI from a container on the compose network..."
docker run --rm --network "$NETWORK" -v "$PWD/mitmproxy-ca.pem:/mitmproxy-ca.pem" curlimages/curl:latest \
  -sS -o /dev/null -w "HTTP_CODE:%{http_code}\n" \
  -x http://interceptor:8080 --cacert /mitmproxy-ca.pem https://api.openai.com/v1/models \
  -H "Authorization: Bearer ${OPENAI_API_KEY:-}"

echo "Running exporter in a transient container attached to the compose network so it can reach 'mcp' by service name"
docker-compose run --rm --no-deps mcp bash -lc "cd /app && pip install -e . >/tmp/pip_install.log 2>&1 || true && pip install requests >/tmp/pip_requests.log 2>&1 || true && python -m interceptor.export --mcp http://mcp:56789"

echo "Done. If the exporter succeeded, the MCP receiver will have stored messages in /app/mcp_received.jsonl inside the mcp container (and the file is mounted into the repo)."
echo "You can inspect it with:"
echo "  docker exec \\$(docker ps -qf \"name=loc-ai-storage-mcp-1\") tail -n 200 /app/mcp_received.jsonl"
