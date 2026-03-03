# VCAP Execution Engine — Design Document

**Date:** 2026-03-03
**Branch:** feature/hooks-and-sideload
**Status:** Approved

## Overview

`vcap_execution_engine.py` is a headless CLI execution engine for the VCAP automation framework. It wraps the existing `SimpleAutomation` pipeline into a single command-line invocable script, enabling parallel multi-SUT execution without the Tkinter GUI.

### Core Invocation

```
vcap_execution_engine.py <input_yaml> <sut_ip> <sut_port> [OPTIONS]
```

Parallel multi-SUT usage (each is its own process):

```bash
vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080 --game-path "C:\Games\Cyberpunk\bin\x64\Cyberpunk2077.exe" &
vcap_execution_engine.py config/games/cs2_benchmark.yaml  192.168.1.11 8080 --game-path "steam://rungameid/730" &
vcap_execution_engine.py config/games/hitman3.yaml         192.168.1.12 8080 --run-count 5 &
```

## Architecture

### Execution Flow

```
CLI Args Parsed (argparse)
    |
    v
YAML loaded via SimpleConfigParser
    |
    v
CLI args override YAML values where applicable
    |
    v
NetworkManager connects to SUT
    |
    v
Vision model client initialized (omniparser/gemma/qwen)
    |
    v
[Optional] Steam login
    |
    v
[Optional] Game launch via GameLauncher
    |
    v
SimpleAutomation.run()
    |-- Pre-hooks execute
    |-- Persistent hooks start
    |-- Steps 1..N execute (find -> action -> sideload -> verify)
    |-- Persistent hooks stop
    |-- Post-hooks execute
    |
    v
Exit code 0 (success) or 1 (failure)
```

### Module Dependencies

```
vcap_execution_engine.py
    ├── modules/simple_config_parser.py   (YAML validation & parsing)
    ├── modules/simple_automation.py      (step execution engine)
    ├── modules/network.py                (SUT REST communication)
    ├── modules/screenshot.py             (screenshot capture)
    ├── modules/annotator.py              (bounding box visualization)
    ├── modules/game_launcher.py          (game process launch)
    ├── modules/gemma_client.py           (Gemma vision model)
    ├── modules/qwen_client.py            (Qwen VL vision model)
    └── modules/omniparser_client.py      (OmniParser vision model)
```

## Positional Arguments

| Arg | Type | Description |
|-----|------|-------------|
| `input_yaml` | str | Path to YAML workflow configuration file |
| `sut_ip` | str | IP address of the System Under Test |
| `sut_port` | int | Port of the SUT service |

## Argument Groups

### Execution Identity

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--id` | str | auto | Execution ID (auto: `VCAP-{game}-{timestamp}`) |
| `--batch-id` | str | None | Group multiple runs under a shared batch ID |

### Execution Control

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--run-count` | int | 1 | Number of times to run the workflow |
| `--run-delay` | int | 30 | Seconds to wait between runs |
| `--max-retries` | int | 3 | Max retries per failed step |
| `--retry-delay` | float | 2.0 | Seconds between retries |
| `--startup-wait` | int | from YAML | Seconds to wait after game launch |
| `--step-timeout` | int | 60 | Global per-step timeout override |
| `--abort-on-hook-failure` | flag | False | Abort if any hook fails |
| `--continue-on-sideload-failure` | flag | False | Continue when sideload fails |
| `--fail-fast` | flag | False | Exit immediately on first step failure |
| `--dry-run` | flag | False | Validate config and print plan, don't execute |

### Vision Model

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--vision-model` | choice | omniparser | Vision model backend: gemma, qwen, omniparser |
| `--vision-model-url` | str | model-dependent | URL for vision model API endpoint |
| `--vision-temperature` | float | 0.01 | LLM sampling temperature |
| `--vision-max-tokens` | int | 800 | Max response tokens from vision model |
| `--vision-timeout` | int | 60 | Vision model request timeout (seconds) |
| `--vision-max-elements` | int | 10 | Max UI elements to detect per frame |
| `--vision-confidence-threshold` | float | 0.6 | Min confidence for element matching |
| `--omniparser-box-threshold` | float | 0.05 | OmniParser detection box threshold |
| `--omniparser-iou-threshold` | float | 0.1 | OmniParser intersection-over-union threshold |
| `--omniparser-use-paddleocr` | flag | True | Enable PaddleOCR in OmniParser |
| `--no-paddleocr` | flag | False | Disable PaddleOCR in OmniParser |

### Game Launch

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--game-path` | str | None | Path to game executable or Steam app URL |
| `--process-id` | str | None | Process name to track after launch |
| `--no-launch` | flag | False | Skip game launch (assume already running) |
| `--kill-on-failure` | flag | True | Kill game process on automation failure |
| `--no-kill-on-failure` | flag | False | Leave game running even on failure |

