# Implementation Plan: Sideload & Hooks Features

## Status: ✅ IMPLEMENTED

## Overview
Two new features added to the game automation system:
1. **Hooks** - Run executables before step 1 starts and after the last step completes (with support for long-running tools)
2. **Sideload** - Run executables within any automation step

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

## Feature 2: Sideload (In-Step Executable Execution)

### YAML Schema
```yaml
steps:
  3:
    description: "Run pre-benchmark preparation"
    action:
      type: "sideload"
      path: "C:\\Scripts\\configure_settings.ps1"
      args: ["-Resolution", "1920x1080"]
      timeout: 60
      wait_for_completion: true
      check_exit_code: true
    expected_delay: 2

  5:
    description: "Start background trace"
    action:
      type: "sideload"
      path: "C:\\Tools\\trace.exe"
      args: ["--start"]
      wait_for_completion: false  # Fire and forget
    expected_delay: 1
```

### Sideload Fields
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | required | Must be "sideload" |
| `path` | string | required | Path to executable |
| `args` | list | [] | Command line arguments |
| `timeout` | int | 300 | Max seconds to wait |
| `working_dir` | string | parent of path | Working directory |
| `wait_for_completion` | bool | true | Block until process completes |
| `check_exit_code` | bool | true | Fail step if exit code != 0 |
| `shell` | bool | auto | Run in shell |

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
- Added `_handle_sideload_action()` - handles sideload action type
- Modified `run()` to call hooks at appropriate times
- Modified `_execute_modular_action()` to dispatch sideload actions

### 4. `modules/simple_config_parser.py` ✅
- Added `VALID_ACTION_TYPES` set including "sideload"
- Added `_validate_hooks()` - validates hooks section structure
- Added `_validate_hook_entry()` - validates individual hook entries
- Added `_validate_sideload_action()` - validates sideload action config

### 5. `workflow_builder.py` ✅
- Added "Sideload" action type to action grid (Column 3)
- Added sideload_frame with path, args, timeout, wait, check_exit_code fields
- Added hooks support in `save_yaml()` and `load_yaml()`
- Added `show_hooks_editor()` - dialog to manage pre/post hooks
- Added `_add_hook_dialog()` - dialog to add individual hooks
- Added `show_yaml_reference()` - comprehensive YAML reference dialog
- Added yellow "? Help" button for quick YAML reference
- Added "⚙ Hooks" button for hooks editor
- Updated `on_action_change()` to show/hide sideload frame
- Updated `on_ok()` to build sideload action config
- Updated `WorkflowStep.to_dict()` to handle sideload
- Updated `new_workflow()` to reset hooks

---

## Execution Flow

```
run() called
    │
    ├── _execute_pre_hooks()  ← Run non-persistent pre hooks (wait for each)
    │
    ├── _start_persistent_hooks()  ← Start persistent hooks (no wait)
    │
    ├── while current_step <= len(steps):
    │       │
    │       ├── if action.type == "sideload":
    │       │       └── _handle_sideload_action()
    │       │
    │       └── (other action types...)
    │
    ├── _stop_persistent_hooks()  ← Terminate all persistent hooks
    │
    └── _execute_post_hooks()  ← Run non-persistent post hooks (wait for each)
```

---

## UI Changes

### Workflow Builder
1. **New Sideload action type** in "Other Actions" column
2. **Sideload configuration panel** with:
   - Path input field
   - Arguments field (comma-separated)
   - Timeout field
   - Wait for completion checkbox
   - Check exit code checkbox

3. **Edit → Edit Hooks...** menu option opens hooks editor dialog
4. **Help → YAML Reference** menu option opens full YAML reference
5. **Yellow "? Help" button** on toolbar for quick reference
6. **"⚙ Hooks" button** on toolbar for hooks editor

---

## Example Complete YAML

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
    description: "Configure resolution"
    action:
      type: "sideload"
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
    description: "Wait for benchmark"
    action:
      type: "wait"
      duration: 120
    expected_delay: 0
```

---

## Implementation Date
- **Completed**: January 24, 2026
