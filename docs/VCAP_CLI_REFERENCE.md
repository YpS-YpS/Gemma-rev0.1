# VCAP Execution Engine - CLI Reference

**Version:** 0.1.0
**Binary:** `vcap_execution_engine.py`
**Arguments:** 83 (3 positional, 80 optional across 13 groups)
**License:** Internal

---

## Quick Start

```bash
# Minimal invocation
vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080

# With game launch
vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080 \
    --game-path "C:\Games\Cyberpunk\bin\x64\Cyberpunk2077.exe"

# Full production run
vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080 \
    --game-path "C:\Games\Cyberpunk\bin\x64\Cyberpunk2077.exe" \
    --run-count 5 --run-delay 60 \
    --vision-model omniparser --vision-confidence-threshold 0.7 \
    --log-format json --report-format junit --report-file results/report.xml \
    --timing --verbose
```

## Parallel Multi-SUT Execution

Each CLI invocation is a standalone OS process. Run multiple SUTs simultaneously:

```bash
# Three games across three machines, in parallel
vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080 \
    --game-path "C:\Games\Cyberpunk\bin\x64\Cyberpunk2077.exe" \
    --batch-id "nightly-2026-03-03" &

vcap_execution_engine.py config/games/cs2_benchmark.yaml 192.168.1.11 8080 \
    --game-path "steam://rungameid/730" \
    --batch-id "nightly-2026-03-03" &

vcap_execution_engine.py config/games/hitman3.yaml 192.168.1.12 8080 \
    --run-count 5 \
    --batch-id "nightly-2026-03-03" &

wait  # Wait for all to complete
```

---

## Positional Arguments

| # | Argument | Type | Required | Description |
|---|----------|------|----------|-------------|
| 1 | `input_yaml` | path | Yes* | Path to YAML workflow configuration file |
| 2 | `sut_ip` | IP address | Yes* | IP address of the System Under Test |
| 3 | `sut_port` | integer | Yes* | Port of the SUT service |

> *Not required for introspection commands (`--version`, `--list-games`).

---

## 1. Execution Identity (2 arguments)

Control how executions are identified and grouped.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--id ID` | string | auto | Override auto-generated execution ID. Default format: `VCAP-{game}-{YYYYMMDD_HHMMSS}` |
| `--batch-id ID` | string | none | Group multiple runs under a shared batch ID for unified reporting |

**Auto-Generated ID Format:**

```
VCAP-{game_name}-{YYYYMMDD_HHMMSS}
```

Example: `VCAP-Cyberpunk2077-20260303_143022`

---

## 2. Execution Control (10 arguments)

Control run behavior, retry logic, and failure handling.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--run-count N` | int | 1 | Number of times to execute the workflow |
| `--run-delay N` | int | 30 | Seconds to wait between consecutive runs |
| `--max-retries N` | int | 3 | Maximum retry attempts per failed step |
| `--retry-delay N` | float | 2.0 | Seconds to wait between retry attempts |
| `--startup-wait N` | int | YAML | Seconds to wait after game launch before starting automation |
| `--step-timeout N` | int | 60 | Global per-step timeout override (seconds) |
| `--abort-on-hook-failure` | flag | off | Abort entire execution if any hook script fails |
| `--continue-on-sideload-failure` | flag | off | Continue execution when a sideload script fails |
| `--fail-fast` | flag | off | Exit immediately on first step failure (no retries) |
| `--dry-run` | flag | off | Validate config and print execution plan without connecting to SUT |

**Run Count Example:**

```bash
# Run the benchmark 10 times with 60-second gaps
vcap_execution_engine.py config/games/cyberpunk2077.yaml 10.0.0.5 8080 \
    --run-count 10 --run-delay 60 --fail-fast
```

---

## 3. Vision Model Configuration (11 arguments)

