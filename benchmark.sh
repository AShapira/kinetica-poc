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

: "${OVERTURE_RELEASE_DIR:?Set OVERTURE_RELEASE_DIR or create .env}"
: "${BENCHMARK_DATA_DIR:?Set BENCHMARK_DATA_DIR or create .env}"

image="localhost/sedona-kinetica-benchmark:0.1.0"
config="/workspace/config/benchmark.yaml"
benchmark_git_commit="$(git rev-parse HEAD)"
benchmark_git_dirty=false
# Curated evidence is produced by the run itself; it is not benchmark source.
if [[ -n "$(git status --porcelain -- . ':(exclude)evidence/**')" ]]; then
  benchmark_git_dirty=true
fi
benchmark_image_id="$(podman image inspect --format '{{.Id}}' "${image}")"

run_container() {
  local cpuset="$1"
  shift
  local secret_args=()
  local telemetry_args=()
  if [[ -n "${KINETICA_PASSWORD_FILE:-}" ]]; then
    secret_args=(
      --volume "${KINETICA_PASSWORD_FILE}:/run/secrets/kinetica_password:ro,z"
      --env KINETICA_PASSWORD_FILE=/run/secrets/kinetica_password
    )
  fi
  if [[ -n "${BENCHMARK_EXTERNAL_TELEMETRY_DIR:-}" ]]; then
    telemetry_args=(
      --env "BENCHMARK_EXTERNAL_TELEMETRY_DIR=${BENCHMARK_EXTERNAL_TELEMETRY_DIR}"
    )
  fi
  podman run --rm \
    --network=host \
    --userns=keep-id \
    --security-opt=no-new-privileges \
    --cap-drop=all \
    --volume "${project_dir}:/workspace:Z" \
    --volume "${OVERTURE_RELEASE_DIR}:/overture:ro,z" \
    --volume "${BENCHMARK_DATA_DIR}:/benchmark-data:Z" \
    --env BENCHMARK_CONFIG="${config}" \
    --env BENCHMARK_GIT_COMMIT="${benchmark_git_commit}" \
    --env BENCHMARK_GIT_DIRTY="${benchmark_git_dirty}" \
    --env BENCHMARK_IMAGE_ID="${benchmark_image_id}" \
    --env OVERTURE_RELEASE_DIR=/overture \
    --env BENCHMARK_DATA_DIR=/benchmark-data \
    --env "KINETICA_USER=${KINETICA_USER:-admin}" \
    "${secret_args[@]}" \
    "${telemetry_args[@]}" \
    "${image}" taskset --cpu-list "${cpuset}" python -m sedona_benchmark "$@"
}

run_kinetica_with_telemetry() {
  local stamp telemetry_dir gpu_pid stats_pid result
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  telemetry_dir="${BENCHMARK_DATA_DIR}/telemetry/kinetica-${stamp}"
  install -d -m 700 "${telemetry_dir}"
  export BENCHMARK_EXTERNAL_TELEMETRY_DIR="${telemetry_dir}"
  nvidia-smi \
    --query-gpu=timestamp,index,name,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw \
    --format=csv \
    --loop-ms=500 > "${telemetry_dir}/nvidia-smi.csv" &
  gpu_pid=$!
  (
    while true; do
      date -u +%Y-%m-%dT%H:%M:%S.%3NZ
      podman stats --no-stream \
        --format '{{.Name}},{{.CPU}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}},{{.PIDS}}' \
        kinetica
      sleep 0.5
    done
  ) > "${telemetry_dir}/podman-stats.csv" &
  stats_pid=$!
  set +e
  run_container "0-27" "$@"
  result=$?
  set -e
  kill "${gpu_pid}" "${stats_pid}" 2>/dev/null || true
  wait "${gpu_pid}" "${stats_pid}" 2>/dev/null || true
  unset BENCHMARK_EXTERNAL_TELEMETRY_DIR
  return "${result}"
}

require_secret() {
  : "${KINETICA_PASSWORD_FILE:?Set KINETICA_PASSWORD_FILE for Kinetica operations}"
  [[ -f "${KINETICA_PASSWORD_FILE}" ]] || {
    echo "Kinetica password file is missing" >&2
    exit 1
  }
  mode="$(stat --format='%a' "${KINETICA_PASSWORD_FILE}")"
  [[ "${mode}" == "600" ]] || {
    echo "Kinetica password file mode must be 600, found ${mode}" >&2
    exit 1
  }
}

kinetica_is_running() {
  [[ "$(podman inspect --format '{{.State.Running}}' kinetica 2>/dev/null || true)" == "true" ]]
}

require_kinetica_stopped() {
  if kinetica_is_running; then
    echo "Stop Kinetica before measuring Sedona: DOCKER_PROGRAM=podman ./kinetica stop" >&2
    exit 1
  fi
}

pause_kinetica() {
  benchmark_restore_kinetica=false
  if kinetica_is_running; then
    DOCKER_PROGRAM=podman ./kinetica stop
    benchmark_restore_kinetica=true
  fi
}

restore_kinetica() {
  if [[ "${benchmark_restore_kinetica:-false}" == "true" ]]; then
    DOCKER_PROGRAM=podman ./kinetica start
    benchmark_restore_kinetica=false
  fi
}

run_scaling() {
  local tier="${1:-general_driving}"
  local labels=(physical_1 physical_2 physical_4 physical_8 physical_12 physical_14 logical_28)
  local sets=(0 0,2 0,2,4,6 0,2,4,6,8,10,12,14 \
    0,2,4,6,8,10,12,14,16,18,20,22 \
    0,2,4,6,8,10,12,14,16,18,20,22,24,26 0-27)
  for index in "${!labels[@]}"; do
    echo "Running ${tier} with ${labels[index]} (${sets[index]})"
    run_container "${sets[index]}" run-sedona --tier "${tier}"
  done
}

case "${1:-help}" in
  prepare)
    run_container "0-27" prepare "${2:-all}" "${@:3}"
    ;;
  run-sedona)
    require_kinetica_stopped
    run_container "${SEDONA_CPUSET:-0-27}" run-sedona "${@:2}"
    ;;
  run-scaling)
    require_kinetica_stopped
    run_scaling "${2:-general_driving}"
    ;;
  load-kinetica)
    require_secret
    run_container "0-27" load-kinetica
    ;;
  run-kinetica)
    require_secret
    run_kinetica_with_telemetry run-kinetica "${@:2}"
    ;;
  compare)
    run_container "0-27" compare
    ;;
  smoke)
    run_container "0-3" prepare all --smoke
    run_container "0-3" run-sedona --tier general_driving --smoke
    ;;
  all|reproduce)
    run_container "0-27" prepare all
    pause_kinetica
    trap restore_kinetica EXIT
    run_scaling general_driving
    run_container "0-27" run-sedona --tier arterial
    run_container "0-27" run-sedona --tier service_rural
    restore_kinetica
    trap - EXIT
    require_secret
    run_container "0-27" load-kinetica
    for tier in arterial general_driving service_rural; do
      run_kinetica_with_telemetry run-kinetica --tier "${tier}"
    done
    run_container "0-27" compare
    ;;
  *)
    echo "Usage: $0 prepare|run-sedona|run-scaling|load-kinetica|run-kinetica|compare|smoke|reproduce" >&2
    exit 2
    ;;
esac
