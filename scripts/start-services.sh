#!/usr/bin/env bash
# Start Redis + API + Web via Docker Compose
set -e
cd "$(dirname "$0")/.."

if [ ! -f config.yaml ]; then
  echo "config.yaml not found. Copy from config.yaml.example and edit."
  exit 1
fi

docker compose up -d
echo "Services started. Web: http://localhost:3000  API: http://localhost:8080"
