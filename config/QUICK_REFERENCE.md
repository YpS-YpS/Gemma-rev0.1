# Step-Based YAML Quick Reference

## Basic Structure

```yaml
metadata:
  game_name: "Game Name"
  path: "C:\\Path\\To\\Game.exe"
  benchmark_duration: 120

steps:
  1:
    description: "What this step does"
    find_and_click:  # or action:
      type: "icon"
      text: "Button Text"
    verify_success:
      - type: "icon"
        text: "Expected Element"
    expected_delay: 2
    timeout: 20
```

## Element Types

| Type | Matches | Use When |
|------|---------|----------|
| `"icon"` | Interactive elements only | Clicking buttons, icons |
| `"text"` | Text elements only | Reading labels, status |
| `"any"` | Both icon and text | Flexible matching |

## Text Matching Strategies

| Strategy | Behavior | Example |
|----------|----------|---------|
| `"exact"` | Must match exactly | `"PLAY"` matches only `"PLAY"` |
| `"contains"` | Must contain text | `"PLAY"` matches `"PLAY NOW"` |
| `"startswith"` | Must start with text | `"PLAY"` matches `"PLAY NOW"` |
| `"endswith"` | Must end with text | `"NOW"` matches `"PLAY NOW"` |

## Action Types Cheat Sheet

### Click Actions
```yaml
find_and_click:
  type: "icon"
  text: "PLAY"
```

### Keyboard
```yaml
action:
  type: "key"
  key: "escape"  # enter, space, tab, f1-f12, etc.
```

### Hotkey
```yaml
action:
  type: "hotkey"
  keys: ["ctrl", "shift", "c"]
```

### Type Text
```yaml
action:
  type: "text"
  text: "username123"
  char_delay: 0.05
```

### Wait
```yaml
action: "wait"
duration: 10
```

### Scroll
```yaml
action:
  type: "scroll"
  direction: "down"  # or "up"
  clicks: 5
  x: 960
  y: 540
```

### Drag
```yaml
action:
  type: "drag"
  end_x: 500
  end_y: 300
  duration: 1.0
```

### Right Click
```yaml
action:
  type: "right_click"  # also: double_click, middle_click
```

### Sequence
```yaml
action:
  type: "sequence"
  delay_between: 0.5
  actions:
    - type: "key"
      key: "escape"
    - type: "wait"
      duration: 1
    - type: "click"
      x: 960
      y: 540
```

### Conditional
```yaml
action:
  type: "conditional"
  check_element:
    type: "icon"
    text: "OK"
  if_found:
    type: "click"
  if_not_found:
    type: "wait"
    duration: 1
```

## Verification Strategies

```yaml
# Multiple elements (all must be found)
verify_success:
  - type: "icon"
    text: "Element 1"
  - type: "text"
    text: "Element 2"

# No verification
verify_success: []

# Loading screen (skip verification)
verify_success: [loading]
```

## Optional Steps (Popup Handlers)

```yaml
optional_steps:
  close_popup:
    description: "Close popup if appears"
    trigger:
      type: "icon"
      text: "Close"
    action:
      type: "click"
    max_checks: 10
    priority: 1
```

## Fallback Actions

```yaml
fallbacks:
  general:
    action: "key"
    key: "escape"
    expected_delay: 1
```

## Common Keys

```
enter, return, space, tab, escape, esc
backspace, delete
shift, ctrl, alt, win
f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11, f12
up, down, left, right
home, end, pageup, pagedown
```

## Timing Parameters

| Parameter | Where Used | Purpose |
|-----------|------------|---------|
| `expected_delay` | Every step | Wait after action before verification |
| `timeout` | Every step | Max time to wait for element |
| `duration` | Wait action | How long to wait |
| `move_duration` | Click action | Time to move mouse |
| `click_delay` | Click action | Pause before clicking |
| `char_delay` | Text action | Delay between characters |
| `delay_between` | Sequence action | Delay between actions |

## Common Patterns

### Pattern: Click and Verify
```yaml
1:
  description: "Click button"
  find_and_click:
    type: "icon"
    text: "PLAY"
  verify_success:
    - type: "icon"
      text: "Next Menu"
  expected_delay: 2
  timeout: 15
```

### Pattern: Press Key
```yaml
2:
  description: "Press Escape"
  action:
    type: "key"
    key: "escape"
  verify_success: []
  expected_delay: 1
  timeout: 5
```

### Pattern: Wait for Loading
```yaml
3:
  description: "Wait for load"
  action: "wait"
  duration: 30
  verify_success: [loading]
  timeout: 35
```

### Pattern: Handle Popup
```yaml
optional_steps:
  popup:
    trigger:
      type: "icon"
      text: "OK"
    action:
      type: "click"
    max_checks: 5
```

## Troubleshooting Quick Guide

| Problem | Solution |
|---------|----------|
| Element not found | Try `type: "any"` and `text_match: "contains"` |
| Timeout errors | Increase `timeout` value |
| Wrong element clicked | Use `text_match: "exact"` |
| Action too fast | Increase `expected_delay` |
| Key not recognized | Use lowercase, check supported keys list |
| Verification fails | Check logs for available elements |
| Mouse misses target | Check screen resolution matches config |

## File Locations

- **Your configs:** `config/games/yourconfig.yaml`
- **Screenshots:** `logs/[Game Name]/screenshots/`
- **Annotations:** `logs/[Game Name]/annotated/`
- **Logs:** `logs/[Game Name]/automation.log`

## Example: Complete Simple Benchmark

```yaml
metadata:
  game_name: "My Game"
  path: "C:\\Games\\MyGame\\game.exe"
  benchmark_duration: 120
  startup_wait: 30

steps:
  1:
    description: "Click PLAY"
    find_and_click:
      type: "icon"
      text: "PLAY"
      text_match: "contains"
    verify_success:
      - type: "icon"
        text: "Settings"
    expected_delay: 2
    timeout: 20

  2:
    description: "Open Settings"
    find_and_click:
      type: "icon"
      text: "Settings"
      text_match: "contains"
    verify_success:
      - type: "icon"
        text: "Graphics"
    expected_delay: 3
    timeout: 15

  3:
    description: "Start Benchmark"
    find_and_click:
      type: "icon"
      text: "Benchmark"
      text_match: "contains"
    verify_success: [loading]
    expected_delay: 3
    timeout: 20

  4:
    description: "Wait for benchmark"
    action: "wait"
    duration: 120
    verify_success: [loading]
    timeout: 125

  5:
    description: "Close results"
    find_and_click:
      type: "icon"
      text: "Close"
      text_match: "contains"
    verify_success: []
    expected_delay: 2
    timeout: 15

fallbacks:
  general:
    action: "key"
    key: "escape"
    expected_delay: 1
```

## Tips for Success

1. **Start simple** - Get basic clicks working first
2. **Use "any" type** - Most flexible for element matching
3. **Use "contains" matching** - Most forgiving text matching
4. **Test each step** - Verify one step at a time
5. **Check logs** - Screenshots show what was detected
6. **Increase timeouts** - Better too long than too short
7. **Add optional_steps** - Handle common popups
8. **Use fallbacks** - Escape key usually works
9. **Be patient** - Games can be slow to respond
10. **Read error messages** - They tell you what went wrong

## Need More Help?

- **Full reference:** `COMPREHENSIVE_TEMPLATE_STEPS.yaml`
- **Examples:** `EXAMPLES_COMMON_SCENARIOS.yaml`
- **Documentation:** `README.md`
- **Existing configs:** `config/games/*.yaml`
