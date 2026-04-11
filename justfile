set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

setup: sync web-install runtime-build

sync:
    uv sync

config-init:
    cp -n config.example.yaml config.yaml

runtime-build:
    mvn clean package -f java-runtime/pom.xml

docker-build tag="comet-l:multi-jdk":
    docker build -t {{tag}} .

docker-self-check tag="comet-l:multi-jdk":
    docker run --rm {{tag}} comet-docker-self-check

docker-jdk-smoke tag="comet-l:multi-jdk":
    docker run --rm {{tag}} bash -lc 'for v in 8 11 17 21 25; do /opt/jdks/jdk-${v}/bin/java -version >/tmp/j$v.txt 2>&1 || exit 1; done'

docker-runtime-smoke tag="comet-l:multi-jdk":
    docker run --rm {{tag}} bash -lc 'uv run python -V && java -version'

docker-verify tag="comet-l:multi-jdk":
    just docker-self-check {{tag}}
    just docker-jdk-smoke {{tag}}
    just docker-runtime-smoke {{tag}}

run project="examples/calculator-demo":
    uv run python main.py --project-path {{project}}

run-debug project="examples/calculator-demo":
    uv run python main.py --project-path {{project}} --debug

run-config project config="config.yaml":
    uv run python main.py --project-path {{project}} --config {{config}}

run-budget project budget="500":
    uv run python main.py --project-path {{project}} --budget {{budget}}

run-parallel project="examples/calculator-demo" targets="8":
    uv run python main.py --project-path {{project}} --parallel --parallel-targets {{targets}}

web-install:
    pnpm --dir web install

web-build:
    pnpm --dir web build

web-dev:
    pnpm --dir web dev

web-serve port="8000":
    uv run python -m uvicorn comet.web.app:app --reload --host 0.0.0.0 --port {{port}}

test-web page="ResultsPage.test.tsx":
    pnpm --dir web test -- --run {{page}}

test-web-api:
    uv run python -m unittest tests.test_web_api

format: format-python format-web format-java

check: check-python check-web check-java

format-python:
    uv run ruff check --fix .
    uv run ruff format .

check-python:
    uv run ruff check .
    uv run ruff format --check .

format-web:
    pnpm --dir web run format

check-web:
    pnpm --dir web run format:check

format-java:
    mvn -q -f java-runtime/pom.xml spotless:apply
    mvn -q -f examples/calculator-demo/pom.xml spotless:apply
    mvn -q -f examples/mockito-demo/pom.xml spotless:apply
    mvn -q -f examples/multi-file-demo/pom.xml spotless:apply

check-java:
    mvn -q -f java-runtime/pom.xml spotless:check
    mvn -q -f examples/calculator-demo/pom.xml spotless:check
    mvn -q -f examples/mockito-demo/pom.xml spotless:check
    mvn -q -f examples/multi-file-demo/pom.xml spotless:check

install-hooks:
    uv run pre-commit install
