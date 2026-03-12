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

web-serve:
    uv run uvicorn comet.web.app:app --reload

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