### Steam Integration

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--steam-login` | flag | False | Enable Steam login before game launch |
| `--steam-username` | str | None | Steam account username |
| `--steam-password` | str | None | Steam account password |
| `--steam-login-timeout` | int | 120 | Steam login timeout (seconds) |

### Logging & Output

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--log-level` | choice | INFO | Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `--log-dir` | str | logs/ | Base directory for log output |
| `--run-dir` | str | auto | Override the run-specific output directory |
| `--log-format` | choice | detailed | Log format: detailed, compact, json |
| `--log-file` | str | auto | Explicit path for the log file |
| `--quiet` / `-q` | flag | False | Suppress stdout output, log to file only |
| `--verbose` / `-v` | flag | False | Enable DEBUG logging (alias for --log-level DEBUG) |
| `--no-screenshots` | flag | False | Skip saving screenshots to disk |
| `--no-annotations` | flag | False | Skip generating annotated screenshots |
| `--save-json-response` | flag | True | Save vision model JSON responses |
| `--no-json-response` | flag | False | Disable saving JSON responses |

### Network Tuning

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--network-timeout` | int | 10 | Timeout for SUT action commands (seconds) |
| `--screenshot-timeout` | int | 15 | Timeout for screenshot capture (seconds) |
| `--launch-timeout` | int | 90 | Timeout for game launch request (seconds) |
| `--connection-check` | flag | True | Verify SUT connectivity on startup |
| `--no-connection-check` | flag | False | Skip SUT connectivity check |

### Hooks & Sideload

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--pre-hook` | str (repeatable) | None | Additional pre-hook script path(s) |
| `--post-hook` | str (repeatable) | None | Additional post-hook script path(s) |
| `--hook-timeout` | int | 300 | Default timeout for hook execution |
| `--no-hooks` | flag | False | Disable all hooks (pre, post, persistent) |
| `--no-sideload` | flag | False | Disable all step sideloads |
| `--sideload-timeout` | int | 300 | Default sideload execution timeout |

### Input Control Tuning

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--click-move-duration` | float | 0.5 | Mouse movement duration before click (seconds) |
| `--click-delay` | float | 0.1 | Delay between mouse down/up (seconds) |
| `--type-char-delay` | float | 0.05 | Delay between keystrokes for text input |
| `--scroll-clicks` | int | 3 | Default scroll wheel clicks |
| `--drag-duration` | float | 1.0 | Duration for drag operations (seconds) |
| `--drag-steps` | int | 20 | Interpolation steps for drag smoothing |

### Text Matching

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--default-match-strategy` | choice | contains | Default text matching: exact, contains, startswith, endswith |
| `--case-sensitive` | flag | False | Enable case-sensitive text matching |

### Campaign Mode

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--campaign` | str | None | Path to campaign JSON file |
| `--delay-between-games` | int | 120 | Seconds between games in campaign |
| `--continue-on-failure` | flag | True | Continue campaign after game failure |
| `--no-continue-on-failure` | flag | False | Stop campaign on first game failure |

### Reporting

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--report-format` | choice | text | Report output format: text, json, junit |
| `--report-file` | str | None | Write execution report to file |
| `--exit-code-mode` | choice | binary | Exit code mode: binary (0/1), step-count |
| `--timing` | flag | False | Print per-step timing summary |
| `--summary` | flag | True | Print execution summary on completion |
| `--no-summary` | flag | False | Suppress execution summary |

### Introspection & Utilities

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--validate-only` | flag | False | Validate YAML config and exit |
| `--config-dump` | flag | False | Dump merged configuration and exit |
| `--list-steps` | flag | False | List workflow steps and exit |
| `--list-games` | flag | False | List available game configs and exit |
| `--override` | key=value (repeatable) | None | Override YAML config values at runtime |
| `--env-file` | str | None | Load environment variables from .env file |
| `--version` | flag | — | Print VCAP version and exit |

## Auto-Generated Execution ID

Format: `VCAP-{game_name}-{YYYYMMDD_HHMMSS}`

Example: `VCAP-Cyberpunk2077-20260303_143022`

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All steps completed successfully |
| 1 | One or more steps failed |
| 2 | Configuration validation error |
| 3 | SUT connection failure |
| 4 | Vision model connection failure |
| 5 | Game launch failure |

## Log Directory Structure

```
logs/
└── {execution_id}/
    ├── automation.log
    ├── config_dump.yaml
    ├── screenshots/
    │   ├── screenshot_1.png
    │   ├── screenshot_2.png
    │   └── ...
    ├── annotated/
    │   ├── annotated_1.png
    │   └── ...
    └── report.{txt|json|xml}
```

## Implementation Notes

- Single file: `vcap_execution_engine.py` at project root
- No new dependencies — uses only existing modules
- CLI args take precedence over YAML config values
- The `--dry-run` flag validates config, prints execution plan, and exits
- Introspection flags (`--validate-only`, `--list-steps`, etc.) exit before connecting to SUT
- Hook/sideload disable flags (`--no-hooks`, `--no-sideload`) override YAML at runtime