Configure the computer vision backend used for UI element detection and screen analysis.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--vision-model MODEL` | choice | `omniparser` | Vision backend: `gemma`, `qwen`, `omniparser` |
| `--vision-model-url URL` | string | model-dependent | URL for vision model API endpoint |
| `--vision-temperature T` | float | 0.01 | LLM sampling temperature (lower = more deterministic) |
| `--vision-max-tokens N` | int | 800 | Maximum response tokens from vision model |
| `--vision-timeout N` | int | 60 | Vision model request timeout (seconds) |
| `--vision-max-elements N` | int | 10 | Maximum UI elements to detect per frame |
| `--vision-confidence-threshold T` | float | 0.6 | Minimum confidence score for element matching (0.0-1.0) |
| `--omniparser-box-threshold T` | float | 0.05 | OmniParser detection box confidence threshold |
| `--omniparser-iou-threshold T` | float | 0.1 | OmniParser intersection-over-union threshold for NMS |
| `--omniparser-use-paddleocr` | flag | on | Enable PaddleOCR text recognition in OmniParser |
| `--no-paddleocr` | flag | off | Disable PaddleOCR (use default OCR engine) |

**Default Vision Model URLs:**

| Model | Default URL |
|-------|-------------|
| omniparser | `http://localhost:8000` |
| gemma | `http://localhost:11434` |
| qwen | `http://localhost:11434` |

**Tuning Example:**

```bash
# High-confidence detection with increased token budget
vcap_execution_engine.py config/games/hitman3.yaml 10.0.0.5 8080 \
    --vision-model omniparser \
    --vision-confidence-threshold 0.8 \
    --vision-max-elements 15 \
    --vision-max-tokens 1200 \
    --omniparser-box-threshold 0.1
```

---

## 4. Game Launch (5 arguments)

Control game process launch and lifecycle management on the SUT.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--game-path PATH` | string | none | Path to game executable or Steam app URL on the SUT |
| `--process-id NAME` | string | none | Process name to track after launch (e.g., `Cyberpunk2077`) |
| `--no-launch` | flag | off | Skip game launch (assume game is already running on SUT) |
| `--kill-on-failure` | flag | on | Kill game process on automation failure |
| `--no-kill-on-failure` | flag | off | Leave game running even on automation failure |

**Launch Methods:**

```bash
# Direct executable path
--game-path "C:\Games\Cyberpunk\bin\x64\Cyberpunk2077.exe"

# Steam protocol URL
--game-path "steam://rungameid/730"

# Skip launch entirely
--no-launch
```

---

## 5. Steam Integration (4 arguments)

Automated Steam login before game launch. The SUT service handles the Steam client interaction.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--steam-login` | flag | off | Enable automated Steam login before game launch |
| `--steam-username USER` | string | none | Steam account username |
| `--steam-password PASS` | string | none | Steam account password |
| `--steam-login-timeout N` | int | 120 | Steam login timeout (seconds) |

**Example:**

```bash
vcap_execution_engine.py config/games/cs2_benchmark.yaml 10.0.0.5 8080 \
    --steam-login --steam-username "testaccount" --steam-password "$STEAM_PASS" \
    --game-path "steam://rungameid/730"
```

> **Security Note:** Prefer `--env-file` or environment variables for credentials instead of command-line arguments.

---

## 6. Logging & Output (11 arguments)

Control log verbosity, output format, and artifact generation.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--log-level LEVEL` | choice | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `--log-dir DIR` | path | `logs/` | Base directory for all log output |
| `--run-dir DIR` | path | auto | Override the run-specific output directory |
| `--log-format FMT` | choice | `detailed` | Log format: `detailed`, `compact`, `json` |
| `--log-file PATH` | path | auto | Explicit path for the log file |
| `-q` / `--quiet` | flag | off | Suppress stdout output, log to file only |
| `-v` / `--verbose` | flag | off | Enable DEBUG logging (shorthand for `--log-level DEBUG`) |
| `--no-screenshots` | flag | off | Skip saving screenshots to disk |
| `--no-annotations` | flag | off | Skip generating annotated bounding-box screenshots |
| `--save-json-response` | flag | on | Save vision model raw JSON responses to disk |
| `--no-json-response` | flag | off | Disable saving vision model JSON responses |

**Log Directory Structure:**

```
logs/
  {execution_id}/
    run_1/
      automation.log
      config_dump.yaml
      screenshots/
        screenshot_1.png
        screenshot_2.png
        ...
      annotated/
        annotated_1.png
        ...
      report.{txt|json|xml}
    run_2/
      ...
