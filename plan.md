# Implementation Plan: Sideload & Hooks Features

## Status: ✅ IMPLEMENTED (Updated)

## Overview
Two new features added to the game automation system:
1. **Hooks** - Run executables before step 1 starts and after the last step completes (with support for long-running tools)
2. **Sideload** - Run executables within any automation step (as a separate attribute, not an action type)

---

## Feature 1: Hooks (Pre/Post Automation Executables)

### YAML Schema
```yaml
metadata:
  game_name: "Cyberpunk2077"

hooks:
  pre:
    # Run once before step 1 - waits for completion
    - path: "C:\\Scripts\\setup_environment.bat"
      args: ["--profile", "gaming"]
      timeout: 30
      working_dir: "C:\\Scripts"

    # Long-running tool - starts before step 1, stops after last step
    - path: "C:\\Tools\\GPUTrace\\trace.exe"
      args: ["--output", "C:\\Logs\\trace.etl"]
      persistent: true  # KEY: Runs throughout automation

  post:
    # Run after all steps complete
    - path: "C:\\Scripts\\collect_results.py"
      args: ["--run-id", "${RUN_ID}"]
      timeout: 60

steps:
  1:
    description: "Click Play"
    # ...
```

### Hook Fields
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | string | required | Path to executable (.py, .bat, .ps1, .exe, .cmd) |
| `args` | list | [] | Command line arguments |
| `timeout` | int | 300 | Max seconds to wait (ignored if persistent) |
| `working_dir` | string | parent of path | Working directory |
| `persistent` | bool | false | If true, starts before step 1, terminates after last step |
| `shell` | bool | auto | Run in shell (auto-detected by extension) |

---

## Feature 2: Sideload (Step Attribute for Script Execution)

**UPDATED**: Sideload is now a **separate step attribute**, not an action type. This allows:
- Combining any action (click, key, wait) with a sideload script
- Steps that only run a sideload (no action required)
- More flexibility for users

### YAML Schema (Updated)
```yaml
steps:
  # Step with action AND sideload (sideload runs AFTER action)
  2:
    description: "Click Play and run setup script"
    find:
      type: "button"
      text: "PLAY"
    action:
      type: "click"
    sideload:                          # Separate attribute!
      path: "C:\\Scripts\\setup.ps1"
      args: ["-Config", "high"]
      timeout: 60
      wait_for_completion: true
      check_exit_code: true
    expected_delay: 5

  # Step with ONLY sideload (no action)
  3:
    description: "Run configuration script"
    sideload:
      path: "C:\\Scripts\\configure_settings.ps1"
      args: ["-Resolution", "1920x1080"]
      timeout: 60
      wait_for_completion: true
    expected_delay: 2

  # Step with action only (no sideload)
  4:
    description: "Wait for benchmark"
    action:
      type: "wait"
      duration: 120
    expected_delay: 0
```

### Sideload Fields
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | string | required | Path to executable |
| `args` | list | [] | Command line arguments |
| `timeout` | int | 300 | Max seconds to wait |
| `working_dir` | string | parent of path | Working directory |
| `wait_for_completion` | bool | true | Block until process completes |
| `check_exit_code` | bool | true | Fail step if exit code != 0 |

---

## Files Modified

### 1. `modules/network.py` ✅
- Added `execute_command()` method - sends POST to `/execute` on SUT
- Added `terminate_process()` method - sends POST to `/terminate` on SUT

### 2. `sut_service_installer/gemma_service_0.1.py` ✅
- Added `/execute` endpoint - runs scripts (.py, .bat, .ps1, .exe, .cmd)
- Added `/terminate` endpoint - terminates background processes by PID
- Supports sync (wait) and async (fire-and-forget) execution
- Auto-detects shell mode from file extension

### 3. `modules/simple_automation.py` ✅
- Added `self.hooks` and `self.persistent_processes` tracking
- Added `_execute_pre_hooks()` - runs non-persistent pre hooks
- Added `_start_persistent_hooks()` - starts persistent hooks (no wait)
- Added `_stop_persistent_hooks()` - terminates persistent hooks after automation
- Added `_execute_post_hooks()` - runs post hooks
- Added `_handle_sideload_action()` - handles sideload execution
- **Updated**: `_process_step_modular()` handles sideload as separate step attribute
- Sideload runs AFTER action completes (if both present)
- Steps can have action only, sideload only, or both

