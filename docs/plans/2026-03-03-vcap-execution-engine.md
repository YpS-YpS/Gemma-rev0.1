# VCAP Execution Engine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `vcap_execution_engine.py`, a headless CLI that wraps the existing SimpleAutomation pipeline with ~70 argparse arguments, plus a comprehensive `VCAP_CLI_REFERENCE.md` markdown doc.

**Architecture:** Single-file CLI at project root. Uses argparse with argument groups. Wraps existing modules (SimpleConfigParser, SimpleAutomation, NetworkManager, vision clients, GameLauncher, Annotator). No new dependencies.

**Tech Stack:** Python 3.10+, argparse, existing modules in `modules/`

---

### Task 1: Scaffold the file with argparse and all argument groups

**Files:**
- Create: `vcap_execution_engine.py`

**Step 1: Create `vcap_execution_engine.py` with the full argparse skeleton**

Write the complete file with:
- Shebang, docstring, version constant `__version__ = "0.1.0"`
- `build_parser()` function that returns an `argparse.ArgumentParser` with all 12 argument groups and ~70 arguments exactly as specified in `docs/plans/2026-03-03-vcap-execution-engine-design.md`
- `main()` function that calls `build_parser().parse_args()` and prints `args` for now
- `if __name__ == "__main__": main()` guard

The 3 positional arguments:
```python
parser.add_argument('input_yaml', type=str, help='Path to YAML workflow configuration file')
parser.add_argument('sut_ip', type=str, help='IP address of the System Under Test')
parser.add_argument('sut_port', type=int, help='Port of the SUT service')
```

All 12 argument groups (copy exact flags from design doc):
1. Execution Identity: `--id`, `--batch-id`
2. Execution Control: `--run-count`, `--run-delay`, `--max-retries`, `--retry-delay`, `--startup-wait`, `--step-timeout`, `--abort-on-hook-failure`, `--continue-on-sideload-failure`, `--fail-fast`, `--dry-run`
3. Vision Model: `--vision-model`, `--vision-model-url`, `--vision-temperature`, `--vision-max-tokens`, `--vision-timeout`, `--vision-max-elements`, `--vision-confidence-threshold`, `--omniparser-box-threshold`, `--omniparser-iou-threshold`, `--omniparser-use-paddleocr`, `--no-paddleocr`
4. Game Launch: `--game-path`, `--process-id`, `--no-launch`, `--kill-on-failure`, `--no-kill-on-failure`
5. Steam Integration: `--steam-login`, `--steam-username`, `--steam-password`, `--steam-login-timeout`
6. Logging & Output: `--log-level`, `--log-dir`, `--run-dir`, `--log-format`, `--log-file`, `--quiet`/`-q`, `--verbose`/`-v`, `--no-screenshots`, `--no-annotations`, `--save-json-response`, `--no-json-response`
7. Network Tuning: `--network-timeout`, `--screenshot-timeout`, `--launch-timeout`, `--connection-check`, `--no-connection-check`
8. Hooks & Sideload: `--pre-hook`, `--post-hook`, `--hook-timeout`, `--no-hooks`, `--no-sideload`, `--sideload-timeout`
9. Input Control Tuning: `--click-move-duration`, `--click-delay`, `--type-char-delay`, `--scroll-clicks`, `--drag-duration`, `--drag-steps`
10. Text Matching: `--default-match-strategy`, `--case-sensitive`
11. Campaign Mode: `--campaign`, `--delay-between-games`, `--continue-on-failure`, `--no-continue-on-failure`
12. Reporting: `--report-format`, `--report-file`, `--exit-code-mode`, `--timing`, `--summary`, `--no-summary`
13. Introspection: `--validate-only`, `--config-dump`, `--list-steps`, `--list-games`, `--override`, `--env-file`, `--version`

**Step 2: Verify the argument parser works**