```

**Log Formats:**

| Format | Description | Use Case |
|--------|-------------|----------|
| `detailed` | Timestamp + level + module + message | Development, debugging |
| `compact` | Timestamp + level + message | Quick monitoring |
| `json` | Structured JSON per line | Log aggregation (ELK, Splunk) |

---

## 7. Network Tuning (5 arguments)

Fine-tune SUT communication timeouts and connection behavior.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--network-timeout N` | int | 10 | Timeout for SUT action commands (seconds) |
| `--screenshot-timeout N` | int | 15 | Timeout for screenshot capture (seconds) |
| `--launch-timeout N` | int | 90 | Timeout for game launch request (seconds) |
| `--connection-check` | flag | on | Verify SUT connectivity on startup |
| `--no-connection-check` | flag | off | Skip SUT connectivity verification |

**Slow Network Example:**

```bash
# Extended timeouts for high-latency SUT connections
vcap_execution_engine.py config/games/hitman3.yaml 10.0.0.5 8080 \
    --network-timeout 30 --screenshot-timeout 30 --launch-timeout 180
```

---

## 8. Hooks & Sideload (6 arguments)

Manage pre/post-automation hooks and per-step sideload script execution.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--pre-hook PATH` | string (repeatable) | none | Additional pre-hook script path. Can be specified multiple times. |
| `--post-hook PATH` | string (repeatable) | none | Additional post-hook script path. Can be specified multiple times. |
| `--hook-timeout N` | int | 300 | Default timeout for hook execution (seconds) |
| `--no-hooks` | flag | off | Disable all hooks (pre, post, and persistent) |
| `--no-sideload` | flag | off | Disable all step sideload scripts |
| `--sideload-timeout N` | int | 300 | Default sideload execution timeout (seconds) |

**Hook Execution Order:**

```
Pre-hooks (sequential)
  |
  v
Persistent hooks start (background)
  |
  v
Step 1..N execute
  |-- Each step: find -> action -> sideload -> verify
  |
  v
Persistent hooks stop
  |
  v
Post-hooks (sequential)
```

**Example:**

```bash
# Add monitoring hooks via CLI (merged with YAML-defined hooks)
vcap_execution_engine.py config/games/cs2_benchmark.yaml 10.0.0.5 8080 \
    --pre-hook scripts/start_monitoring.ps1 \
    --pre-hook scripts/clear_logs.ps1 \
    --post-hook scripts/collect_results.ps1 \
    --hook-timeout 600
```

---

## 9. Input Control Tuning (6 arguments)

Fine-tune mouse movement, click timing, and keyboard input parameters sent to the SUT.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--click-move-duration T` | float | 0.5 | Mouse movement duration before click (seconds) |
| `--click-delay T` | float | 0.1 | Delay between mouse down/up events (seconds) |
| `--type-char-delay T` | float | 0.05 | Delay between keystrokes for text input (seconds) |
| `--scroll-clicks N` | int | 3 | Default scroll wheel clicks per scroll action |
| `--drag-duration T` | float | 1.0 | Duration for drag operations (seconds) |
| `--drag-steps N` | int | 20 | Interpolation steps for drag movement smoothing |

**Slow Input Example:**

```bash
# Slow, deliberate input for animation-heavy UI
vcap_execution_engine.py config/games/hitman3.yaml 10.0.0.5 8080 \
    --click-move-duration 1.0 --click-delay 0.3 --type-char-delay 0.1
```

---

## 10. Text Matching (2 arguments)

