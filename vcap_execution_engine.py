#!/usr/bin/env python3
"""
VCAP Execution Engine - Headless CLI for the VCAP Automation Framework.

Wraps the SimpleAutomation pipeline into a single command-line invocable script,
enabling parallel multi-SUT execution without the Tkinter GUI.

Usage:
    vcap_execution_engine.py <input_yaml> <sut_ip> <sut_port> [OPTIONS]

Parallel multi-SUT:
    vcap_execution_engine.py config/games/cyberpunk2077.yaml 192.168.1.10 8080 &
    vcap_execution_engine.py config/games/cs2_benchmark.yaml  192.168.1.11 8080 &
"""

import os
import sys
import re
import time
import json
import glob
import yaml
import logging
import argparse
from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"

VCAP_BANNER = """
 __     __ ____    _    ____
 \\ \\   / // ___|  / \\  |  _ \\
  \\ \\ / /| |     / _ \\ | |_) |
   \\ V / | |___ / ___ \\|  __/
    \\_/   \\____/_/   \\_\\_|
  VCAP Execution Engine v{}
""".format(__version__)


# ============================================================================
# ARGPARSE BUILDER
# ============================================================================

def build_parser():
    """Build the argument parser with all ~70 arguments across 13 groups."""

    parser = argparse.ArgumentParser(
        prog="vcap_execution_engine.py",
        description="VCAP Execution Engine - Headless CLI for vision-based game UI automation",
        epilog="Documentation: docs/VCAP_CLI_REFERENCE.md | Version: %(prog)s v" + __version__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # -- Positional Arguments ----------------------------------------------
    # nargs='?' makes them optional so --version / --list-games work without them
    parser.add_argument(
        "input_yaml", type=str, nargs="?", default=None,
        help="Path to YAML workflow configuration file"
    )
    parser.add_argument(
        "sut_ip", type=str, nargs="?", default=None,
        help="IP address of the System Under Test"
    )
    parser.add_argument(
        "sut_port", type=int, nargs="?", default=None,
        help="Port of the SUT service"
    )

    # -- 1. Execution Identity ---------------------------------------------
    identity = parser.add_argument_group(
        "Execution Identity",
        "Control execution identification and batch grouping"
    )
    identity.add_argument(
        "--id", type=str, default=None, dest="execution_id",
        help="Execution ID override (default: auto-generated VCAP-{game}-{timestamp})"
    )
    identity.add_argument(
        "--batch-id", type=str, default=None,
        help="Group multiple runs under a shared batch ID for unified reporting"
    )

    # -- 2. Execution Control ----------------------------------------------
    execution = parser.add_argument_group(
        "Execution Control",
        "Control run behavior, retries, and failure handling"
    )
    execution.add_argument(
        "--run-count", type=int, default=1,
        help="Number of times to run the workflow (default: 1)"
    )
    execution.add_argument(
        "--run-delay", type=int, default=30,
        help="Seconds to wait between runs (default: 30)"
    )
    execution.add_argument(
        "--max-retries", type=int, default=3,
        help="Max retries per failed step (default: 3)"
    )
    execution.add_argument(
        "--retry-delay", type=float, default=2.0,
        help="Seconds between retries (default: 2.0)"
    )
    execution.add_argument(
        "--startup-wait", type=int, default=None,
        help="Seconds to wait after game launch (default: from YAML metadata)"
    )
    execution.add_argument(
        "--step-timeout", type=int, default=60,
        help="Global per-step timeout override in seconds (default: 60)"
    )
    execution.add_argument(
        "--abort-on-hook-failure", action="store_true", default=False,
        help="Abort execution if any hook fails"
    )
    execution.add_argument(
        "--continue-on-sideload-failure", action="store_true", default=False,
        help="Continue execution when sideload script fails"
    )
    execution.add_argument(
        "--fail-fast", action="store_true", default=False,
        help="Exit immediately on first step failure without retries"
    )
    execution.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Validate config and print execution plan without connecting to SUT"
    )

    # -- 3. Vision Model --------------------------------------------------
    vision = parser.add_argument_group(
        "Vision Model Configuration",
        "Configure the computer vision backend for UI element detection"
    )
    vision.add_argument(
        "--vision-model", type=str, default="omniparser",
        choices=["gemma", "qwen", "omniparser"],
        help="Vision model backend (default: omniparser)"
    )
    vision.add_argument(
        "--vision-model-url", type=str, default=None,
        help="URL for vision model API endpoint (default: model-dependent)"
    )
    vision.add_argument(
        "--vision-temperature", type=float, default=0.01,
        help="LLM sampling temperature for vision model (default: 0.01)"
    )
    vision.add_argument(
        "--vision-max-tokens", type=int, default=800,
        help="Max response tokens from vision model (default: 800)"
    )
    vision.add_argument(
        "--vision-timeout", type=int, default=60,
        help="Vision model request timeout in seconds (default: 60)"
    )
    vision.add_argument(
        "--vision-max-elements", type=int, default=10,
        help="Max UI elements to detect per frame (default: 10)"
    )
    vision.add_argument(
        "--vision-confidence-threshold", type=float, default=0.6,
        help="Minimum confidence threshold for element matching (default: 0.6)"
    )
    vision.add_argument(
        "--omniparser-box-threshold", type=float, default=0.05,
        help="OmniParser detection box confidence threshold (default: 0.05)"
    )
    vision.add_argument(
        "--omniparser-iou-threshold", type=float, default=0.1,
        help="OmniParser intersection-over-union threshold (default: 0.1)"
    )
    vision.add_argument(
        "--omniparser-use-paddleocr", action="store_true", default=True,
        help="Enable PaddleOCR in OmniParser (default: enabled)"
    )
    vision.add_argument(
        "--no-paddleocr", action="store_true", default=False,
        help="Disable PaddleOCR in OmniParser"
    )

    # -- 4. Game Launch ----------------------------------------------------
    game = parser.add_argument_group(
        "Game Launch",
        "Control game process launch and lifecycle management"
    )
    game.add_argument(
        "--game-path", type=str, default=None,
        help="Path to game executable or Steam app URL on the SUT"
    )
    game.add_argument(
        "--process-id", type=str, default=None,
        help="Process name to track after launch (e.g., 'Cyberpunk2077')"
    )
    game.add_argument(
        "--no-launch", action="store_true", default=False,
        help="Skip game launch (assume game is already running)"
    )
    game.add_argument(
        "--kill-on-failure", action="store_true", default=True,
        help="Kill game process on automation failure (default: enabled)"
    )
    game.add_argument(
        "--no-kill-on-failure", action="store_true", default=False,
        help="Leave game running even on automation failure"
    )

    # -- 5. Steam Integration ---------------------------------------------
    steam = parser.add_argument_group(
        "Steam Integration",
        "Automated Steam login before game launch"
    )
    steam.add_argument(
        "--steam-login", action="store_true", default=False,
        help="Enable Steam login before game launch"
    )
    steam.add_argument(
        "--steam-username", type=str, default=None,
        help="Steam account username"
    )
    steam.add_argument(
        "--steam-password", type=str, default=None,
        help="Steam account password"
    )
    steam.add_argument(
        "--steam-login-timeout", type=int, default=120,
        help="Steam login timeout in seconds (default: 120)"
    )

    # -- 6. Logging & Output ----------------------------------------------
    log = parser.add_argument_group(
        "Logging & Output",
        "Control log verbosity, format, and artifact output"
    )
    log.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO)"
    )
    log.add_argument(
        "--log-dir", type=str, default="logs",
        help="Base directory for log output (default: logs/)"
    )
    log.add_argument(
        "--run-dir", type=str, default=None,
        help="Override the run-specific output directory"
    )
    log.add_argument(
        "--log-format", type=str, default="detailed",
        choices=["detailed", "compact", "json"],
        help="Log format style (default: detailed)"
    )
    log.add_argument(
        "--log-file", type=str, default=None,
        help="Explicit path for the log file"
    )
    log.add_argument(
        "-q", "--quiet", action="store_true", default=False,
        help="Suppress stdout output, log to file only"
    )
    log.add_argument(
        "-v", "--verbose", action="store_true", default=False,
        help="Enable DEBUG logging (alias for --log-level DEBUG)"
    )
    log.add_argument(
        "--no-screenshots", action="store_true", default=False,
        help="Skip saving screenshots to disk"
    )
    log.add_argument(
        "--no-annotations", action="store_true", default=False,
        help="Skip generating annotated screenshots"
    )
    log.add_argument(
        "--save-json-response", action="store_true", default=True,
        help="Save vision model JSON responses (default: enabled)"
    )
    log.add_argument(
        "--no-json-response", action="store_true", default=False,
        help="Disable saving vision model JSON responses"
    )

    # -- 7. Network Tuning ------------------------------------------------
    network = parser.add_argument_group(
        "Network Tuning",
        "Fine-tune SUT communication timeouts and connection behavior"
    )
    network.add_argument(
        "--network-timeout", type=int, default=10,
        help="Timeout for SUT action commands in seconds (default: 10)"
    )
    network.add_argument(
        "--screenshot-timeout", type=int, default=15,
        help="Timeout for screenshot capture in seconds (default: 15)"
    )
    network.add_argument(
        "--launch-timeout", type=int, default=90,
        help="Timeout for game launch request in seconds (default: 90)"
    )
    network.add_argument(
        "--connection-check", action="store_true", default=True,
        help="Verify SUT connectivity on startup (default: enabled)"
    )
    network.add_argument(
        "--no-connection-check", action="store_true", default=False,
        help="Skip SUT connectivity verification on startup"
    )

    # -- 8. Hooks & Sideload ----------------------------------------------
    hooks = parser.add_argument_group(
        "Hooks & Sideload",
        "Manage pre/post-automation hooks and per-step sideload scripts"
    )
    hooks.add_argument(
        "--pre-hook", type=str, action="append", default=None,
        help="Additional pre-hook script path (repeatable)"
    )
    hooks.add_argument(
        "--post-hook", type=str, action="append", default=None,
        help="Additional post-hook script path (repeatable)"
    )
    hooks.add_argument(
        "--hook-timeout", type=int, default=300,
        help="Default timeout for hook execution in seconds (default: 300)"
    )
    hooks.add_argument(
        "--no-hooks", action="store_true", default=False,
        help="Disable all hooks (pre, post, and persistent)"
    )
    hooks.add_argument(
        "--no-sideload", action="store_true", default=False,
        help="Disable all step sideload scripts"
    )
    hooks.add_argument(
        "--sideload-timeout", type=int, default=300,
        help="Default sideload execution timeout in seconds (default: 300)"
    )

    # -- 9. Input Control Tuning ------------------------------------------
    input_ctrl = parser.add_argument_group(
        "Input Control Tuning",
        "Fine-tune mouse movement, click timing, and keyboard input parameters"
    )
    input_ctrl.add_argument(
        "--click-move-duration", type=float, default=0.5,
        help="Mouse movement duration before click in seconds (default: 0.5)"
    )
    input_ctrl.add_argument(
        "--click-delay", type=float, default=0.1,
        help="Delay between mouse down/up in seconds (default: 0.1)"
    )
    input_ctrl.add_argument(
        "--type-char-delay", type=float, default=0.05,
        help="Delay between keystrokes for text input in seconds (default: 0.05)"
    )
    input_ctrl.add_argument(
        "--scroll-clicks", type=int, default=3,
        help="Default scroll wheel clicks per scroll action (default: 3)"
    )
    input_ctrl.add_argument(
        "--drag-duration", type=float, default=1.0,
        help="Duration for drag operations in seconds (default: 1.0)"
    )
    input_ctrl.add_argument(
        "--drag-steps", type=int, default=20,
        help="Interpolation steps for drag smoothing (default: 20)"
    )

    # -- 10. Text Matching ------------------------------------------------
    matching = parser.add_argument_group(
        "Text Matching",
        "Configure UI element text matching behavior"
    )
    matching.add_argument(
        "--default-match-strategy", type=str, default="contains",
        choices=["exact", "contains", "startswith", "endswith"],
        help="Default text matching strategy (default: contains)"
    )
    matching.add_argument(
        "--case-sensitive", action="store_true", default=False,
        help="Enable case-sensitive text matching"
    )

    # -- 11. Campaign Mode ------------------------------------------------
    campaign = parser.add_argument_group(
        "Campaign Mode",
        "Execute multiple games sequentially from a campaign JSON file"
    )
    campaign.add_argument(
        "--campaign", type=str, default=None,
        help="Path to campaign JSON file (overrides input_yaml for multi-game execution)"
    )
    campaign.add_argument(
        "--delay-between-games", type=int, default=120,
        help="Seconds between games in campaign mode (default: 120)"
    )
    campaign.add_argument(
        "--continue-on-failure", action="store_true", default=True,
        help="Continue campaign after individual game failure (default: enabled)"
    )
    campaign.add_argument(
        "--no-continue-on-failure", action="store_true", default=False,
        help="Stop campaign on first game failure"
    )

    # -- 12. Reporting ----------------------------------------------------
    reporting = parser.add_argument_group(
        "Reporting",
        "Configure execution reports, summaries, and output formats"
    )
    reporting.add_argument(
        "--report-format", type=str, default="text",
        choices=["text", "json", "junit"],
        help="Report output format (default: text)"
    )
    reporting.add_argument(
        "--report-file", type=str, default=None,
        help="Write execution report to file"
    )
    reporting.add_argument(
        "--exit-code-mode", type=str, default="binary",
        choices=["binary", "step-count"],
        help="Exit code mode: binary (0/1) or step-count (default: binary)"
    )
    reporting.add_argument(
        "--timing", action="store_true", default=False,
        help="Print per-step timing summary after execution"
    )
    reporting.add_argument(
        "--summary", action="store_true", default=True,
        help="Print execution summary on completion (default: enabled)"
    )
    reporting.add_argument(
        "--no-summary", action="store_true", default=False,
        help="Suppress execution summary output"
    )

    # -- 13. Introspection & Utilities ------------------------------------
    introspect = parser.add_argument_group(
        "Introspection & Utilities",
        "Validate configs, inspect workflows, and override settings without execution"
    )
    introspect.add_argument(
        "--validate-only", action="store_true", default=False,
        help="Validate YAML configuration and exit"
    )
    introspect.add_argument(
        "--config-dump", action="store_true", default=False,
        help="Dump merged configuration to stdout and exit"
    )
    introspect.add_argument(
        "--list-steps", action="store_true", default=False,
        help="List workflow steps from configuration and exit"
    )
    introspect.add_argument(
        "--list-games", action="store_true", default=False,
        help="List available game configurations from config/games/ and exit"
    )
    introspect.add_argument(
        "--override", type=str, action="append", default=None, metavar="KEY=VALUE",
        help="Override YAML config values at runtime using dot notation (repeatable)"
    )
    introspect.add_argument(
        "--env-file", type=str, default=None,
        help="Load environment variables from .env file before execution"
    )
    introspect.add_argument(
        "--version", action="store_true", default=False,
        help="Print VCAP version and exit"
    )

    return parser


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def generate_execution_id(game_name):
    """Generate a unique execution ID from game name and timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^a-zA-Z0-9]', '', game_name)
    return f"VCAP-{safe_name}-{timestamp}"


def list_available_games():
    """List all available game configurations."""
    games = []
    for config_path in sorted(glob.glob("config/games/*.yaml")):
        game_name = os.path.basename(config_path).replace('.yaml', '')
        games.append((game_name, config_path))
    return games


def setup_logging(args, execution_id, run_dir):
    """
    Configure logging based on CLI arguments.

    Returns:
        tuple: (logger, log_file_path)
    """
    # Determine log level
    if args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = getattr(logging, args.log_level, logging.INFO)

    # Determine log file path
    if args.log_file:
        log_file = args.log_file
    else:
        log_file = os.path.join(run_dir, "automation.log")

    # Ensure log directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Configure log format
    if args.log_format == "detailed":
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
    elif args.log_format == "compact":
        fmt = "%(asctime)s %(levelname).1s %(message)s"
        datefmt = "%H:%M:%S"
    elif args.log_format == "json":
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'
        datefmt = "%Y-%m-%dT%H:%M:%S"
    else:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"

    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # Root logger setup
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # File handler (always active)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Console handler (unless --quiet)
    if not args.quiet:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    logger = logging.getLogger("VCAP")
    return logger, log_file


def create_run_directory(args, execution_id, run_num=1):
    """
    Create the directory structure for a run.

    Returns:
        str: Path to the run directory
    """
    if args.run_dir:
        run_dir = args.run_dir
    else:
        base = args.log_dir
        if args.batch_id:
            base = os.path.join(base, args.batch_id)
        run_dir = os.path.join(base, execution_id, f"run_{run_num}")

    os.makedirs(run_dir, exist_ok=True)
    os.makedirs(os.path.join(run_dir, "screenshots"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "annotated"), exist_ok=True)

    return run_dir


def init_vision_model(args, logger):
    """
    Initialize the vision model client based on CLI arguments.

    Returns:
        Vision model client instance
    """
    from modules.gemma_client import GemmaClient
    from modules.qwen_client import QwenClient
    from modules.omniparser_client import OmniparserClient

    url = args.vision_model_url

    if args.vision_model == "omniparser":
        if not url:
            url = "http://localhost:8000"
        logger.info(f"Initializing OmniParser vision model at {url}")
        return OmniparserClient(url)
    elif args.vision_model == "gemma":
        if not url:
            url = "http://127.0.0.1:1234"
        logger.info(f"Initializing Gemma vision model at {url}")
        return GemmaClient(url)
    elif args.vision_model == "qwen":
        if not url:
            url = "http://127.0.0.1:1234"
        logger.info(f"Initializing Qwen VL vision model at {url}")
        return QwenClient(url)
    else:
        raise ValueError(f"Unknown vision model: {args.vision_model}")


def apply_config_overrides(config, args):
    """
    Apply CLI overrides to the loaded YAML configuration.

    Modifies config in-place based on CLI flags:
    - --no-hooks: removes hooks section
    - --no-sideload: removes sideload from all steps
    - --pre-hook / --post-hook: injects additional hooks
    - --override: applies dot-notation key=value overrides
    """
    # Strip hooks if disabled
    if args.no_hooks:
        config.pop("hooks", None)

    # Strip sideload from all steps if disabled
    if args.no_sideload:
        for step in config.get("steps", {}).values():
            step.pop("sideload", None)

    # Inject CLI pre-hooks
    if args.pre_hook:
        hooks = config.setdefault("hooks", {})
        pre = hooks.setdefault("pre", [])
        for hook_path in args.pre_hook:
            pre.append({"path": hook_path, "timeout": args.hook_timeout})

    # Inject CLI post-hooks
    if args.post_hook:
        hooks = config.setdefault("hooks", {})
        post = hooks.setdefault("post", [])
        for hook_path in args.post_hook:
            post.append({"path": hook_path, "timeout": args.hook_timeout})

    # Apply dot-notation overrides
    if args.override:
        for override_str in args.override:
            if "=" not in override_str:
                print(f"Warning: Invalid override format '{override_str}', expected KEY=VALUE", file=sys.stderr)
                continue
            key, value = override_str.split("=", 1)
            parts = key.split(".")
            target = config
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = yaml.safe_load(value)

    return config


def load_env_file(env_path):
    """Load environment variables from a .env file."""
    if not os.path.exists(env_path):
        print(f"Error: env file not found: {env_path}", file=sys.stderr)
        sys.exit(2)

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


# ============================================================================
# INTROSPECTION COMMANDS
# ============================================================================

def handle_introspection(args):
    """
    Handle introspection/utility commands that exit before connecting to SUT.

    Returns:
        True if an introspection command was handled (caller should exit).
        False if no introspection command matched (continue to execution).
    """
    # --version
    if args.version:
        print(f"VCAP Execution Engine v{__version__}")
        return True

    # --list-games (doesn't need a real input_yaml)
    if args.list_games:
        games = list_available_games()
        if games:
            print(f"Available game configurations ({len(games)}):")
            print()
            for game_name, config_path in games:
                print(f"  {game_name:<30s} {config_path}")
        else:
            print("No game configurations found in config/games/")
        return True

    # --validate-only
    if args.validate_only:
        if not args.input_yaml:
            print("Error: input_yaml is required for --validate-only", file=sys.stderr)
            sys.exit(2)
        try:
            from modules.simple_config_parser import SimpleConfigParser
            config_parser = SimpleConfigParser(args.input_yaml)
            config = config_parser.get_config()
            steps = config.get("steps", {})
            hooks = config.get("hooks", {})
            pre_hooks = len(hooks.get("pre", []))
            post_hooks = len(hooks.get("post", []))
            sideload_count = sum(1 for s in steps.values() if "sideload" in s)

            print(f"Configuration valid: {config_parser.game_name}")
            print(f"  Steps      : {len(steps)}")
            print(f"  Pre-hooks  : {pre_hooks}")
            print(f"  Post-hooks : {post_hooks}")
            print(f"  Sideloads  : {sideload_count}")
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        except ValueError as e:
            print(f"Validation error: {e}", file=sys.stderr)
            sys.exit(2)
        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
            sys.exit(2)
        return True

    # --list-steps
    if args.list_steps:
        if not args.input_yaml:
            print("Error: input_yaml is required for --list-steps", file=sys.stderr)
            sys.exit(2)
        try:
            from modules.simple_config_parser import SimpleConfigParser
            config_parser = SimpleConfigParser(args.input_yaml)
            steps = config_parser.get_config().get("steps", {})
            game_name = config_parser.game_name

            print(f"Workflow steps for {game_name} ({len(steps)} steps):")
            print()
            print(f"  {'Step':<6s} {'Description':<45s} {'Components'}")
            print(f"  {'-'*4:<6s} {'-'*43:<45s} {'-'*30}")

            for num, step in sorted(steps.items(), key=lambda x: int(x[0])):
                desc = step.get("description", "No description")
                if len(desc) > 43:
                    desc = desc[:40] + "..."

                parts = []
                if "find" in step:
                    find_type = step["find"].get("type", "?")
                    parts.append(f"find:{find_type}")
                if "action" in step:
                    action_type = step["action"].get("type", "?") if isinstance(step["action"], dict) else step["action"]
                    parts.append(f"action:{action_type}")
                if "sideload" in step:
                    parts.append("sideload")
                if step.get("optional", False) or "[OPTIONAL]" in step.get("description", "").upper():
                    parts.append("optional")

                print(f"  {str(num):<6s} {desc:<45s} [{', '.join(parts)}]")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        return True

    # --config-dump
    if args.config_dump:
        if not args.input_yaml:
            print("Error: input_yaml is required for --config-dump", file=sys.stderr)
            sys.exit(2)
        try:
            from modules.simple_config_parser import SimpleConfigParser
            config_parser = SimpleConfigParser(args.input_yaml)
            config = config_parser.get_config()

            # Apply any overrides before dumping
            config = apply_config_overrides(config, args)

            yaml.dump(config, sys.stdout, default_flow_style=False, sort_keys=False)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        return True

    # --dry-run
    if args.dry_run:
        if not args.input_yaml:
            print("Error: input_yaml is required for --dry-run", file=sys.stderr)
            sys.exit(2)
        try:
            from modules.simple_config_parser import SimpleConfigParser
            config_parser = SimpleConfigParser(args.input_yaml)
            config = config_parser.get_config()
            config = apply_config_overrides(config, args)

            metadata = config.get("metadata", {})
            steps = config.get("steps", {})
            hooks = config.get("hooks", {})
            game_name = metadata.get("game_name", "Unknown")

            execution_id = args.execution_id or generate_execution_id(game_name)

            print(VCAP_BANNER)
            print("DRY RUN - No SUT connection will be made")
            print()
            print(f"{'='*60}")
            print(f"  Execution Plan")
            print(f"{'='*60}")
            print(f"  Execution ID   : {execution_id}")
            print(f"  Config         : {args.input_yaml}")
            print(f"  Game           : {game_name}")
            print(f"  SUT Target     : {args.sut_ip}:{args.sut_port}")
            print(f"  Vision Model   : {args.vision_model}")
            print(f"  Run Count      : {args.run_count}")
            print(f"  Max Retries    : {args.max_retries}")
            print()

            # Game launch info
            if args.game_path and not args.no_launch:
                startup_wait = args.startup_wait or metadata.get("startup_wait", 30)
                print(f"  Game Launch    : {args.game_path}")
                print(f"  Process ID     : {args.process_id or metadata.get('process_id', 'auto')}")
                print(f"  Startup Wait   : {startup_wait}s")
            elif args.no_launch:
                print(f"  Game Launch    : SKIPPED (--no-launch)")
            else:
                print(f"  Game Launch    : No game path specified")
            print()

            # Hooks
            pre_hooks = hooks.get("pre", [])
            post_hooks = hooks.get("post", [])
            if pre_hooks or post_hooks:
                print(f"  Hooks:")
                for i, hook in enumerate(pre_hooks):
                    persistent = " [PERSISTENT]" if hook.get("persistent", False) else ""
                    print(f"    PRE  {i+1}: {hook.get('path', '?')}{persistent}")
                for i, hook in enumerate(post_hooks):
                    print(f"    POST {i+1}: {hook.get('path', '?')}")
                print()

            # Steps
            print(f"  Steps ({len(steps)}):")
            for num, step in sorted(steps.items(), key=lambda x: int(x[0])):
                desc = step.get("description", "No description")
                parts = []
                if "find" in step:
                    parts.append(f"find:{step['find'].get('type', '?')}")
                if "action" in step:
                    atype = step["action"].get("type", "?") if isinstance(step["action"], dict) else step["action"]
                    parts.append(f"action:{atype}")
                if "sideload" in step:
                    parts.append(f"sideload:{os.path.basename(step['sideload'].get('path', '?'))}")
                print(f"    {str(num):>3s}. {desc}")
                print(f"         [{', '.join(parts)}]")
            print()
            print(f"{'='*60}")
            print(f"  Dry run complete. No actions were executed.")
            print(f"{'='*60}")

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        return True

    return False


# ============================================================================
# REPORTING
# ============================================================================

def print_execution_summary(execution_id, args, run_results, total_duration, logger):
    """Print the execution summary block."""
    successful_runs = sum(1 for r in run_results if r["success"])
    failed_runs = len(run_results) - successful_runs
    overall_success = failed_runs == 0

    # Format duration
    minutes = int(total_duration // 60)
    seconds = int(total_duration % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

    status_str = "SUCCESS" if overall_success else "FAILED"

    summary = f"""
{'='*60}
  VCAP Execution Summary
{'='*60}
  Execution ID : {execution_id}
  Config       : {args.input_yaml}
  SUT          : {args.sut_ip}:{args.sut_port}
  Vision Model : {args.vision_model}
  Status       : {status_str}
  Runs         : {successful_runs}/{len(run_results)} completed
  Duration     : {duration_str}
  Log Dir      : {args.log_dir}/{execution_id}/
{'='*60}"""

    if not args.quiet:
        print(summary)
    logger.info(summary)

    return overall_success


def print_timing_summary(step_timings):
    """Print per-step timing table."""
    if not step_timings:
        return

    print()
    print(f"  {'Step':<6s} {'Description':<40s} {'Duration':>10s}")
    print(f"  {'-'*4:<6s} {'-'*38:<40s} {'-'*8:>10s}")

    total = 0.0
    for entry in step_timings:
        desc = entry["description"]
        if len(desc) > 38:
            desc = desc[:35] + "..."
        duration = entry["duration"]
        total += duration
        print(f"  {entry['step']:<6s} {desc:<40s} {duration:>8.1f}s")

    print(f"  {'-'*4:<6s} {'-'*38:<40s} {'-'*8:>10s}")
    print(f"  {'':6s} {'Total':<40s} {total:>8.1f}s")
    print()


def write_report(args, execution_id, run_results, total_duration, step_timings):
    """Write execution report to file in the specified format."""
    if not args.report_file:
        return

    successful_runs = sum(1 for r in run_results if r["success"])
    overall_success = successful_runs == len(run_results)

    if args.report_format == "json":
        report = {
            "vcap_version": __version__,
            "execution_id": execution_id,
            "config": args.input_yaml,
            "sut": f"{args.sut_ip}:{args.sut_port}",
            "vision_model": args.vision_model,
            "success": overall_success,
            "total_runs": len(run_results),
            "successful_runs": successful_runs,
            "total_duration_seconds": round(total_duration, 2),
            "runs": run_results,
            "step_timings": step_timings,
            "timestamp": datetime.now().isoformat(),
        }
        with open(args.report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    elif args.report_format == "junit":
        # JUnit XML for CI integration
        test_count = len(run_results)
        failures = sum(1 for r in run_results if not r["success"])
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<testsuite name="VCAP-{execution_id}" tests="{test_count}" failures="{failures}" time="{total_duration:.2f}">',
        ]
        for r in run_results:
            status = "passed" if r["success"] else "failed"
            xml_lines.append(f'  <testcase name="run_{r["run_num"]}" classname="{execution_id}" time="{r.get("duration", 0):.2f}">')
            if not r["success"]:
                xml_lines.append(f'    <failure message="Run {r["run_num"]} failed">{r.get("error", "Unknown error")}</failure>')
            xml_lines.append("  </testcase>")
        xml_lines.append("</testsuite>")

        with open(args.report_file, "w", encoding="utf-8") as f:
            f.write("\n".join(xml_lines))

    else:
        # text format
        with open(args.report_file, "w", encoding="utf-8") as f:
            f.write(f"VCAP Execution Report\n")
            f.write(f"{'='*60}\n")
            f.write(f"Execution ID : {execution_id}\n")
            f.write(f"Config       : {args.input_yaml}\n")
            f.write(f"SUT          : {args.sut_ip}:{args.sut_port}\n")
            f.write(f"Vision Model : {args.vision_model}\n")
            f.write(f"Status       : {'SUCCESS' if overall_success else 'FAILED'}\n")
            f.write(f"Runs         : {successful_runs}/{len(run_results)}\n")
            f.write(f"Duration     : {total_duration:.1f}s\n")
            f.write(f"Timestamp    : {datetime.now().isoformat()}\n")
            f.write(f"{'='*60}\n")


# ============================================================================
# CORE EXECUTION ENGINE
# ============================================================================

def run_execution(args):
    """
    Main execution function - orchestrates the full VCAP pipeline.

    Returns:
        int: Exit code (0=success, 1=failure, 2=config error, 3=connection error,
             4=vision error, 5=launch error)
    """
    # Validate required positional arguments for execution mode
    if not args.input_yaml or not args.sut_ip or args.sut_port is None:
        print("Error: input_yaml, sut_ip, and sut_port are required for execution.", file=sys.stderr)
        print("Use --help for usage information.", file=sys.stderr)
        return 2

    # Load env file if specified
    if args.env_file:
        load_env_file(args.env_file)

    # Load and validate configuration
    try:
        from modules.simple_config_parser import SimpleConfigParser
        config_parser = SimpleConfigParser(args.input_yaml)
        config = config_parser.get_config()
    except FileNotFoundError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"Configuration validation error: {e}", file=sys.stderr)
        return 2

    # Apply CLI overrides
    config = apply_config_overrides(config, args)

    # Extract metadata
    metadata = config.get("metadata", {})
    game_name = metadata.get("game_name", "Unknown")

    # Generate execution ID
    execution_id = args.execution_id or generate_execution_id(game_name)

    # Create top-level run directory for first run (logging needs it)
    first_run_dir = create_run_directory(args, execution_id, run_num=1)

    # Setup logging
    logger, log_file = setup_logging(args, execution_id, first_run_dir)

    if not args.quiet:
        print(VCAP_BANNER)

    logger.info(f"VCAP Execution Engine v{__version__}")
    logger.info(f"Execution ID: {execution_id}")
    logger.info(f"Config: {args.input_yaml}")
    logger.info(f"Game: {game_name}")
    logger.info(f"SUT: {args.sut_ip}:{args.sut_port}")
    logger.info(f"Vision Model: {args.vision_model}")
    logger.info(f"Run Count: {args.run_count}")

    # Dump config to run directory
    config_dump_path = os.path.join(first_run_dir, "config_dump.yaml")
    try:
        with open(config_dump_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Config dumped to {config_dump_path}")
    except Exception as e:
        logger.warning(f"Failed to dump config: {e}")

    # Import execution modules
    try:
        from modules.network import NetworkManager
        from modules.screenshot import ScreenshotManager
        from modules.annotator import Annotator
        from modules.game_launcher import GameLauncher
        from modules.simple_automation import SimpleAutomation
    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        return 2

    # Execution tracking
    run_results = []
    step_timings = []
    overall_start = time.time()

    # -- Run Loop ----------------------------------------------------------
    for run_num in range(1, args.run_count + 1):
        run_start = time.time()

        logger.info("=" * 60)
        logger.info(f"STARTING RUN {run_num}/{args.run_count}")
        logger.info("=" * 60)

        # Create per-run directory
        run_dir = create_run_directory(args, execution_id, run_num) if run_num > 1 else first_run_dir

        network = None
        vision_model = None
        run_success = False
        run_error = None

        try:
            # Connect to SUT
            logger.info(f"Connecting to SUT at {args.sut_ip}:{args.sut_port}")
            network = NetworkManager(args.sut_ip, int(args.sut_port))
            logger.info("SUT connection established")

            # Initialize vision model
            try:
                vision_model = init_vision_model(args, logger)
            except Exception as e:
                logger.error(f"Vision model initialization failed: {e}")
                run_results.append({"run_num": run_num, "success": False, "error": str(e), "duration": time.time() - run_start})
                if args.fail_fast:
                    return 4
                continue

            # Initialize other components
            screenshot_mgr = ScreenshotManager(network)
            annotator = None if args.no_annotations else Annotator()

            # Handle Steam login
            if args.steam_login and args.steam_username and args.steam_password:
                logger.info(f"Logging into Steam as {args.steam_username}...")
                steam_ok = network.login_steam(args.steam_username, args.steam_password)
                if not steam_ok:
                    logger.error("Steam login failed")
                    run_results.append({"run_num": run_num, "success": False, "error": "Steam login failed", "duration": time.time() - run_start})
                    if args.fail_fast:
                        return 5
                    continue
                logger.info("Steam login successful")
                time.sleep(5)

            # Launch game
            game_process_id = args.process_id or metadata.get("process_id", "")
            if args.game_path and not args.no_launch:
                logger.info(f"Launching game: {args.game_path}")
                try:
                    game_launcher = GameLauncher(network)
                    startup_wait = args.startup_wait or metadata.get("startup_wait", 30)
                    game_launcher.launch(args.game_path, game_process_id, startup_wait)
                    logger.info(f"Game launched, waiting {startup_wait}s for initialization...")
                    time.sleep(startup_wait)
                except Exception as e:
                    logger.error(f"Game launch failed: {e}")
                    run_results.append({"run_num": run_num, "success": False, "error": str(e), "duration": time.time() - run_start})
                    if args.fail_fast:
                        return 5
                    continue

            # Write modified config to temp file for SimpleAutomation
            temp_config_path = os.path.join(run_dir, "runtime_config.yaml")
            with open(temp_config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            # Run SimpleAutomation
            logger.info("Starting SimpleAutomation...")
            automation = SimpleAutomation(
                config_path=temp_config_path,
                network=network,
                screenshot_mgr=screenshot_mgr,
                vision_model=vision_model,
                stop_event=None,
                run_dir=run_dir,
                annotator=annotator,
            )

            run_success = automation.run()

            if run_success:
                logger.info(f"Run {run_num} completed SUCCESSFULLY")
            else:
                logger.error(f"Run {run_num} FAILED")
                run_error = "Automation returned failure"

                # Kill game on failure if configured
                if not args.no_kill_on_failure and args.game_path and game_process_id:
                    logger.info("Killing game process after failure...")
                    try:
                        network.send_action({"type": "terminate_game"})
                    except Exception:
                        pass

        except ConnectionError as e:
            logger.error(f"SUT connection failed: {e}")
            run_error = str(e)
            if args.fail_fast:
                run_results.append({"run_num": run_num, "success": False, "error": run_error, "duration": time.time() - run_start})
                return 3

        except Exception as e:
            logger.error(f"Run {run_num} error: {e}", exc_info=True)
            run_error = str(e)

        finally:
            # Cleanup per-run resources
            if network:
                try:
                    network.close()
                except Exception:
                    pass
            if vision_model and hasattr(vision_model, "close"):
                try:
                    vision_model.close()
                except Exception:
                    pass

        run_duration = time.time() - run_start
        run_results.append({
            "run_num": run_num,
            "success": run_success,
            "error": run_error,
            "duration": round(run_duration, 2),
        })

        # Delay between runs
        if run_num < args.run_count and args.run_delay > 0:
            logger.info(f"Waiting {args.run_delay}s before next run...")
            time.sleep(args.run_delay)

    # -- Post-Execution ----------------------------------------------------
    total_duration = time.time() - overall_start

    # Print timing if requested
    if args.timing and step_timings:
        print_timing_summary(step_timings)

    # Print summary
    if args.summary and not args.no_summary:
        overall_success = print_execution_summary(execution_id, args, run_results, total_duration, logger)
    else:
        successful_runs = sum(1 for r in run_results if r["success"])
        overall_success = successful_runs == len(run_results)

    # Write report
    write_report(args, execution_id, run_results, total_duration, step_timings)

    # Determine exit code
    if args.exit_code_mode == "step-count":
        failed_count = sum(1 for r in run_results if not r["success"])
        return failed_count
    else:
        return 0 if overall_success else 1


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for the VCAP Execution Engine."""
    parser = build_parser()
    args = parser.parse_args()

    # Handle introspection commands first (they exit early)
    if handle_introspection(args):
        sys.exit(0)

    # Run the execution engine
    exit_code = run_execution(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
