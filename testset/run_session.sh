#!/usr/bin/env bash
# Run from the repository root. Keep a separate persistent data directory for each budget.
set -eu
if [[ $# != 2 ]]; then
    echo "Usage: bash testset/run_session.sh ENV_FILE DATA_DIRECTORY" >&2
    exit 2
fi
env_file=$1
mkdir -p "$2"
data="$(cd "$2" && pwd)"
image=${WATCHTOWER_IMAGE:-watchtower}
network="wt-session-$$"
api="$network-api"
mock="$network-mock"
root="$(pwd)"
cleanup() {
    docker rm -fv "$api" "$mock" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
docker network create "$network" >/dev/null
trap cleanup EXIT
docker run -d --name "$mock" --network "$network" --network-alias mock \
    -v "$root/mock_portal:/srv/mock_portal:ro" "$image" \
    uvicorn mock_portal.server:app --host 0.0.0.0 --port 8090 >/dev/null
docker run -d --name "$api" --network "$network" -p "127.0.0.1:${WATCHTOWER_PORT:-18080}:8080" \
    --env-file "$env_file" -e DATA_DIR=/data -e REFRESH_MINUTES=15 \
    -v "$data:/data" "$image" >/dev/null
addr="http://$(docker port "$api" 8080/tcp)"
for i in {1..45}; do
    if curl -sf "$addr/portals" >/dev/null; then break; fi
    sleep 1
done
echo "Dashboard at $addr. Data in $data."
"${PYTHON:-python}" testset/run_all.py --api "$addr" --refresh --mock-url http://mock:8090/tenders \
    --db "$data/watchtower.db"
