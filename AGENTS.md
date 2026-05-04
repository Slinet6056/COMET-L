# COMET-L Agent Notes

Use executable sources first: `justfile`, `pyproject.toml`, `web/package.json`, `pnpm-workspace.yaml`, Maven POMs, and `.pre-commit-config.yaml`. Treat README prose as secondary when it conflicts with commands or config.

## Repo layout and entrypoints

- `main.py` is the CLI entrypoint.
  - Subcommands: `run`, `study`, `analyze-study`, `replay-defects4j`.
  - If no subcommand is given, it defaults to `run`.
- `comet/web/app.py` builds the FastAPI app.
  - It serves `/api/*` routes.
  - If `web/dist/index.html` exists, it also mounts the built SPA and serves it from `/`.
- `web/src/main.tsx` is the Vite/React browser entry.
  - `web/vite.config.ts` aliases `@` to `web/src`.
  - Vitest runs in `jsdom` and loads `web/vitest.setup.ts`.
- `java-runtime/pom.xml` is the standalone Java runtime module.
  - It builds a JAR and a fat JAR.
  - Maven compiler release is `25`.
- `pnpm-workspace.yaml` only includes `web/`.

## Package boundaries

- Python owns orchestration, configuration, execution, storage, RAG, and the FastAPI backend under `comet/`.
- Java runtime code lives in `java-runtime/` and is built separately from target projects.
- Web UI code lives in `web/` and is a separate Node workspace package.
- Example Maven projects live under `examples/` and are part of the Java formatting/check surface.

## Commands worth remembering

- Setup: `just setup`
  - Expands to `uv sync`, `pnpm --dir web install`, and `mvn clean package -f java-runtime/pom.xml`.
- Full checks: `just check`
  - Runs Python, Web, and Java checks.
- Formatting: `just format`
- Python checks: `just check-python` (`ruff check .` and `ruff format --check .`).
- Web checks: `just check-web` (Prettier only; not TypeScript typecheck).
- Java checks: `just check-java` (Spotless on `java-runtime/` and all example Maven projects).
- Java runtime rebuild: `just runtime-build`
- Web build/typecheck: `just web-build` (`tsc -b && vite build`).
- Web dev-only: `just web-dev`
- Full web console flow: `just web-build && just web-serve`
- Python tests: `just test-python <target>` or `uv run python -m pytest <target>`
- Web tests: `just test-web <file>` or `pnpm --dir web exec vitest run <file>`
- Backend API test: `just test-web-api`

## Workflow quirks that matter

- `config-init` copies `config.example.yaml` to `config.yaml`; treat `config.yaml` as local runtime config, not a source file to blindly edit or commit.
- `execution.runtime_java_home` is for the COMET-L runtime JDK; `execution.target_java_home` is for the Maven project being analyzed. Keep them separate.
- `execution.selected_java_version` is a target-project selector with supported values `8`, `11`, `17`, `21`, and `25`.
- The repo’s Docker flow assumes fixed JDKs under `/opt/jdks/jdk-*` and defaults to JDK 25 if `JAVA_HOME` is not set.
- The web console does not rely on a Vite API proxy for full end-to-end use; build the frontend and serve it from FastAPI.
- There is no root `package.json`; always run frontend commands with `pnpm --dir web ...`.
- Pre-commit runs Ruff fixes/format, `pnpm --dir web run format` for `web/`, and `just check-java` for `java-runtime/` or `examples/` changes.
- GitHub repo import / managed clone / PR flows require OAuth-related config or environment variables; local Maven-path runs do not.
- Runtime artifacts live under `state/`, `output/`, `sandbox/`, and `logs/`; these are operational directories, not source files.

## Editing and verification rules

- Keep changes surgical and aligned with the surrounding file’s style.
- Use Chinese for Python user-facing strings; keep Java comments in English; avoid emojis.
- When changing Java runtime code, rebuild it with `just runtime-build`.
- For frontend logic/type changes, run `just web-build` in addition to `just check-web`.
- For repo-wide validation, prefer the smallest relevant check first; use `just check` when changes cross Python/Web/Java boundaries.

## Existing instruction files

- Root repo instruction file: this `AGENTS.md`.
- No repo-local `.cursorrules`, `.cursor/rules/`, `.github/copilot-instructions.md`, or GitHub Actions workflows are currently present; `.github/` only contains Dependabot config.
