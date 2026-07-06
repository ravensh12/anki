set windows-shell := ["pwsh", "-NoLogo", "-NoProfileLoadTime", "-Command"]

mod release

# Show available commands
default:
    @just --list

# Build the project
build:
    {{ ninja }} pylib qt

# Build and run Anki in development mode
run *args:
    {{ run_script }} {{ args }}

# Build and run Anki in optimized (release) mode
run-optimized *args:
    {{ if os() == "windows" { "$env:RELEASE='1'; .\\run.bat" } else { "RELEASE=1 ./run" } }} {{ args }}

# Watch web sources and rebuild/reload Anki's web stack on change (macOS/Linux)
web-watch:
    ./tools/web-watch

# Rebuild and reload Anki's web stack without restarting (macOS/Linux)
rebuild-web:
    ./tools/rebuild-web

# Build wheels (needed for some platforms)
wheels:
    {{ ninja }} wheels

# Ante: benchmark engine actions (p50/p95/worst) on a deck
bench deck="out/mcat_seed.anki2" iters="200":
    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.bench --deck {{ deck }} --iters {{ iters }}

# Ante: export the premade MCAT deck (curated only; per_topic>0 pads for benchmarks)
seed-deck per_topic="0" out="out/mcat_seed.anki2":
    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.generate_seed_deck --out {{ out }} --per-topic {{ per_topic }} --apkg out/mcat_seed.apkg

# Ante: run the AI eval harness (offline by default; set ANTHROPIC_API_KEY for Claude)
ai-eval *args:
    PYTHONPATH=. out/pyenv/bin/python -m ante.ai.eval {{ args }}

# Ante: run the study-feature experiment (mastery-gating vs ablation vs plain)
experiment *args:
    PYTHONPATH=. out/pyenv/bin/python -m ante.experiment {{ args }}

# Ante: the paraphrase test (spec 7d) — 30 cards x 2 reworded questions.
# With no args, runs the synthetic memorizer-vs-transfer demonstration.
paraphrase *args:
    PYTHONPATH=. out/pyenv/bin/python -m ante.paraphrase {{ args }}

# Ante: memory calibration report + reliability SVG (Brier / log-loss / ECE)
calibrate predictions="ante/data/sample_predictions.json" *args:
    PYTHONPATH=. out/pyenv/bin/python -m ante.tools.calibrate \
      --predictions {{ predictions }} --out-svg out/calibration.svg {{ args }}

# Ante: leakage scan (spec 7e) — flags test items that leaked into training data
leakage-check train test threshold="0.85":
    PYTHONPATH=. out/pyenv/bin/python -m ante.leakage \
      --train {{ train }} --test {{ test }} --threshold {{ threshold }}

# Ante: pre-render Sahir's Back Room voice for a demo (zero on-stage latency).
# Close Anki first (exclusive collection lock); needs a TTS key. Extra args
# pass through, e.g. --topics mcat::bio_biochem::enzymes --clips
warm-backroom collection *args:
    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.warm_backroom \
      --collection {{ collection }} {{ args }}

# Ante: set up the optional model/AI service venv (one-time; PRD 3.2 stack)
ante-service-setup:
    {{ uv }} venv out/ante-svc
    {{ uv }} pip install --python out/ante-svc/bin/python -r ante/service/requirements.txt

# Ante: run the model/AI service (run ante-service-setup once first)
ante-service port="8723":
    out/ante-svc/bin/uvicorn ante.service.app:app --port {{ port }}

# Ante: run a self-hosted Anki sync server (shared by desktop + phone)
sync-server port="27701" user="ante" password="ante123":
    SYNC_HOST=0.0.0.0 SYNC_PORT={{ port }} SYNC_BASE="$PWD/out/syncbase" \
      SYNC_USER1={{ user }}:{{ password }} PYTHONPATH=out/pylib:pylib \
      out/pyenv/bin/python -c "from anki._backend import RustBackend; RustBackend.syncserver()"

# Ante: re-runnable two-way sync test (PRD 11 / 7b); needs sync-server running
sync-test endpoint="http://127.0.0.1:27701/" user="ante" password="ante123":
    PYTHONPATH=out/pylib:pylib:. SYNC_ENDPOINT={{ endpoint }} SYNC_USER={{ user }} \
      SYNC_PASS={{ password }} out/pyenv/bin/python ante/tools/sync_test.py

# Ante: crash-recovery test (spec 7g) — SIGKILL mid-review N times, 0 corruption
crash-test deck="out/mcat_seed.anki2" trials="20":
    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.crash_test \
      --deck {{ deck }} --trials {{ trials }}

# Ante: run the dependency-free Ante unit tests (fast; no Anki build)
test-ante *args:
    PYTHONPATH=. out/pyenv/bin/pytest ante/tests {{ args }}

