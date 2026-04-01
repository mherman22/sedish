#!/bin/bash

declare ACTION=""
declare MODE=""
declare COMPOSE_FILE_PATH=""
declare UTILS_PATH=""
declare STACK="isanteplus"

# iSantePlus instances to manage — add new instances here
INSTANCES=(isanteplus isanteplus2 isanteplus3 isanteplus4)

function init_vars() {
  ACTION=$1
  MODE=$2

  COMPOSE_FILE_PATH=$(
    cd "$(dirname "${BASH_SOURCE[0]}")" || exit
    pwd -P
  )

  UTILS_PATH="${COMPOSE_FILE_PATH}/../utils"

  readonly ACTION
  readonly MODE
  readonly COMPOSE_FILE_PATH
  readonly UTILS_PATH
  readonly STACK
}

# shellcheck disable=SC1091
function import_sources() {
  source "${UTILS_PATH}/docker-utils.sh"
  source "${UTILS_PATH}/config-utils.sh"
  source "${UTILS_PATH}/log.sh"
}

# Wait for an iSantePlus instance to return HTTP 200 on its FHIR endpoint.
# Retries for up to ~15 minutes (60 attempts x 15s).
function wait_for_instance() {
  local svc="$1"
  local max_attempts=60
  local attempt=0

  while [ $attempt -lt $max_attempts ]; do
    attempt=$((attempt + 1))
    local status
    status=$(docker exec "$(docker ps -q -f name=${STACK}_${svc}.1)" \
      curl -sf -o /dev/null -w "%{http_code}" \
      "http://localhost:8080/openmrs/ws/fhir2/R4/metadata" 2>/dev/null || echo "000")

    if [ "$status" = "200" ]; then
      log info "${svc} is ready (attempt ${attempt})"
      return 0
    fi

    # If we get 500, OpenMRS had a classloader error — restart it
    if [ "$status" = "500" ] && [ $attempt -gt 20 ]; then
      log warn "${svc} returned 500 after ${attempt} attempts — restarting"
      docker service update --force "${STACK}_${svc}" >/dev/null 2>&1
    fi

    sleep 15
  done

  log error "${svc} did not become ready after $((max_attempts * 15))s"
  return 1
}

function initialize_package() {
  log info "Running package in PROD mode"

  # Deploy the stack with all services defined in docker-compose
  (
    docker::deploy_service "$STACK" "${COMPOSE_FILE_PATH}" "docker-compose.yml"
  ) || {
    log error "Failed to deploy package"
    exit 1
  }

  # Staggered startup: boot instances one at a time to prevent resource
  # starvation on single-node deployments. Each OpenMRS instance needs
  # ~2GB heap + heavy CPU during module loading — simultaneous boot causes
  # classloader failures and OOM.
  # docker-compose sets replicas: 0, so no instances start during stack deploy.
  log info "Starting ${#INSTANCES[@]} instances sequentially (staggered boot)..."

  # Start one at a time, waiting for each to be healthy
  for svc in "${INSTANCES[@]}"; do
    log info "Starting ${svc}..."
    docker service scale "${STACK}_${svc}=1" >/dev/null 2>&1

    if ! wait_for_instance "$svc"; then
      log warn "${svc} failed to start — continuing with next instance"
    fi
  done

  log info "All instances started"
}

function destroy_package() {
  docker::stack_destroy $STACK

  if [[ "${CLUSTERED_MODE}" == "true" ]]; then
    log warn "Volumes are only deleted on the host on which the command is run. Postgres volumes on other nodes are not deleted"
  fi

  docker::prune_configs "isanteplus"
}

main() {
  init_vars "$@"
  import_sources

  if [[ "${ACTION}" == "init" ]] || [[ "${ACTION}" == "up" ]]; then
    if [[ "${CLUSTERED_MODE}" == "true" ]]; then
      log info "Running package in Cluster node mode"
    else
      log info "Running package in Single node mode"
    fi

    initialize_package
  elif [[ "${ACTION}" == "down" ]]; then
    log info "Scaling down package"

    docker::scale_services "$STACK" 0
  elif [[ "${ACTION}" == "destroy" ]]; then
    log info "Destroying package"
    destroy_package
  else
    log error "Valid options are: init, up, down, or destroy"
  fi
}

main "$@"
