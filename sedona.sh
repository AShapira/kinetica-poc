#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${project_dir}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${OVERTURE_RELEASE_DIR:?Set OVERTURE_RELEASE_DIR or create .env from .env.example}"
: "${BENCHMARK_DATA_DIR:?Set BENCHMARK_DATA_DIR or create .env from .env.example}"

image="localhost/sedona-kinetica-benchmark:0.1.0"
db_container="sedonadb-benchmark"
spark_container="sedonaspark-learning"

remove_container() {
  local name="$1"
  if podman container exists "${name}"; then
    podman rm --force "${name}" >/dev/null
  fi
}

case "${1:-help}" in
  build)
    podman build \
      --build-arg "APP_UID=$(id -u)" \
      --build-arg "APP_GID=$(id -g)" \
      --file containers/Containerfile.sedonadb \
      --tag "${image}" .
    ;;
  start-db)
    remove_container "${db_container}"
    podman run --detach \
      --name "${db_container}" \
      --userns=keep-id \
      --security-opt=no-new-privileges \
      --cap-drop=all \
      --publish "127.0.0.1:${SEDONADB_JUPYTER_PORT:-8889}:8888" \
      --volume "${project_dir}:/workspace:Z" \
      --volume "${OVERTURE_RELEASE_DIR}:/overture:ro,z" \
      --volume "${BENCHMARK_DATA_DIR}:/benchmark-data:Z" \
      --env BENCHMARK_CONFIG=/workspace/config/benchmark.yaml \
      --env OVERTURE_RELEASE_DIR=/overture \
      --env BENCHMARK_DATA_DIR=/benchmark-data \
      "${image}" taskset --cpu-list "${SEDONA_CPUSET:-0-27}" \
        python -m sedona_benchmark serve
    ;;
  start-spark)
    remove_container "${spark_container}"
    install -d -m 700 "${project_dir}/runtime/spark-home"
    podman run --detach \
      --name "${spark_container}" \
      --userns=keep-id \
      --security-opt=no-new-privileges \
      --cap-drop=all \
      --publish "127.0.0.1:${SEDONASPARK_JUPYTER_PORT:-8890}:8888" \
      --publish "127.0.0.1:4040:4040" \
      --volume "${project_dir}/notebooks:/opt/workspace/notebooks:Z" \
      --volume "${project_dir}/runtime/spark-home:/opt/workspace/.local:Z" \
      --volume "${OVERTURE_RELEASE_DIR}:/overture:ro,z" \
      --volume "${BENCHMARK_DATA_DIR}:/benchmark-data:Z" \
      --env BENCHMARK_DATA_DIR=/benchmark-data \
      --env BENCHMARK_REPO_DIR=/opt/workspace \
      --env HOME=/opt/workspace/.local \
      --env "PYSPARK_SUBMIT_ARGS=--driver-memory ${SEDONASPARK_DRIVER_MEM:-6g} pyspark-shell" \
      docker.io/apache/sedona:1.9.0 \
      jupyter lab \
        --ip=0.0.0.0 \
        --port=8888 \
        --no-browser \
        --ServerApp.token= \
        --ServerApp.password=
    ;;
  stop)
    remove_container "${db_container}"
    remove_container "${spark_container}"
    ;;
  status)
    podman ps --all \
      --filter "name=${db_container}" \
      --filter "name=${spark_container}" \
      --format "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"
    ;;
  smoke)
    podman run --rm \
      --userns=keep-id \
      --security-opt=no-new-privileges \
      --cap-drop=all \
      --volume "${project_dir}:/workspace:Z" \
      --volume "${OVERTURE_RELEASE_DIR}:/overture:ro,z" \
      --volume "${BENCHMARK_DATA_DIR}:/benchmark-data:Z" \
      --env BENCHMARK_CONFIG=/workspace/config/benchmark.yaml \
      --env OVERTURE_RELEASE_DIR=/overture \
      --env BENCHMARK_DATA_DIR=/benchmark-data \
      "${image}" python -m sedona_benchmark doctor
    ;;
  *)
    echo "Usage: $0 build|start-db|start-spark|stop|status|smoke" >&2
    exit 2
    ;;
esac
