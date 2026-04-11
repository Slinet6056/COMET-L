FROM eclipse-temurin:25.0.2_10-jdk-noble AS runtime-base

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3 /usr/local/bin/pip

FROM runtime-base AS python-builder

WORKDIR /src

RUN python3 -m pip install --break-system-packages --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

FROM maven:3.9.11-eclipse-temurin-25 AS java-builder

WORKDIR /src

COPY java-runtime/pom.xml java-runtime/pom.xml
COPY java-runtime/src java-runtime/src

RUN mvn -q -f java-runtime/pom.xml -Dmaven.compiler.release=25 clean package

FROM node:22.14.0-bookworm-slim AS web-builder

WORKDIR /src/web

RUN npm install -g pnpm

COPY web/package.json ./package.json
COPY web/tsconfig*.json ./
COPY web/vite.config.ts ./vite.config.ts
COPY web/src ./src
COPY web/index.html ./index.html

RUN pnpm install --no-frozen-lockfile
RUN pnpm build

FROM eclipse-temurin:8u482-b08-jdk-noble AS jdk8
FROM eclipse-temurin:11.0.30_7-jdk-noble AS jdk11
FROM eclipse-temurin:17.0.18_8-jdk-noble AS jdk17
FROM eclipse-temurin:21.0.10_7-jdk-noble AS jdk21
FROM eclipse-temurin:25.0.2_10-jdk-noble AS jdk25

FROM runtime-base AS runtime

ENV COMET_HOME=/opt/comet-l
ENV JAVA_HOME=/opt/jdks/jdk-25
ENV UV_PYTHON=python3.12
ENV PATH=/opt/comet-l/.venv/bin:/opt/jdks/jdk-25/bin:${PATH}

WORKDIR /opt/comet-l

COPY . /opt/comet-l
COPY --from=python-builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=python-builder /src/.venv /opt/comet-l/.venv
COPY --from=java-builder /src/java-runtime/target /opt/comet-l/java-runtime/target
COPY --from=web-builder /src/web/dist /opt/comet-l/web/dist
COPY --from=jdk8 /opt/java/openjdk /opt/jdks/jdk-8
COPY --from=jdk11 /opt/java/openjdk /opt/jdks/jdk-11
COPY --from=jdk17 /opt/java/openjdk /opt/jdks/jdk-17
COPY --from=jdk21 /opt/java/openjdk /opt/jdks/jdk-21
COPY --from=jdk25 /opt/java/openjdk /opt/jdks/jdk-25

RUN chmod +x /opt/comet-l/docker/entrypoint.sh /opt/comet-l/docker/java-env.sh /opt/comet-l/docker/self-check.sh \
    && ln -sf /opt/comet-l/docker/self-check.sh /usr/local/bin/comet-docker-self-check \
    && mkdir -p /opt/comet-l/state /opt/comet-l/output /opt/comet-l/sandbox /opt/comet-l/logs

ENTRYPOINT ["/opt/comet-l/docker/entrypoint.sh"]
CMD ["uv", "run", "python", "-m", "uvicorn", "comet.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