Run: `python vcap_execution_engine.py --help`
Expected: Full help output with all argument groups displayed.

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080`
Expected: Prints parsed args namespace (no crash).

**Step 3: Commit**

```bash
git add vcap_execution_engine.py
git commit -m "feat: Scaffold vcap_execution_engine.py with full argparse (~70 args)"
```

---

### Task 2: Implement introspection commands (no SUT needed)

**Files:**
- Modify: `vcap_execution_engine.py`

These commands validate/inspect config and exit without connecting to any SUT.

**Step 1: Implement `--version`**

```python
if args.version:
    print(f"VCAP Execution Engine v{__version__}")
    sys.exit(0)
```

**Step 2: Implement `--list-games`**

Reuse logic from `main.py:list_available_games()`:
```python
if args.list_games:
    # Scan config/games/*.yaml and print game names
    ...
    sys.exit(0)
```

**Step 3: Implement `--validate-only`**

```python
if args.validate_only:
    try:
        from modules.simple_config_parser import SimpleConfigParser
        parser = SimpleConfigParser(args.input_yaml)
        print(f"Config valid: {parser.game_name} ({len(parser.get_config().get('steps', {}))} steps)")
        sys.exit(0)
    except Exception as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(2)
```

**Step 4: Implement `--list-steps`**

```python
if args.list_steps:
    from modules.simple_config_parser import SimpleConfigParser
    parser = SimpleConfigParser(args.input_yaml)
    steps = parser.get_config().get("steps", {})
    for num, step in sorted(steps.items(), key=lambda x: int(x[0])):
        desc = step.get("description", "No description")
        has_find = "find" in step
        has_action = "action" in step
        has_sideload = "sideload" in step
        parts = []
        if has_find: parts.append("find")
        if has_action: parts.append(f"action:{step['action'].get('type','?')}")
        if has_sideload: parts.append("sideload")
        print(f"  Step {num}: {desc}  [{', '.join(parts)}]")
    sys.exit(0)
```

**Step 5: Implement `--config-dump`**

```python
if args.config_dump:
    from modules.simple_config_parser import SimpleConfigParser
    import yaml
    parser = SimpleConfigParser(args.input_yaml)
    yaml.dump(parser.get_config(), sys.stdout, default_flow_style=False)
    sys.exit(0)
```

**Step 6: Implement `--dry-run`**

Validates config, prints execution plan (steps, hooks, sideloads), but does NOT connect to SUT.

**Step 7: Verify introspection commands**

Run: `python vcap_execution_engine.py --version`
Expected: `VCAP Execution Engine v0.1.0`

Run: `python vcap_execution_engine.py --list-games dummy 0 0`
Expected: List of game configs from `config/games/`

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 0.0.0.0 0 --validate-only`
Expected: `Config valid: Cyberpunk2077 (8 steps)`

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 0.0.0.0 0 --list-steps`
Expected: Numbered step list with descriptions.

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 0.0.0.0 0 --dry-run`
Expected: Execution plan printed, no SUT connection.

**Step 8: Commit**

```bash
git add vcap_execution_engine.py
git commit -m "feat: Add introspection commands (--version, --list-games, --validate-only, --list-steps, --config-dump, --dry-run)"
```

---

### Task 3: Implement the core execution engine

**Files:**
- Modify: `vcap_execution_engine.py`

This is the main execution logic — the equivalent of `SUTController._run_single_game()` but driven by CLI args.

**Step 1: Implement `generate_execution_id()`**

```python
def generate_execution_id(game_name):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^a-zA-Z0-9]', '', game_name)
    return f"VCAP-{safe_name}-{timestamp}"
```

**Step 2: Implement `setup_logging(args, execution_id)`**

Sets up file + console logging based on `--log-level`, `--log-dir`, `--log-format`, `--quiet`, `--verbose`, `--log-file`. Creates the log directory structure:
```
logs/{execution_id}/
    ├── automation.log
    ├── screenshots/
    └── annotated/
```

**Step 3: Implement `create_run_directory(args, execution_id, run_num)`**

Creates per-run subdirectories within the execution directory (for `--run-count > 1`).

**Step 4: Implement `init_vision_model(args)`**