Configure how UI element text is matched against expected values.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--default-match-strategy STRATEGY` | choice | `contains` | Strategy: `exact`, `contains`, `startswith`, `endswith` |
| `--case-sensitive` | flag | off | Enable case-sensitive text matching |

**Match Strategies:**

| Strategy | Behavior | Example Match |
|----------|----------|---------------|
| `exact` | Full string equality | "Settings" matches "Settings" only |
| `contains` | Substring search | "Settings" matches "Game Settings Menu" |
| `startswith` | Prefix match | "Settings" matches "Settings & Options" |
| `endswith` | Suffix match | "Settings" matches "Game Settings" |

---

## 11. Campaign Mode (4 arguments)

Execute multiple games sequentially from a campaign configuration file.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--campaign PATH` | path | none | Path to campaign JSON file |
| `--delay-between-games N` | int | 120 | Seconds between games in campaign (seconds) |
| `--continue-on-failure` | flag | on | Continue campaign after individual game failure |
| `--no-continue-on-failure` | flag | off | Stop entire campaign on first game failure |

**Campaign JSON Format:**

```json
{
  "campaign_name": "Nightly Benchmark Suite",
  "games": [
    {
      "config": "config/games/cyberpunk2077.yaml",
      "game_path": "C:\\Games\\Cyberpunk\\bin\\x64\\Cyberpunk2077.exe",
      "run_count": 3
    },
    {
      "config": "config/games/cs2_benchmark.yaml",
      "game_path": "steam://rungameid/730",
      "run_count": 5
    },
    {
      "config": "config/games/hitman3.yaml",
      "game_path": "C:\\Games\\Hitman3\\hitman3.exe",
      "run_count": 3
    }
  ]
}
```

**Example:**

```bash
vcap_execution_engine.py --campaign campaigns/nightly.json 10.0.0.5 8080 \
    --delay-between-games 180 --no-continue-on-failure
```

---

## 12. Reporting (6 arguments)

Configure execution reports, summaries, and output formats.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--report-format FMT` | choice | `text` | Output format: `text`, `json`, `junit` |
| `--report-file PATH` | path | none | Write execution report to file |
| `--exit-code-mode MODE` | choice | `binary` | Exit code mode: `binary` (0/1) or `step-count` (failed step count) |
| `--timing` | flag | off | Print per-step timing summary after execution |
| `--summary` | flag | on | Print execution summary on completion |
| `--no-summary` | flag | off | Suppress execution summary output |

**Report Formats:**

| Format | Description | Use Case |
|--------|-------------|----------|
| `text` | Human-readable plain text | Manual review |
| `json` | Structured JSON with full metadata | Programmatic consumption, dashboards |
| `junit` | JUnit XML format | CI/CD integration (Jenkins, GitHub Actions, Azure DevOps) |

**CI/CD Integration Example:**

```bash
vcap_execution_engine.py config/games/cyberpunk2077.yaml 10.0.0.5 8080 \
    --report-format junit --report-file results/benchmark.xml \
    --timing --log-format json --quiet
```

---

## 13. Introspection & Utilities (7 arguments)

Validate configs, inspect workflows, and override settings without running the full pipeline.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--validate-only` | flag | off | Validate YAML configuration and exit |
| `--config-dump` | flag | off | Dump merged configuration to stdout (YAML format) |
| `--list-steps` | flag | off | List workflow steps with components and exit |
| `--list-games` | flag | off | List available game configs from `config/games/` and exit |
| `--override KEY=VALUE` | repeatable | none | Override YAML config values at runtime (dot notation) |
| `--env-file PATH` | path | none | Load environment variables from `.env` file before execution |
| `--version` | flag | off | Print VCAP version and exit |

**Override Examples:**

```bash
# Override nested config values with dot notation
--override metadata.game_name=CustomTest
--override steps.1.timeout=30
--override metadata.startup_wait=120

# Load environment from file
--env-file production.env
```

**Introspection Usage:**

```bash
# Validate a config without connecting to SUT
vcap_execution_engine.py config/games/cyberpunk2077.yaml 0 0 --validate-only

# Dump the full merged config
vcap_execution_engine.py config/games/cyberpunk2077.yaml 0 0 --config-dump

# List all steps in a workflow
vcap_execution_engine.py config/games/cyberpunk2077.yaml 0 0 --list-steps

# List all available game configs
vcap_execution_engine.py --list-games

# Print version
vcap_execution_engine.py --version
```

---

## Exit Codes

