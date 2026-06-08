#!/usr/bin/env bash
# Start Redis + API + Web (Docker/Podman).
# For full control use: scripts/ctl.sh
exec "$(dirname "$0")/ctl.sh" start docker
