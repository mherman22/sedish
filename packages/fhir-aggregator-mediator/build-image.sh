#!/bin/bash
# Build the FHIR Aggregator Mediator Docker image from the upstream repo.
# Run this once before deploying the package.

set -e

REPO_URL="https://github.com/mherman22/fhir-aggregator-mediator.git"
IMAGE_NAME="fhir-aggregator-mediator:latest"
BUILD_DIR="/tmp/fhir-aggregator-mediator-build"

echo "Cloning ${REPO_URL}..."
rm -rf "${BUILD_DIR}"
git clone --depth 1 "${REPO_URL}" "${BUILD_DIR}"

echo "Building Docker image ${IMAGE_NAME}..."
docker build -t "${IMAGE_NAME}" "${BUILD_DIR}"

echo "Cleaning up..."
rm -rf "${BUILD_DIR}"

echo "Done. Image: ${IMAGE_NAME}"