| Code | Meaning | Typical Cause |
|------|---------|---------------|
| 0 | Success | All steps completed successfully |
| 1 | Step failure | One or more automation steps failed |
| 2 | Config error | YAML validation failed, missing modules |
| 3 | Connection error | Cannot reach SUT at specified IP:port |
| 4 | Vision model error | Vision model API not responding |
| 5 | Launch error | Game failed to launch on SUT |

With `--exit-code-mode step-count`, exit code 1 is replaced with the count of failed steps (useful for CI pipelines).

---

## Environment Variables

The following environment variables are recognized when set (or loaded via `--env-file`):

| Variable | Equivalent Argument |
|----------|-------------------|
| `VCAP_VISION_MODEL` | `--vision-model` |
| `VCAP_VISION_URL` | `--vision-model-url` |
| `VCAP_LOG_LEVEL` | `--log-level` |
| `VCAP_SUT_IP` | `sut_ip` positional |
| `VCAP_SUT_PORT` | `sut_port` positional |
| `STEAM_USERNAME` | `--steam-username` |
| `STEAM_PASSWORD` | `--steam-password` |

> CLI arguments always take precedence over environment variables, which take precedence over YAML config values.

---

## Configuration Precedence

Settings are resolved in this order (highest priority first):

```
1. CLI arguments          (--vision-model omniparser)
2. --override values      (--override metadata.startup_wait=120)
3. Environment variables  (VCAP_VISION_MODEL=gemma)
4. --env-file values      (loaded from .env file)
5. YAML config file       (config/games/cyberpunk2077.yaml)
6. Built-in defaults      (hardcoded in argparse)
```

---

## Architecture

```
vcap_execution_engine.py
    |
    |-- [Parse CLI args]
    |-- [Load YAML config]
    |-- [Apply overrides: CLI > env > YAML]
    |
    |-- NetworkManager          -> SUT REST communication
    |-- Vision Model Client     -> OmniParser / Gemma / Qwen
    |-- ScreenshotManager       -> Screen capture from SUT
    |-- Annotator               -> Bounding box visualization
    |-- GameLauncher            -> Game process management
    |
    |-- SimpleAutomation.run()
    |   |-- Pre-hooks execute
    |   |-- Persistent hooks start
    |   |-- Steps 1..N
    |   |   |-- find (vision model detects UI elements)
    |   |   |-- action (click/key/type/scroll/drag/wait)
    |   |   |-- sideload (per-step script execution)
    |   |   |-- verify (optional verification)
    |   |-- Persistent hooks stop
    |   |-- Post-hooks execute
    |
    |-- [Generate report]
    |-- [Print summary]
    |-- [Exit with code]
```

---

## Full Argument Summary

| # | Group | Count | Notable Arguments |
|---|-------|------:|-------------------|
| - | Positional | 3 | `input_yaml`, `sut_ip`, `sut_port` |
| 1 | Execution Identity | 2 | `--id`, `--batch-id` |
| 2 | Execution Control | 10 | `--run-count`, `--dry-run`, `--fail-fast` |
| 3 | Vision Model | 11 | `--vision-model`, `--vision-confidence-threshold` |
| 4 | Game Launch | 5 | `--game-path`, `--no-launch` |
| 5 | Steam Integration | 4 | `--steam-login`, `--steam-username` |
| 6 | Logging & Output | 11 | `--log-level`, `-q`, `-v`, `--no-screenshots` |
| 7 | Network Tuning | 5 | `--network-timeout`, `--screenshot-timeout` |
| 8 | Hooks & Sideload | 6 | `--pre-hook`, `--no-hooks`, `--no-sideload` |
| 9 | Input Control | 6 | `--click-move-duration`, `--drag-steps` |
| 10 | Text Matching | 2 | `--default-match-strategy`, `--case-sensitive` |
| 11 | Campaign Mode | 4 | `--campaign`, `--delay-between-games` |
| 12 | Reporting | 6 | `--report-format`, `--timing`, `--exit-code-mode` |
| 13 | Introspection | 7 | `--validate-only`, `--list-steps`, `--override` |
| | **Total** | **83** | |

> Plus `--help` (built-in), bringing the effective total to **84 recognized flags**.

---

*Generated for VCAP Execution Engine v0.1.0 on 2026-03-03.*
