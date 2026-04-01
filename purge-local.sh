#!/bin/bash

docker service rm $(docker service ls -q) 2>/dev/null || true

# Wait for all services and their containers to be fully removed before proceeding.
# docker service rm is async — containers (and their port bindings) linger briefly.
echo "Waiting for services to stop..."
while [ -n "$(docker service ls -q 2>/dev/null)" ]; do sleep 2; done
while [ -n "$(docker ps -q 2>/dev/null)" ]; do sleep 1; done

docker rm -f $(docker ps -aq) 2>/dev/null || true
docker volume prune -af
docker config rm $(docker config ls -q) 2>/dev/null || true
docker network prune -f
