#!/usr/bin/env bash
set -euo pipefail

source "${COMET_HOME:-/opt/comet-l}/docker/java-env.sh"

cd /opt/comet-l
comet_print_java_homes

for version in ${COMET_JAVA_VERSIONS}; do
	java_home_var="COMET_JAVA_HOME_${version}"
	"${!java_home_var}/bin/java" -version >/dev/null 2>&1
done

uv run python -V
java -version