### 4. `modules/simple_config_parser.py` ✅
- Added `VALID_ACTION_TYPES` set (sideload removed - it's now a step attribute)
- Added `_validate_hooks()` - validates hooks section structure
- Added `_validate_hook_entry()` - validates individual hook entries
- **Updated**: `_validate_sideload()` - validates sideload step attribute
- Updated validation to allow: action OR sideload OR both

### 5. `workflow_builder.py` ✅
- **Updated**: Sideload is now a toggleable section, not an action type
- Sideload frame with "Enable Sideload" checkbox
- Sideload fields: path, args, timeout, wait, check_exit_code
- Added hooks support in `save_yaml()` and `load_yaml()`
- Added `show_hooks_editor()` - dialog to manage pre/post hooks
- Added `_add_hook_dialog()` - dialog to add individual hooks
- Added `show_yaml_reference()` - comprehensive help dialog with tabs
- Added yellow "? Help" button for quick reference
- Added "⚙ Hooks" button for hooks editor
- Updated `WorkflowStep` class with `sideload_config` attribute
- Updated `WorkflowStep.to_dict()` to output sideload as step attribute
- Updated `load_yaml()` to load sideload from step attribute

---

## Execution Flow (Updated)

```
run() called
    │
    ├── _execute_pre_hooks()  ← Run non-persistent pre hooks (wait for each)
    │
    ├── _start_persistent_hooks()  ← Start persistent hooks (no wait)
    │
    ├── while current_step <= len(steps):
    │       │
    │       ├── Execute ACTION (if present): click, wait, key, etc.
    │       │
    │       └── Execute SIDELOAD (if present): runs AFTER action
    │
    ├── _stop_persistent_hooks()  ← Terminate all persistent hooks
    │
    └── _execute_post_hooks()  ← Run non-persistent post hooks (wait for each)
```

---

## UI Changes

### Workflow Builder
1. **Sideload section** (separate from actions):
   - "Enable Sideload" checkbox
   - Path input field
   - Arguments field (comma-separated)
   - Timeout field
   - Wait for completion checkbox
   - Check exit code checkbox

2. **Edit → Edit Hooks...** menu option opens hooks editor dialog
3. **Help → YAML Reference** menu option opens full help guide
4. **Yellow "? Help" button** on toolbar for quick reference
5. **"⚙ Hooks" button** on toolbar for hooks editor

---

## Example Complete YAML (Updated)

```yaml
metadata:
  game_name: "Benchmark Test"
  path: "730"  # CS2 Steam ID
  process_id: "cs2"

hooks:
  pre:
    - path: "D:\\Tools\\GPUView\\trace.cmd"
      args: ["--start"]
      persistent: true

    - path: "D:\\Scripts\\clear_logs.bat"
      timeout: 10

  post:
    - path: "D:\\Scripts\\collect_traces.py"
      args: ["--output", "D:\\Results"]
      timeout: 120

steps:
  1:
    description: "Configure resolution (sideload only)"
    sideload:
      path: "D:\\Scripts\\set_resolution.ps1"
      args: ["-Width", "1920", "-Height", "1080"]
      timeout: 30
    expected_delay: 2

  2:
    description: "Click Play"
    find:
      type: "button"
      text: "PLAY"
    action:
      type: "click"
    expected_delay: 5

  3:
    description: "Click Settings and run config script"
    find:
      type: "icon"
      text: "Settings"
    action:
      type: "click"
    sideload:
      path: "D:\\Scripts\\apply_settings.py"
      args: ["--preset", "ultra"]
      timeout: 30
    expected_delay: 2

  4:
    description: "Wait for benchmark"
    action:
      type: "wait"
      duration: 120
    expected_delay: 0
```

---

## Implementation Date
- **Initial**: January 24, 2026
- **Updated (Sideload as attribute)**: January 24, 2026
