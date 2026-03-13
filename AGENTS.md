# AGENTS.md
This file gives repository-specific guidance to coding agents working in `COMET-L`.

## Project Snapshot
- COMET-L is a mixed Python + Java + React/Vite repository for LLM-driven test generation and mutation analysis.
- Python drives orchestration, storage, execution, knowledge retrieval, CLI, and the FastAPI backend.
- `java-runtime/` contains the Java runtime used for analysis, formatting, and Maven execution.
- `web/` contains the local web console built with React, TypeScript, Vite, and Vitest.
- `examples/` contains Maven demo targets and also acts as part of the Java formatting/test surface.

## Tooling And Environment
- Python: `>=3.11` from `pyproject.toml`; use `uv`.
- Java: Java 25 is required for `java-runtime`; target projects may still be Java 8+.
- Maven is required for `java-runtime/` and example projects.
- Web tooling uses `pnpm`.
- Prefer `just` recipes when one exists; fall back to raw commands only when needed.

## Core Commands
Setup and validation:
```bash
just setup
just check
just format
just runtime-build
just web-build
```
Direct equivalents:
```bash
uv sync
mvn clean package -f java-runtime/pom.xml
pnpm --dir web install
pnpm --dir web build
uv run ruff check .
uv run ruff format --check .
```
Run flows:
- `just run`
- `just run /path/to/project`
- `just run-debug examples/calculator-demo`
- `just run-config examples/calculator-demo config.yaml`
- `just run-budget examples/calculator-demo 500`
- `just run-parallel examples/calculator-demo 8`
- Direct CLI: `uv run python main.py --project-path examples/calculator-demo`

## Build, Lint, And Format
Repo-wide:
- `just format`
- `just check`
- `just install-hooks`
Python:
- Format: `just format-python`
- Check: `just check-python`
- Direct: `uv run ruff check --fix .`, `uv run ruff format .`
- Check-only: `uv run ruff check .`, `uv run ruff format --check .`
Web:
- Format: `just format-web`
- Check: `just check-web`
- Direct: `pnpm --dir web run format`, `pnpm --dir web run format:check`
Java:
- Format runtime + examples: `just format-java`
- Check runtime + examples: `just check-java`
- Direct runtime check: `mvn -q -f java-runtime/pom.xml spotless:check`
- Spotless also applies to `examples/calculator-demo`, `examples/mockito-demo`, and `examples/multi-file-demo`.

## Test Commands
Python:
- All tests: `uv run python -m pytest`
- Single file: `uv run python -m pytest tests/test_llm_client.py`
- Single test: `uv run python -m pytest tests/test_llm_client.py::LLMClientReasoningEnabledTest::test_reasoning_effort_is_forwarded`
- Existing helper: `just test-web-api` -> `uv run python -m unittest tests.test_web_api`
Web:
- All tests: `pnpm --dir web test`
- Single file via helper: `just test-web ResultsPage.test.tsx`
- Reliable direct single-file form: `pnpm --dir web exec vitest run src/pages/ResultsPage.test.tsx`
Java:
- `java-runtime/` has no dedicated unit-test suite; treat it as a buildable runtime module.
- Example single test class: `mvn -f examples/calculator-demo/pom.xml test -Dtest=Calculator_addTest`
- Example single test method: `mvn -f examples/calculator-demo/pom.xml test -Dtest=Calculator_addTest#testAddTwoPositiveNumbers`

## What To Run After Changes
- Python-only change: `just check-python` and the nearest `uv run python -m pytest ...`
- Web-only change: `just check-web` and the nearest `just test-web ...` or direct Vitest file run.
- Java-only change: `just check-java`; rebuild with `just runtime-build` if `java-runtime/` changed.
- Cross-cutting change: `just check`.
- If you touch `java-runtime/`, rebuild it before claiming success.

