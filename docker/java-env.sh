#!/usr/bin/env bash
set -euo pipefail

export COMET_JAVA_HOME_8=/opt/jdks/jdk-8
export COMET_JAVA_HOME_11=/opt/jdks/jdk-11
export COMET_JAVA_HOME_17=/opt/jdks/jdk-17
export COMET_JAVA_HOME_21=/opt/jdks/jdk-21
export COMET_JAVA_HOME_25=/opt/jdks/jdk-25
export COMET_JAVA_VERSIONS='8 11 17 21 25'
export COMET_MAVEN_HOME=/opt/maven

if [[ -z "${JAVA_HOME:-}" ]]; then
	export JAVA_HOME="${COMET_JAVA_HOME_25}"
fi

export MAVEN_HOME="${MAVEN_HOME:-${COMET_MAVEN_HOME}}"
export M2_HOME="${M2_HOME:-${MAVEN_HOME}}"

case ":${PATH}:" in
*":${JAVA_HOME}/bin:"*) ;;
*) export PATH="${JAVA_HOME}/bin:${PATH}" ;;
esac

case ":${PATH}:" in
*":${MAVEN_HOME}/bin:"*) ;;
*) export PATH="${MAVEN_HOME}/bin:${PATH}" ;;
esac

comet_print_java_homes() {
	printf 'JAVA_HOME=%s\n' "${JAVA_HOME}"
	printf 'MAVEN_HOME=%s\n' "${MAVEN_HOME}"
	printf 'M2_HOME=%s\n' "${M2_HOME}"
	printf 'COMET_MAVEN_HOME=%s\n' "${COMET_MAVEN_HOME}"
	printf 'COMET_JAVA_HOME_8=%s\n' "${COMET_JAVA_HOME_8}"
	printf 'COMET_JAVA_HOME_11=%s\n' "${COMET_JAVA_HOME_11}"
	printf 'COMET_JAVA_HOME_17=%s\n' "${COMET_JAVA_HOME_17}"
	printf 'COMET_JAVA_HOME_21=%s\n' "${COMET_JAVA_HOME_21}"
	printf 'COMET_JAVA_HOME_25=%s\n' "${COMET_JAVA_HOME_25}"
}