# Ante: cross-compile the shared engine and package AnkiEngine.xcframework
# for the iOS app (device + Apple-silicon simulator). Needs Xcode CLTs.
ios-engine:
    rustup target add aarch64-apple-ios aarch64-apple-ios-sim
    CARGO_TARGET_DIR=out/rust cargo build -p anki_ios_ffi --features native-tls --release --target aarch64-apple-ios
    CARGO_TARGET_DIR=out/rust cargo build -p anki_ios_ffi --features native-tls --release --target aarch64-apple-ios-sim
    nm -gU out/rust/aarch64-apple-ios/release/libanki_engine.a | grep -q _ante_backend_run
    rm -rf out/ios/AnkiEngine.xcframework
    mkdir -p out/ios
    xcodebuild -create-xcframework \
        -library out/rust/aarch64-apple-ios/release/libanki_engine.a -headers rslib/ios-ffi/include \
        -library out/rust/aarch64-apple-ios-sim/release/libanki_engine.a -headers rslib/ios-ffi/include \
        -output out/ios/AnkiEngine.xcframework
    @echo "xcframework at out/ios/AnkiEngine.xcframework"

# Ante: engine C-ABI smoke test on the host (buildhash + open a collection
# + one RPC through a plain C caller; no Xcode required)
ios-engine-smoke:
    CARGO_TARGET_DIR=out/rust cargo build -p anki_ios_ffi --features native-tls --release
    mkdir -p out
    cc rslib/ios-ffi/ctest/smoke.c -Irslib/ios-ffi/include \
        out/rust/release/libanki_engine.a \
        -framework Security -framework CoreFoundation -framework SystemConfiguration \
        -framework IOKit \
        -liconv -o out/ios_ffi_smoke
    ./out/ios_ffi_smoke

# Ante: exercise the iOS app's PRODUCTION Swift engine path (hand-rolled
# protobuf codec + FFI bridge + SyncedEngine) against the host-built engine.
# With SYNC_ENDPOINT/SYNC_USER/SYNC_PASS set and a sync server running, it
# also full-downloads the den, answers a card, and syncs it back — the exact
# call sequence the phone runs.
ios-swift-smoke:
    CARGO_TARGET_DIR=out/rust cargo build -p anki_ios_ffi --features native-tls --release
    mkdir -p out
    xcrun -sdk macosx swiftc -parse-as-library \
        -import-objc-header rslib/ios-ffi/include/anki_engine.h \
        ios/Ante/Engine/ProtoWire.swift ios/Ante/Engine/BackendMessages.swift \
        ios/Ante/Generated/BackendIndices.swift ios/Ante/Engine/AnkiEngine.swift \
        ios/Ante/EngineClient.swift ios/Ante/Models.swift ios/hosttest/HostSmoke.swift \
        out/rust/release/libanki_engine.a \
        -framework Security -framework CoreFoundation -framework SystemConfiguration \
        -framework IOKit -liconv -o out/ios_swift_smoke
    ANTE_COLLECTION_DIR="$(mktemp -d)" ./out/ios_swift_smoke

# Ante: build the desktop installer for THIS platform into out/installer/dist/.
# Bundles the ante/ package (den UI, seed data) so the app runs on a clean
# machine. Locally it is adhoc-signed; the release workflow produces the
# signed/notarized installers. See ante/docs/installer.md.
installer:
    {{ if os() == "windows" { "$env:RELEASE='2'; tools\\ninja installer" } else { "RELEASE=2 ./ninja installer" } }}
    @echo "installer artifacts -> out/installer/dist/"

# Build and run all checks (lint + test) - lets ninja handle dependencies
check:
    {{ ninja }} pylib qt check

# Run all tests (Rust, Python, TypeScript). Pass --coverage to enforce coverage, and --html to include HTML reports.
[arg("coverage", long="coverage", value="--coverage")]
[arg("html", long="html", value="--html")]
test coverage='' html='':
    just {{ if coverage == "--coverage" { "coverage " + html } else { "_test" } }}

# Run coverage for all test stacks. Pass --html to also generate HTML reports.
[arg("html", long="html", value="--html")]
coverage html='':
    just _coverage-rust {{ html }}
    just _coverage-py {{ html }}
    just _coverage-ts {{ html }}

# Run Rust tests. Pass --coverage to enforce Rust coverage, and --html to include an HTML report.
[arg("coverage", long="coverage", value="--coverage")]
[arg("html", long="html", value="--html")]
test-rust coverage='' html='':
    just {{ if coverage == "--coverage" { "_coverage-rust " + html } else { "_test-rust" } }}