## Repository Map
- `main.py`: CLI entry point and high-level orchestration.
- `comet/agent/`: planning, state, target selection, and tool orchestration.
- `comet/config/`: Pydantic settings and runtime/path resolution.
- `comet/executor/`: Java execution, coverage parsing, mutation evaluation.
- `comet/knowledge/`: RAG storage, embeddings, chunking, and retrieval.
- `comet/web/`: FastAPI app, routes, runtime event/log handling.
- `java-runtime/`: Java analyzer, executor, formatter, and models.
- `web/`: React UI, API contracts, tests, and CSS.
- `tests/`: Python test suite, mostly `unittest` style but runnable with `pytest`.

## Cross-Cutting Style Rules
- Follow local patterns in the touched module; do not introduce a new house style.
- Keep diffs surgical; this repo spans orchestration, persistence, subprocess control, and UI.
- Preserve explicit typing in Python and TypeScript.
- Keep comments sparse and only for non-obvious logic.
- Match comment language already used nearby: Python comments/docstrings are usually Chinese; Java comments are usually English.
- Do not add emoji to code, comments, or commit messages.

## Python Style
- Use 4-space indentation and stay within Ruff's `line-length = 100`.
- Group imports as standard library, third-party, then local imports with blank lines between groups.
- Prefer `dict[str, T]`, `list[T]`, `set[T]`, and `X | None` when the file already uses them.
- Older modules still use `Optional[...]`; preserve local consistency instead of rewriting just for style.
- Pydantic models use `BaseModel` and `Field(...)`; keep validation near the schema.
- Use `@dataclass(slots=True)` for lightweight records where that pattern already appears.
- Prefer `pathlib.Path` over raw path string manipulation.
- Prefer module-level logging via `logger = logging.getLogger(__name__)` over `print`.
- Do not swallow exceptions; log context and then raise or return a clear fallback.
- Keep Python CLI/backend user-facing strings in Chinese.

## Python Testing Style
- Most tests use `unittest.TestCase` with `test_...` methods.
- Keep type annotations in tests when present.
- Prefer focused assertions and local fixtures.
- For API tests, follow the `fastapi.testclient.TestClient` pattern used in `tests/test_web_api.py`.

## Java Style
- Formatting is enforced by Spotless with `google-java-format`.
- Do not hand-format against the formatter; run Spotless instead.
- Keep imports explicit and one-per-line; Spotless removes unused imports.
- Follow standard Java naming: `PascalCase` classes, `camelCase` methods/fields, `UPPER_SNAKE_CASE` constants.
- Favor small focused classes and direct error paths near IO/command boundaries.
- Keep Java comments in English.

## TypeScript, React, And CSS Style
- Use explicit TypeScript types for API payloads and UI contracts.
- Prefer `import { value, type TypeName }` when importing local types.
- Follow existing `web/src/` style: single quotes, semicolons, functional components, and hooks.
- Keep derived view state in helpers or `useMemo` instead of repeating transformations inline.
- Preserve the existing visual language in `web/src/styles.css`; do not introduce an unrelated design system.
- User-visible UI copy in the current web app is Chinese; keep it consistent.
- CSS classes use kebab-case and component-oriented names like `run-page__hero`.

## Error Handling Expectations
- Never swallow exceptions silently.
- Prefer narrow exception handling when the failure mode is known.
- At subprocess, filesystem, HTTP, and parsing boundaries, log enough context to debug the issue.
- Use warnings for recoverable degradation and errors for hard failures.
- Do not mask a real failure with a vague success-looking fallback.

## Agent Notes
- Prefer `just` before raw commands.
- If you modify `java-runtime/`, rebuild it.
- If you modify runtime web behavior, run the relevant Vitest file.
- If you modify backend or orchestration code, run the nearest Python test file instead of only broad checks.
- There is no repository-local `.cursorrules`, `.cursor/rules/`, or `.github/copilot-instructions.md` at this time.
- Pre-commit mirrors the main checks: Ruff for Python, Prettier for `web/`, and `just check-java` for Java/example modules.

## Safe Defaults For Agents
- Start with targeted tests before broad suites.
- Do not refactor unrelated modules while fixing a bug.
- Keep cache, output, sandbox, and runtime path behavior consistent with `config.yaml` and `comet/config/settings.py`.
- When unsure about backend execution paths, inspect `comet/config/settings.py` first.
- When unsure about frontend payload shapes, inspect `web/src/lib/api.ts` first.
