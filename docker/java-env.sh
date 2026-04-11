#!/usr/bin/env bash
set -euo pipefail

export COMET_JAVA_HOME_8=/opt/jdks/jdk-8
export COMET_JAVA_HOME_11=/opt/jdks/jdk-11
export COMET_JAVA_HOME_17=/opt/jdks/jdk-17
export COMET_JAVA_HOME_21=/opt/jdks/jdk-21
export COMET_JAVA_HOME_25=/opt/jdks/jdk-25
export COMET_JAVA_VERSIONS='8 11 17 21 25'

if [[ -z "${JAVA_HOME:-}" ]]; then
	export JAVA_HOME="${COMET_JAVA_HOME_25}"
fi

case ":${PATH}:" in
*":${JAVA_HOME}/bin:"*) ;;
*) export PATH="${JAVA_HOME}/bin:${PATH}" ;;
esac

comet_print_java_homes() {
	printf 'JAVA_HOME=%s\n' "${JAVA_HOME}"
	printf 'COMET_JAVA_HOME_8=%s\n' "${COMET_JAVA_HOME_8}"
	printf 'COMET_JAVA_HOME_11=%s\n' "${COMET_JAVA_HOME_11}"
	printf 'COMET_JAVA_HOME_17=%s\n' "${COMET_JAVA_HOME_17}"
	printf 'COMET_JAVA_HOME_21=%s\n' "${COMET_JAVA_HOME_21}"
	printf 'COMET_JAVA_HOME_25=%s\n' "${COMET_JAVA_HOME_25}"
}
