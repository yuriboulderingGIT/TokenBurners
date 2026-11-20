#!/usr/bin/env bash
# Run from the repository root with pytest on PATH or PYTHON pointing to the project venv.
set -eu
image=${1:-watchtower}
network=wt-contract-$$
root="$(pwd)"
mock="$network-mock"
model="$network-model"
api="$network-api"

cleanup() {
    docker rm -fv "$api" "$mock" "$model" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
docker network create "$network" >/dev/null
trap cleanup EXIT
docker run -d --name "$mock" --network "$network" \
    -v "$root/mock_portal:/srv/mock_portal:ro" "$image" \
    uvicorn mock_portal.server:app --host 0.0.0.0 --port 8090 >/dev/null
docker run -d --name "$model" --network "$network" \
    -v "$root/tests:/srv/tests:ro" -v "$root/testset/fixtures/mock:/srv/testset/fixtures/mock:ro" \
    "$image" uvicorn tests.contract_fixture:app --host 0.0.0.0 --port 8091 >/dev/null
docker run -d --name "$api" --network "$network" -p 127.0.0.1::8080 \
    -e OPENAI_API_KEY=ci-fixture -e OPENAI_BASE_URL="http://$model:8091/v1" "$image" >/dev/null
addr="$(docker port "$api" 8080/tcp)"
export CONTRACT_API_URL="http://$addr" CONTRACT_PORTAL_URL="http://$mock:8090/tenders"
for i in {1..45}; do
    if curl -sf "$CONTRACT_API_URL/portals" >/dev/null; then break; fi
    sleep 1
done
"${PYTHON:-python}" -m pytest -q tests/contract_test.py::test_contract || {
    docker logs "$api"
    docker logs "$model"
    docker logs "$mock"
    exit 1
}