# Run Python tests (pylib + qt). Pass --coverage to enforce coverage, and --html to include HTML reports.
[arg("coverage", long="coverage", value="--coverage")]
[arg("html", long="html", value="--html")]
test-py coverage='' html='':
    just {{ if coverage == "--coverage" { "_coverage-py " + html } else { "_test-py" } }}

# Run TypeScript/Svelte Vitest tests. Pass --coverage to enforce coverage, and --html to include an HTML report.
[arg("coverage", long="coverage", value="--coverage")]
[arg("html", long="html", value="--html")]
test-ts coverage='' html='':
    just {{ if coverage == "--coverage" { "_coverage-ts " + html } else { "_test-ts" } }}

# Run Playwright end-to-end tests. Pass --ui to open the interactive UI.
[arg("ui", long="ui", value="--ui")]
test-e2e ui='': _install-playwright-browsers
    {{ ninja }} pyenv ts:generated pylib qt
    {{ playwright_env }} {{ yarn }} test:e2e {{ ui }}

[private]
_test:
    {{ ninja }} check:rust_test check:pytest check:vitest

[private]
_test-rust:
    {{ ninja }} check:rust_test

[private]
_test-py:
    {{ ninja }} check:pytest

[private]
_test-ts:
    {{ ninja }} check:vitest

[private]
_coverage-rust html='':
    {{ if os_family() == "windows" { "tools\\coverage\\coverage-rust" } else { "tools/coverage/coverage-rust" } }} {{ html }}

[private]
_coverage-py html='':
    {{ ninja }} pylib qt
    just _coverage-py-pylib {{ html }}
    just _coverage-py-qt {{ html }}

[private]
_coverage-py-pylib html='':
    {{ if os_family() == "windows" { "tools\\coverage\\coverage-py" } else { "tools/coverage/coverage-py" } }} pylib {{ html }}

[private]
_coverage-py-qt html='':
    {{ if os_family() == "windows" { "tools\\coverage\\coverage-py" } else { "tools/coverage/coverage-py" } }} qt {{ html }}

[private]
_coverage-ts html='':
    {{ ninja }} node_modules ts:generated
    {{ if os_family() == "windows" { "tools\\coverage\\coverage-ts" } else { "tools/coverage/coverage-ts" } }} {{ html }}

[private]
_install-playwright-browsers:
    {{ ninja }} node_modules
    {{ playwright_env }} {{ yarn }} playwright install chromium

# Check formatting (fast, no build needed)
fmt:
    {{ ninja }} check:format

# Fix formatting
fix-fmt:
    {{ ninja }} format

# Run linting and type checking (requires build outputs)
lint:
    {{ ninja }} \
        check:clippy \
        check:mypy \
        check:ruff \
        check:eslint \
        check:svelte \
        check:typescript

# Fix auto-fixable lint issues (ruff + eslint)
fix-lint:
    {{ ninja }} fix:ruff fix:eslint

# Run minilints (copyright, contributors, licenses)
minilints:
    {{ ninja }} check:minilints

# Fix minilints (update licenses.json)
fix-minilints:
    {{ ninja }} fix:minilints

# Sync translation files
ftl-sync:
    {{ ninja }} ftl-sync

# Deprecate translation strings
ftl-deprecate:
    {{ ninja }} ftl-deprecate

# Build documentation site
docs:
    {{ uv }} run --group docs sphinx-build -b html docs out/docs/html
    @echo "Docs built at out/docs/html/index.html"

# Build and serve documentation site
docs-serve:
    {{ uv }} run --group docs sphinx-autobuild docs out/docs/html --host 127.0.0.1 --port 8000

# Build Rust API docs
docs-rust:
    cargo doc --open

# Dispatch CI workflow on a given branch or tag
ci branch:
    gh workflow run ci.yml --ref {{ branch }}

# Run Complexipy in regression-only mode
complexipy-diff:
    {{ ninja }} check:complexipy-diff

# Remove build outputs from out/ (pass keep-env to keep node_modules/pyenv); macOS/Linux
clean *args:
    ./tools/clean {{ args }}

# Helpers to get the right commands for the platform

ninja := if os() == "windows" { "tools\\ninja" } else { "./ninja" }
run_script := if os() == "windows" { ".\\run.bat" } else { "./run" }
playwright_env := if os() == "windows" { "set PLAYWRIGHT_BROWSERS_PATH=out\\playwright-browsers&&" } else { "PLAYWRIGHT_BROWSERS_PATH=out/playwright-browsers" }
yarn := if os() == "windows" { "out\\extracted\\node\\yarn.cmd" } else { "out/extracted/node/bin/yarn" }
uv := env("UV_BINARY", if os() == "windows" { "out\\extracted\\uv\\uv" } else { "out/extracted/uv/uv" })
export UV_PROJECT_ENVIRONMENT := if os() == "windows" { "out\\pyenv" } else { "out/pyenv" }