```python
def init_vision_model(args):
    url = args.vision_model_url
    if args.vision_model == 'omniparser':
        if not url: url = 'http://localhost:8000'
        return OmniparserClient(url)
    elif args.vision_model == 'gemma':
        if not url: url = 'http://127.0.0.1:1234'
        return GemmaClient(url)
    elif args.vision_model == 'qwen':
        if not url: url = 'http://127.0.0.1:1234'
        return QwenClient(url)
```

**Step 5: Implement `run_execution(args)` — the main execution function**

This function orchestrates the full pipeline:
1. Load and validate YAML config via `SimpleConfigParser`
2. Generate execution ID (or use `--id`)
3. Set up logging
4. For each run in `--run-count`:
   a. Create run directory
   b. Connect to SUT via `NetworkManager(args.sut_ip, args.sut_port)`
   c. Init vision model
   d. Handle Steam login if `--steam-login`
   e. Launch game if `--game-path` and not `--no-launch`
   f. Wait `--startup-wait` seconds
   g. Create `SimpleAutomation` instance and call `.run()`
   h. Handle success/failure/cleanup
   i. Wait `--run-delay` between runs
5. Print summary if `--summary`
6. Return appropriate exit code

**Step 6: Wire `main()` to call `run_execution(args)`**

After introspection commands are handled, call `run_execution(args)` and `sys.exit()` with the return code.

**Step 7: Verify with `--help` still works**

Run: `python vcap_execution_engine.py --help`
Expected: No import errors, full help displayed.

**Step 8: Commit**

```bash
git add vcap_execution_engine.py
git commit -m "feat: Implement core execution engine with run loop, logging, vision model init"
```

---

### Task 4: Implement CLI overrides that modify runtime behavior

**Files:**
- Modify: `vcap_execution_engine.py`

These flags modify the YAML config or SimpleAutomation behavior at runtime.

**Step 1: Implement `--no-hooks` and `--no-sideload`**

Before passing config to SimpleAutomation, strip hooks/sideload sections:
```python
if args.no_hooks:
    config.pop("hooks", None)
if args.no_sideload:
    for step in config.get("steps", {}).values():
        step.pop("sideload", None)
```

**Step 2: Implement `--pre-hook` and `--post-hook` (CLI-injected hooks)**

Append CLI-specified hooks to the config's hooks section:
```python
if args.pre_hook:
    hooks = config.setdefault("hooks", {})
    pre = hooks.setdefault("pre", [])
    for hook_path in args.pre_hook:
        pre.append({"path": hook_path, "timeout": args.hook_timeout})

if args.post_hook:
    hooks = config.setdefault("hooks", {})
    post = hooks.setdefault("post", [])
    for hook_path in args.post_hook:
        post.append({"path": hook_path, "timeout": args.hook_timeout})
```

**Step 3: Implement `--override` (generic YAML overrides)**

Parse `key=value` pairs and apply dot-notation overrides:
```python
if args.override:
    for override in args.override:
        key, value = override.split("=", 1)
        # Apply to config using dot-notation path
        parts = key.split(".")
        target = config
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = yaml.safe_load(value)
```

**Step 4: Implement `--env-file`**

Load `.env` file into `os.environ` before execution:
```python
if args.env_file:
    with open(args.env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()
```

**Step 5: Implement game process cleanup on failure**

If `--kill-on-failure` (default True) and automation fails, terminate the game process via `NetworkManager.terminate_process()` — mirror `SUTController.kill_game_process()` logic.

**Step 6: Commit**

```bash
git add vcap_execution_engine.py
git commit -m "feat: Add CLI overrides (--no-hooks, --no-sideload, --pre-hook, --post-hook, --override, --env-file)"
```

---

### Task 5: Implement reporting and summary output

**Files:**
- Modify: `vcap_execution_engine.py`

**Step 1: Implement execution summary**

Print a summary block at the end of execution:
```
═══════════════════════════════════════════════════════
  VCAP Execution Summary
═══════════════════════════════════════════════════════
  Execution ID : VCAP-Cyberpunk2077-20260303_143022
  Config       : config/games/cyberpunk2077.yaml
  SUT          : 192.168.1.10:8080
  Vision Model : omniparser
  Status       : SUCCESS
  Runs         : 3/3 completed
  Duration     : 4m 32s
  Log Dir      : logs/VCAP-Cyberpunk2077-20260303_143022/
═══════════════════════════════════════════════════════
```

