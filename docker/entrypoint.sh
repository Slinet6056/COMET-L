#!/usr/bin/env bash
set -euo pipefail

source "${COMET_HOME:-/opt/comet-l}/docker/java-env.sh"

cd /opt/comet-l
exec "$@"