**Step 2: Implement `--timing`**

Track per-step wall-clock time and print a timing table:
```
Step  Description                    Duration
────  ─────────────────────────────  ────────
1     Press space to continue        3.2s
2     Click settings                 5.1s
3     Click graphics                 4.8s
...
Total                                42.3s
```

**Step 3: Implement `--report-file` with `--report-format`**

- `text`: Write the summary block to file
- `json`: Write a JSON object with execution metadata, step results, timing
- `junit`: Write JUnit XML for CI integration

**Step 4: Implement exit code logic**

```python
if args.exit_code_mode == 'binary':
    sys.exit(0 if success else 1)
elif args.exit_code_mode == 'step-count':
    sys.exit(failed_step_count)
```

Plus specific exit codes 2-5 for config/connection/vision/launch failures.

**Step 5: Commit**

```bash
git add vcap_execution_engine.py
git commit -m "feat: Add execution summary, timing, report output, exit codes"
```

---

### Task 6: Write the VCAP CLI Reference markdown

**Files:**
- Create: `docs/VCAP_CLI_REFERENCE.md`

**Step 1: Write the full CLI reference document**

This is the "impress management" artifact. It should include:

1. **Header with ASCII art / branding** — VCAP Execution Engine title
2. **Quick Start** — 3 example commands
3. **Installation & Prerequisites** — Python version, dependencies, SUT service
4. **Usage Synopsis** — Full command line syntax
5. **Positional Arguments** — Table with descriptions
6. **All 12 Argument Groups** — Each with table, description, examples
7. **Environment Variables** — `VCAP_SUT_IP`, `VCAP_VISION_MODEL`, etc. (mapped from args)
8. **Exit Codes** — Full table
9. **Log Directory Structure** — Tree diagram
10. **Configuration Precedence** — CLI > env vars > YAML > defaults
11. **Examples Section** — 15+ real-world usage examples covering:
    - Basic single game run
    - Multi-SUT parallel execution
    - Campaign mode
    - Dry run validation
    - Custom hooks
    - Vision model tuning
    - Report generation
    - CI/CD integration
12. **Troubleshooting** — Common errors and fixes
13. **Version History** — Changelog

**Step 2: Commit**

```bash
git add docs/VCAP_CLI_REFERENCE.md
git commit -m "docs: Add comprehensive VCAP CLI Reference (~70 arguments documented)"
```

---

### Task 7: Final verification and cleanup

**Files:**
- Modify: `vcap_execution_engine.py` (if needed)

**Step 1: Run `--help` and verify all 70 args are present**

Run: `python vcap_execution_engine.py --help`
Expected: Clean output, all groups visible, no crashes.

**Step 2: Run `--version`**

Run: `python vcap_execution_engine.py --version`
Expected: `VCAP Execution Engine v0.1.0`

**Step 3: Run `--validate-only` on multiple configs**

```bash
python vcap_execution_engine.py config/games/cyberpunk2077.yaml 0 0 --validate-only
python vcap_execution_engine.py config/games/cs2_benchmark.yaml 0 0 --validate-only
python vcap_execution_engine.py config/games/hitman3.yaml 0 0 --validate-only
```
Expected: All report valid with step counts.

**Step 4: Run `--list-steps` on a config**

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 0 0 --list-steps`
Expected: Numbered steps with action types.

**Step 5: Run `--dry-run` on a config**

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080 --dry-run --game-path "C:\Games\Cyberpunk.exe"`
Expected: Execution plan printed, no SUT connection attempted.

**Step 6: Run `--config-dump`**

Run: `python vcap_execution_engine.py config/games/cyberpunk2077.yaml 0 0 --config-dump`
Expected: Full YAML dumped to stdout.

**Step 7: Run `--list-games`**

Run: `python vcap_execution_engine.py --list-games dummy 0 0`
Expected: All games from config/games/ listed.

**Step 8: Commit if any fixes were needed**

```bash
git add vcap_execution_engine.py
git commit -m "fix: Final cleanup and verification fixes"
```
