"""
Multi-SUT GUI Application for Game UI Navigation Automation Tool
Manages multiple System Under Test (SUT) machines simultaneously.
Each SUT runs independently with its own configuration and logging.
"""

import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import logging
import queue
import yaml
import json
from pathlib import Path
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageTk
import requests
from dataclasses import dataclass, asdict
import re


# ============================================================================
# DATA STRUCTURES FOR CAMPAIGN SYSTEM
# ============================================================================

@dataclass
class GameEntry:
    """Represents a single game in a campaign."""
    game_name: str          # Display name (e.g., "Cyberpunk 2077")
    config_path: str        # Path to YAML config
    game_path: str          # Path to game executable or Steam ID
    run_count: int = 3      # How many times to run this game
    run_delay: int = 30     # Seconds between runs of this game

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @staticmethod
    def from_dict(data):
        """Create GameEntry from dictionary."""
        return GameEntry(
            game_name=data.get("game_name", "Unknown Game"),
            config_path=data.get("config_path", ""),
            game_path=data.get("game_path", ""),
            run_count=data.get("run_count", 3),
            run_delay=data.get("run_delay", 30)
        )


# ============================================================================
# LOGGING HANDLERS
# ============================================================================

# Add logging handler for GUI
class QueueHandler(logging.Handler):
    """Send logging records to a queue"""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(record)


class ThreadFilter(logging.Filter):
    """Filter that only allows logs from a specific thread"""
    def __init__(self, thread_id):
        super().__init__()
        self.thread_id = thread_id

    def filter(self, record):
        # Only allow logs from our thread
        return record.thread == self.thread_id


class HybridConfigParser:
    """Handles loading and parsing both state machine and step-based YAML configurations."""

    def __init__(self, config_path: str):
        """Initialize the hybrid config parser."""
        self.config_path = config_path
        self.config = self._load_config()
        self.config_type = self._detect_config_type()
        self._validate_config()

        # Extract game metadata
        self.game_name = self.config.get("metadata", {}).get("game_name", "Unknown Game")
        logging.getLogger(__name__).info(f"HybridConfigParser initialized for {self.game_name} using {config_path} (type: {self.config_type})")

    def _load_config(self):
        """Load the YAML configuration file."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            return config
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML config: {str(e)}")

    def _detect_config_type(self):
        """Detect whether this is a step-based or state machine configuration."""
        if "steps" in self.config:
            return "steps"
        elif "states" in self.config:
            # State machine config - transitions may be at root or nested in states
            return "state_machine"
        else:
            logging.getLogger(__name__).warning("Could not determine config type, defaulting to state_machine")
            return "state_machine"

    def _validate_config(self):
        """Validate the configuration structure based on detected type."""
        if self.config_type == "steps":
            return self._validate_steps_config()
        else:
            return self._validate_state_machine_config()

    def _validate_steps_config(self):
        """Validate step-based configuration."""
        if "steps" not in self.config:
            raise ValueError("Invalid config: missing 'steps' section")

        steps = self.config.get("steps", {})
        if not isinstance(steps, dict) or not steps:
            raise ValueError("Invalid config: steps section must be a non-empty dictionary")

        return True

    def _validate_state_machine_config(self):
        """Validate state machine configuration."""
        # Only states is strictly required - transitions can be nested in states
        required_sections = ["states", "initial_state", "target_state"]
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Invalid config: missing '{section}' section")

        return True

    def get_config(self):
        """Get the parsed configuration."""
        return self.config

    def get_config_type(self):
        """Get the detected configuration type."""
        return self.config_type

    def is_step_based(self):
        """Check if this is a step-based configuration."""
        return self.config_type == "steps"

    def get_state_definition(self, state_name: str):
        """Get the definition for a specific state (state machine configs only)."""
        if self.config_type != "state_machine":
            return None
        states = self.config.get("states", {})
        return states.get(state_name)

    def get_game_metadata(self):
        """Get game metadata from the configuration."""
        return self.config.get("metadata", {})


class SUTController:
    """Controls automation for a single SUT machine."""

    def __init__(self, name, ip, port, config_path="", game_path="", color=None):
        """Initialize SUT controller."""
        self.name = name
        self.ip = ip
        self.port = port
        self.config_path = config_path
        self.game_path = game_path
        self.color = color or {"bg": "#f5f5f5", "accent": "#666666", "name": "Gray"}  # Default color

        # Run iteration settings (per SUT)
        self.run_count = 3       # Number of iterations to run
        self.run_delay = 30      # Delay in seconds between runs

        # Threading
        self.thread = None
        self.stop_event = threading.Event()

        # Logging
        self.log_queue = queue.Queue()
        self.logger = None
        self.queue_handler = None  # Initialize to None to prevent duplicates
        self.thread_id = None  # Will be set when automation thread starts

        # Status tracking
        self.status = "Idle"  # Idle, Running, Completed, Failed, Stopped, Error
        self.completed_steps = 0  # Track completed steps for X/Y display
        self.total_steps = 0      # Total steps in config
        self.current_step = ""

        # Run iteration tracking
        self.current_run = 0      # Current run number (for iterations)
        self.total_runs = 1       # Total runs to execute

        # Run directory
        self.current_run_dir = None

        # Live preview
        self.preview_enabled = True  # Enable/disable preview
        self.last_preview_update = 0  # Timestamp of last preview update

        # Campaign mode (NEW)
        self.campaign_mode = False          # True if using campaign, False for single game
        self.campaign_name = "Default"      # Name of campaign
        self.campaign = []                  # List of GameEntry objects
        self.delay_between_games = 120      # Seconds to wait between games
        self.continue_on_failure = True     # Continue campaign if a game fails

        # Campaign progress tracking (NEW)
        self.total_games = 0                # Total games in campaign
        self.current_game_index = 0         # Which game (0-based)
        self.current_game_name = ""         # Name of current game
        self.failed_games = []              # List of failed game names

        # Game process tracking
        self.current_process_id = None      # Process ID of currently running game

    def setup_logger(self, log_level="INFO"):
        """Setup thread-filtered logger for this SUT - isolates logs by thread ID."""
        import threading

        # Store the current thread ID for filtering
        self.thread_id = threading.current_thread().ident

        # Validate and convert log level string to logging constant
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level not in valid_levels:
            log_level = "INFO"  # Fallback to INFO if invalid

        # Create SUT-specific logger
        logger_name = f"SUT-{self.name}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(getattr(logging, log_level))
        self.logger.propagate = False  # Isolate from root logger

        # Remove existing handlers from this logger
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        # Create queue handler with thread filter
        queue_handler = QueueHandler(self.log_queue)
        queue_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                                           datefmt='%H:%M:%S')
        queue_handler.setFormatter(queue_formatter)

        # Add thread filter to ONLY accept logs from this SUT's thread
        thread_filter = ThreadFilter(self.thread_id)
        queue_handler.addFilter(thread_filter)

        self.logger.addHandler(queue_handler)

        # Store queue handler reference
        self.queue_handler = queue_handler

        # Add the SAME queue handler (with thread filter) to root logger
        # This captures ALL module logs, but the filter ensures only THIS thread's logs pass through
        root_logger = logging.getLogger()
        root_logger.addHandler(queue_handler)
        root_logger.setLevel(getattr(logging, log_level))

        return self.logger

    def start_automation(self, shared_settings):
        """Start automation in a new thread."""
        if self.thread and self.thread.is_alive():
            if self.logger:
                self.logger.warning(f"Automation already running for {self.name}")
            return False

        # Validation: Campaign mode vs Single game mode
        if self.campaign_mode:
            # Campaign mode: Check if campaign has games
            if not self.campaign or len(self.campaign) == 0:
                self.status = "Error"
                return False
        else:
            # Single game mode: Check if config path is set
            if not self.config_path:
                self.status = "Error"
                return False

        self.stop_event.clear()
        self.status = "Running"
        self.completed_steps = 0
        self.current_run = 0

        # Set total_runs based on mode
        if self.campaign_mode:
            # For campaign, total runs is sum of all game runs
            self.total_runs = sum(game.run_count for game in self.campaign)
        else:
            # For single game, use controller's run_count
            self.total_runs = self.run_count

        self.thread = threading.Thread(
            target=self._run_automation,
            args=(shared_settings,),
            daemon=True
        )
        self.thread.start()
        return True

    def stop_automation(self):
        """Stop automation."""
        if self.thread and self.thread.is_alive():
            self.logger.info(f"Stopping automation for {self.name}")
            self.stop_event.set()
            self.status = "Stopped"
            
            # v0.2: Also tell SUT to cancel any ongoing launch operation
            try:
                if self.network:
                    import requests
                    url = f"http://{self.network.host}:{self.network.port}/cancel_launch"
                    requests.post(url, timeout=2)
                    self.logger.info(f"Sent cancel_launch request to SUT")
            except Exception as e:
                self.logger.debug(f"Could not send cancel_launch: {e}")
            
            return True
        return False

    def _run_automation(self, shared_settings):
        """Main automation logic (runs in separate thread) - supports both single game and campaign modes."""
        try:
            self.setup_logger(shared_settings.get("log_level", "INFO"))
            self.logger.info(f"Starting automation for {self.name}")
            self.logger.info(f"SUT: {self.ip}:{self.port}")

            # Check mode: Campaign or Single Game
            if self.campaign_mode and len(self.campaign) > 0:
                # CAMPAIGN MODE
                self._run_campaign(shared_settings)
            else:
                # SINGLE GAME MODE (original behavior)
                self._run_single_game(shared_settings)

        except Exception as e:
            self.logger.error(f"Automation failed: {str(e)}", exc_info=True)
            self.status = "Failed"

        finally:
            # Cleanup: Remove this SUT's queue handler from root logger
            if hasattr(self, 'queue_handler') and self.queue_handler:
                try:
                    root_logger = logging.getLogger()
                    root_logger.removeHandler(self.queue_handler)
                    self.logger.info("Cleaned up logging handlers")
                except Exception as e:
                    pass  # Ignore cleanup errors

    def _run_single_game(self, shared_settings):
        """Run single game mode (original behavior)."""
        self.logger.info(f"Mode: Single Game")
        self.logger.info(f"Config: {self.config_path}")

        # Load config
        try:
            config_parser = HybridConfigParser(self.config_path)
            config = config_parser.get_config()
            config_type = config_parser.get_config_type()
            self.logger.info(f"Config type: {config_type}")
        except Exception as e:
            self.logger.error(f"Failed to load config: {str(e)}")
            self.status = "Failed"
            return

        # Create batch folder for all runs
        batch_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        game_name = config_parser.game_name
        sut_dir = f"logs/{self.name}"
        os.makedirs(sut_dir, exist_ok=True)
        batch_dir = f"{sut_dir}/batch_{batch_timestamp}"
        os.makedirs(batch_dir, exist_ok=True)

        # Loop through runs using controller's own settings
        run_count = self.run_count
        run_delay = self.run_delay

        for run_num in range(1, run_count + 1):
            # Check for stop event before each run
            if self.stop_event.is_set():
                self.logger.info(f"Automation stopped by user before run {run_num}")
                self.status = "Stopped"
                return

            self.current_run = run_num
            self.logger.info(f"========================================")
            self.logger.info(f"Starting Run {run_num}/{run_count}")
            self.logger.info(f"========================================")

            # Run appropriate automation type with batch_dir
            if config_parser.is_step_based():
                self._run_simple_automation(config_parser, config, shared_settings, batch_dir, run_num)
            else:
                self._run_state_machine_automation(config_parser, config, shared_settings, batch_dir, run_num)

            # Check if stopped or failed during run
            if self.stop_event.is_set():
                self.logger.info(f"Automation stopped during run {run_num}")
                self.status = "Stopped"
                return

            if self.status == "Failed":
                self.logger.error(f"Run {run_num} failed, stopping batch")
                return

            # Delay between runs (except after last run)
            if run_num < run_count and run_delay > 0:
                self.logger.info(f"Waiting {run_delay} seconds before next run...")
                for _ in range(run_delay):
                    if self.stop_event.is_set():
                        self.logger.info("Automation stopped during delay")
                        self.status = "Stopped"
                        return
                    time.sleep(1)

        # All runs completed successfully
        if self.status != "Failed" and self.status != "Stopped":
            self.status = "Completed"
            self.logger.info(f"========================================")
            self.logger.info(f"All {run_count} runs completed successfully!")
            self.logger.info(f"========================================")

    def _run_campaign(self, shared_settings):
        """Run campaign mode - execute multiple games sequentially."""
        self.logger.info(f"Mode: Campaign")
        self.logger.info(f"Campaign Name: {self.campaign_name}")
        self.logger.info(f"Total Games: {len(self.campaign)}")
        self.logger.info(f"Continue on Failure: {self.continue_on_failure}")

        # Reset failed games list
        self.failed_games = []

        # Create campaign folder
        campaign_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        campaign_folder_name = f"{sanitize_folder_name(self.campaign_name)}_{campaign_timestamp}"
        sut_dir = f"logs/{self.name}"
        os.makedirs(sut_dir, exist_ok=True)
        campaign_dir = f"{sut_dir}/{campaign_folder_name}"
        os.makedirs(campaign_dir, exist_ok=True)

        self.logger.info(f"Campaign directory: {campaign_dir}")

        # Loop through each game in campaign
        for game_index, game_entry in enumerate(self.campaign):
            # Check for stop event before each game
            if self.stop_event.is_set():
                self.logger.info(f"Campaign stopped by user before game {game_index + 1}")
                self.status = "Stopped"
                return

            # Update campaign progress
            self.current_game_index = game_index
            self.current_game_name = game_entry.game_name

            self.logger.info(f"")
            self.logger.info(f"╔════════════════════════════════════════════════════════════╗")
            self.logger.info(f"║ GAME {game_index + 1}/{len(self.campaign)}: {game_entry.game_name}")
            self.logger.info(f"╚════════════════════════════════════════════════════════════╝")
            self.logger.info(f"Config: {game_entry.config_path}")
            self.logger.info(f"Game Path: {game_entry.game_path}")
            self.logger.info(f"Runs: {game_entry.run_count}, Delay: {game_entry.run_delay}s")

            # Load config for this game
            try:
                config_parser = HybridConfigParser(game_entry.config_path)
                config = config_parser.get_config()
                config_type = config_parser.get_config_type()
                self.logger.info(f"Config type: {config_type}")
            except Exception as e:
                self.logger.error(f"Failed to load config for {game_entry.game_name}: {str(e)}")
                self.failed_games.append(game_entry.game_name)

                if self.continue_on_failure:
                    self.logger.warning(f"⚠️  Skipping {game_entry.game_name} and continuing with campaign...")
                    continue  # Skip to next game
                else:
                    self.status = "Failed"
                    return  # Stop campaign

            # Create game folder within campaign
            game_folder_name = f"Game-{game_index + 1}_{sanitize_folder_name(game_entry.game_name)}"
            game_dir = f"{campaign_dir}/{game_folder_name}"
            os.makedirs(game_dir, exist_ok=True)
            self.logger.info(f"Game directory: {game_dir}")

            # Temporarily set game path for this game
            original_game_path = self.game_path
            self.game_path = game_entry.game_path

            # Run this game multiple times
            for run_num in range(1, game_entry.run_count + 1):
                # Check for stop event before each run
                if self.stop_event.is_set():
                    self.logger.info(f"Campaign stopped during Game {game_index + 1}, Run {run_num}")
                    self.status = "Stopped"
                    self.game_path = original_game_path  # Restore
                    return

                self.current_run = run_num
                self.total_runs = game_entry.run_count

                self.logger.info(f"")
                self.logger.info(f"────────────────────────────────────────────────────────────")
                self.logger.info(f"  Run {run_num}/{game_entry.run_count} of {game_entry.game_name}")
                self.logger.info(f"────────────────────────────────────────────────────────────")

                # Run appropriate automation type
                if config_parser.is_step_based():
                    self._run_simple_automation(config_parser, config, shared_settings, game_dir, run_num)
                else:
                    self._run_state_machine_automation(config_parser, config, shared_settings, game_dir, run_num)

                # Check if stopped or failed during run
                if self.stop_event.is_set():
                    self.logger.info(f"Campaign stopped during run")
                    self.status = "Stopped"
                    self.game_path = original_game_path  # Restore
                    return

                if self.status == "Failed":
                    self.logger.error(f"❌ Run {run_num} of {game_entry.game_name} failed")
                    self.failed_games.append(f"{game_entry.game_name} (Run {run_num})")

                    # Kill the failed game process before continuing
                    if self.current_process_id:
                        self.logger.info("Killing failed game process before continuing...")
                        self.kill_game_process()

                    if self.continue_on_failure:
                        self.logger.warning(f"⚠️  Skipping remaining runs of {game_entry.game_name} and continuing with campaign...")
                        self.status = "Running"  # Reset status to continue
                        break  # Skip remaining runs of this game, move to next game
                    else:
                        self.game_path = original_game_path  # Restore
                        return  # Stop campaign

                # Delay between runs (except after last run of this game)
                if run_num < game_entry.run_count and game_entry.run_delay > 0:
                    self.logger.info(f"Waiting {game_entry.run_delay} seconds before next run...")
                    for _ in range(game_entry.run_delay):
                        if self.stop_event.is_set():
                            self.logger.info("Campaign stopped during run delay")
                            self.status = "Stopped"
                            self.game_path = original_game_path  # Restore
                            return
                        time.sleep(1)

            # Restore original game path
            self.game_path = original_game_path

            self.logger.info(f">>>> Completed all {game_entry.run_count} runs of {game_entry.game_name}")

            # Delay between games (except after last game)
            if game_index < len(self.campaign) - 1 and self.delay_between_games > 0:
                self.logger.info(f"")
                self.logger.info(f"⏳ Waiting {self.delay_between_games} seconds before next game...")
                for _ in range(self.delay_between_games):
                    if self.stop_event.is_set():
                        self.logger.info("Campaign stopped during game delay")
                        self.status = "Stopped"
                        return
                    time.sleep(1)

        # Campaign completed (with or without failures)
        if self.status != "Failed" and self.status != "Stopped":
            self.logger.info(f"")
            self.logger.info(f"╔════════════════════════════════════════════════════════════╗")
            if len(self.failed_games) == 0:
                self.status = "Completed"
                self.logger.info(f"║ >>>> CAMPAIGN COMPLETED SUCCESSFULLY!")
                self.logger.info(f"║ Total Games: {len(self.campaign)}")
                self.logger.info(f"║ All games executed without errors")
            else:
                self.status = "Completed"  # Still mark as completed (partial success)
                self.logger.info(f"║ ⚠️  CAMPAIGN COMPLETED WITH FAILURES")
                self.logger.info(f"║ Total Games: {len(self.campaign)}")
                self.logger.info(f"║ Successful: {len(self.campaign) - len(self.failed_games)}")
                self.logger.info(f"║ Failed: {len(self.failed_games)}")
                self.logger.info(f"║")
                self.logger.info(f"║ Failed Games:")
                for failed_game in self.failed_games:
                    self.logger.info(f"║   • {failed_game}")
            self.logger.info(f"║ Campaign: {self.campaign_name}")
            self.logger.info(f"╚════════════════════════════════════════════════════════════╝")
            self.logger.info(f"")

    def _run_simple_automation(self, config_parser, config, shared_settings, batch_dir, run_num):
        """Run step-based automation."""
        try:
            # Import required modules
            from modules.network import NetworkManager
            from modules.screenshot import ScreenshotManager
            from modules.gemma_client import GemmaClient
            from modules.qwen_client import QwenClient
            from modules.omniparser_client import OmniparserClient
            from modules.annotator import Annotator
            from modules.simple_automation import SimpleAutomation
            from modules.game_launcher import GameLauncher

            # Create run-specific directory within batch folder
            run_dir = f"{batch_dir}/run_{run_num}"
            os.makedirs(run_dir, exist_ok=True)
            os.makedirs(f"{run_dir}/screenshots", exist_ok=True)
            os.makedirs(f"{run_dir}/annotated", exist_ok=True)

            self.current_run_dir = run_dir

            # Set up run-specific logging
            run_log_file = f"{run_dir}/automation.log"
            run_file_handler = logging.FileHandler(run_log_file)
            run_file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            run_file_handler.setFormatter(run_file_formatter)
            self.logger.addHandler(run_file_handler)

            self.logger.info(f"Created run directory: {run_dir}")

            # Initialize components
            self.logger.info(f"Connecting to SUT at {self.ip}:{self.port}")
            network = NetworkManager(self.ip, int(self.port))

            self.logger.info("Initializing components...")
            screenshot_mgr = ScreenshotManager(network)

            # Initialize vision model based on shared settings
            vision_model_type = shared_settings.get("vision_model", "omniparser")
            if vision_model_type == 'gemma':
                self.logger.info("Using Gemma for UI detection")
                vision_model = GemmaClient(shared_settings.get("lm_studio_url"))
            elif vision_model_type == 'qwen':
                self.logger.info("Using Qwen VL for UI detection")
                vision_model = QwenClient(shared_settings.get("lm_studio_url"))
            elif vision_model_type == 'omniparser':
                self.logger.info("Using Omniparser for UI detection")
                vision_model = OmniparserClient(shared_settings.get("omniparser_url"))

            annotator = Annotator()
            game_launcher = GameLauncher(network)

            # Helper to handle steam login - returns True if successful or no credentials needed
            def _handle_steam_login(metadata):
                steam_user = metadata.get("steam_username")
                steam_pass = metadata.get("steam_password")
                
                if steam_user and steam_pass:
                    self.logger.info(f"Steam credentials found for user: {steam_user}")
                    try:
                        self.logger.info("Initiating Steam login...")
                        if network.login_steam(steam_user, steam_pass):
                            self.logger.info("Steam login completed successfully")
                            time.sleep(5)  # Wait for Steam to settle
                            return True
                        else:
                            self.logger.error("Steam login FAILED - cannot proceed")
                            return False
                    except Exception as e:
                        self.logger.error(f"Steam login exception: {e}")
                        return False
                else:
                    self.logger.debug("No Steam credentials in metadata - skipping login")
                    return True  # No credentials = success (not required)

            # Get game metadata
            game_metadata = config_parser.get_game_metadata()
            self.logger.info(f"Game metadata loaded: {game_metadata}")
            startup_wait = game_metadata.get("startup_wait", 30)
            process_id = game_metadata.get("process_id", '')

            try:
                # Handle Steam Login FIRST - STOP if it fails
                steam_login_ok = _handle_steam_login(game_metadata)
                if not steam_login_ok:
                    self.logger.error("Stopping automation: Steam login failed")
                    self.status = "Failed"
                    return

                # Launch game if path provided
                if self.game_path:
                    self.logger.info(f"Launching game from: {self.game_path}")
                    # Pass process_id and startup_wait to enable process tracking on SUT
                    game_launcher.launch(self.game_path, process_id, startup_wait)

                    # Store process ID for cleanup
                    if process_id:
                        self.current_process_id = process_id
                        self.logger.debug(f"Tracking game process: {process_id}")

                    # Wait for game to initialize
                    self.logger.info(f"Waiting {startup_wait} seconds for game to initialize...")
                    for i in range(startup_wait):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)
                        self.current_step = f"Initializing ({startup_wait-i}s)"
                else:
                    self.logger.info("No game path provided, assuming game is already running")

                if self.stop_event.is_set():
                    self.logger.info("Automation stopped during initialization")
                    self.status = "Stopped"
                    return

                # Start SimpleAutomation
                self.logger.info("Starting SimpleAutomation...")
                simple_auto = SimpleAutomation(
                    config_path=config_parser.config_path,  # Use config from parser (works in both modes)
                    network=network,
                    screenshot_mgr=screenshot_mgr,
                    vision_model=vision_model,
                    stop_event=self.stop_event,
                    run_dir=run_dir,
                    annotator=annotator,
                    progress_callback=self  # Pass controller to track step progress
                )

                # Run automation
                success = simple_auto.run()

                # Update status based on result
                if success:
                    self.status = "Completed"
                    self.logger.info("Automation completed successfully")
                elif self.stop_event.is_set():
                    self.status = "Stopped"
                    self.logger.info("Automation stopped by user")
                else:
                    self.status = "Failed"
                    self.logger.error("Automation failed")

            except Exception as e:
                self.logger.error(f"Error in automation execution: {str(e)}", exc_info=True)
                self.status = "Failed"

            finally:
                # Kill game process ONLY if failed or stopped (not on success)
                if self.current_process_id and self.status in ["Failed", "Stopped"]:
                    self.logger.info("Cleaning up: Killing game process...")
                    self.kill_game_process()
                elif self.current_process_id and self.status == "Completed":
                    self.logger.debug("Automation completed successfully - leaving game process running")

                # Cleanup per-run resources (NOT queue_handler - that's cleaned up after all runs)
                if 'network' in locals():
                    network.close()
                if 'vision_model' in locals() and hasattr(vision_model, 'close'):
                    vision_model.close()
                if 'run_file_handler' in locals():
                    self.logger.removeHandler(run_file_handler)

        except Exception as e:
            self.logger.error(f"SimpleAutomation failed: {str(e)}", exc_info=True)
            self.status = "Failed"

    def _run_state_machine_automation(self, config_parser, config, shared_settings, batch_dir, run_num):
        """Run state machine automation."""
        try:
            # Import required modules
            from modules.network import NetworkManager
            from modules.screenshot import ScreenshotManager
            from modules.gemma_client import GemmaClient
            from modules.qwen_client import QwenClient
            from modules.omniparser_client import OmniparserClient
            from modules.annotator import Annotator
            from modules.decision_engine import DecisionEngine
            from modules.game_launcher import GameLauncher

            # Create run-specific directory within batch folder
            run_dir = f"{batch_dir}/run_{run_num}"
            os.makedirs(run_dir, exist_ok=True)
            os.makedirs(f"{run_dir}/screenshots", exist_ok=True)
            os.makedirs(f"{run_dir}/annotated", exist_ok=True)

            self.current_run_dir = run_dir

            # Setup logging
            run_log_file = f"{run_dir}/automation.log"
            run_file_handler = logging.FileHandler(run_log_file)
            run_file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            run_file_handler.setFormatter(run_file_formatter)
            self.logger.addHandler(run_file_handler)

            self.logger.info(f"Created run directory: {run_dir}")

            # Initialize components
            self.logger.info(f"Connecting to SUT at {self.ip}:{self.port}")
            network = NetworkManager(self.ip, int(self.port))

            screenshot_mgr = ScreenshotManager(network)

            # Initialize vision model
            vision_model_type = shared_settings.get("vision_model", "omniparser")
            if vision_model_type == 'gemma':
                vision_model = GemmaClient(shared_settings.get("lm_studio_url"))
            elif vision_model_type == 'qwen':
                vision_model = QwenClient(shared_settings.get("lm_studio_url"))
            elif vision_model_type == 'omniparser':
                vision_model = OmniparserClient(shared_settings.get("omniparser_url"))

            annotator = Annotator()
            game_launcher = GameLauncher(network)

            # Get game metadata
            game_metadata = config_parser.get_game_metadata()
            startup_wait = game_metadata.get("startup_wait", 30)
            process_id = game_metadata.get("process_id", '')

            try:
                # Handle Steam Login FIRST - STOP if it fails
                steam_user = game_metadata.get("steam_username")
                steam_pass = game_metadata.get("steam_password")
                steam_login_ok = True  # Default: no credentials needed
                
                if steam_user and steam_pass:
                    self.logger.info(f"Steam credentials found for user: {steam_user}")
                    try:
                        self.logger.info("Initiating Steam login...")
                        if network.login_steam(steam_user, steam_pass):
                            self.logger.info(">>>> Steam login completed successfully")
                            time.sleep(5)
                        else:
                            self.logger.error("XXXX - Steam login FAILED - cannot proceed")
                            steam_login_ok = False
                    except Exception as e:
                        self.logger.error(f"XXXX - Steam login exception: {e}")
                        steam_login_ok = False

                if not steam_login_ok:
                    self.logger.error("Stopping automation: Steam login failed")
                    self.status = "Failed"
                    return

                # Launch game if path provided
                if self.game_path:
                    self.logger.info(f"Launching game from: {self.game_path}")
                    # Pass process_id and startup_wait to enable process tracking on SUT
                    game_launcher.launch(self.game_path, process_id, startup_wait)

                    # Store process ID for cleanup
                    if process_id:
                        self.current_process_id = process_id
                        self.logger.debug(f"Tracking game process: {process_id}")

                    for i in range(startup_wait):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)
                        self.current_step = f"Initializing ({startup_wait-i}s)"

                if self.stop_event.is_set():
                    self.status = "Stopped"
                    return

                # Create decision engine
                self.logger.info("Starting state machine automation...")
                decision_engine = DecisionEngine(
                    config,
                    network,
                    screenshot_mgr,
                    vision_model,
                    annotator,
                    shared_settings.get("max_iterations", 50),
                    run_dir
                )

                # Run automation
                success = decision_engine.run(self.stop_event)

                if success:
                    self.status = "Completed"
                elif self.stop_event.is_set():
                    self.status = "Stopped"
                else:
                    self.status = "Failed"

            except Exception as e:
                self.logger.error(f"Error in state machine execution: {str(e)}", exc_info=True)
                self.status = "Failed"

            finally:
                # Kill game process ONLY if failed or stopped (not on success)
                if self.current_process_id and self.status in ["Failed", "Stopped"]:
                    self.logger.info("Cleaning up: Killing game process...")
                    self.kill_game_process()
                elif self.current_process_id and self.status == "Completed":
                    self.logger.debug("Automation completed successfully - leaving game process running")

                if 'network' in locals():
                    network.close()
                if 'vision_model' in locals() and hasattr(vision_model, 'close'):
                    vision_model.close()
                if 'run_file_handler' in locals():
                    self.logger.removeHandler(run_file_handler)

        except Exception as e:
            self.logger.error(f"State machine automation failed: {str(e)}", exc_info=True)
            self.status = "Failed"

    def get_status_color(self):
        """Get color for status indicator."""
        status_colors = {
            "Idle": "yellow",
            "Running": "green",
            "Completed": "blue",
            "Failed": "red",
            "Stopped": "orange",
            "Error": "red",
            "Not Connected": "red"
        }
        return status_colors.get(self.status, "yellow")

    def to_dict(self):
        """Convert to dictionary for saving (includes campaign data)."""
        import os
        # Normalize paths to use forward slashes for cross-platform compatibility
        config_path_normalized = self.config_path.replace("\\", "/") if self.config_path else ""
        game_path_normalized = self.game_path.replace("\\", "/") if self.game_path else ""

        return {
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "config_path": config_path_normalized,
            "game_path": game_path_normalized,
            "run_count": self.run_count,
            "run_delay": self.run_delay,
            # Campaign data (NEW)
            "campaign_mode": self.campaign_mode,
            "campaign_name": self.campaign_name,
            "campaign": [game.to_dict() for game in self.campaign],
            "delay_between_games": self.delay_between_games,
            "continue_on_failure": self.continue_on_failure
        }

    @staticmethod
    def from_dict(data):
        """Create SUTController from dictionary (includes campaign data)."""
        controller = SUTController(
            name=data.get("name", "SUT"),
            ip=data.get("ip", ""),
            port=data.get("port", 8080),
            config_path=data.get("config_path", ""),
            game_path=data.get("game_path", "")
        )
        # Restore run settings
        controller.run_count = data.get("run_count", 3)
        controller.run_delay = data.get("run_delay", 30)

        # Restore campaign data (NEW)
        controller.campaign_mode = data.get("campaign_mode", False)
        controller.campaign_name = data.get("campaign_name", "Default")
        controller.delay_between_games = data.get("delay_between_games", 120)
        controller.continue_on_failure = data.get("continue_on_failure", True)

        # Restore campaign games
        campaign_data = data.get("campaign", [])
        controller.campaign = [GameEntry.from_dict(game_data) for game_data in campaign_data]
        controller.total_games = len(controller.campaign)

        return controller

    # Campaign management methods
    def add_game_to_campaign(self, game_entry):
        """Add a game to the campaign."""
        self.campaign.append(game_entry)
        self.total_games = len(self.campaign)
        self.campaign_mode = True  # Auto-enable campaign mode

    def remove_game_from_campaign(self, index):
        """Remove a game from the campaign by index."""
        if 0 <= index < len(self.campaign):
            self.campaign.pop(index)
            self.total_games = len(self.campaign)
            if len(self.campaign) == 0:
                self.campaign_mode = False  # Disable campaign mode if empty

    def move_game_up(self, index):
        """Move a game up in the campaign order."""
        if index > 0:
            self.campaign[index], self.campaign[index-1] = self.campaign[index-1], self.campaign[index]

    def move_game_down(self, index):
        """Move a game down in the campaign order."""
        if index < len(self.campaign) - 1:
            self.campaign[index], self.campaign[index+1] = self.campaign[index+1], self.campaign[index]

    def clear_campaign(self):
        """Clear all games from the campaign."""
        self.campaign.clear()
        self.total_games = 0
        self.campaign_mode = False

    def kill_game_process(self, process_id=None):
        """Kill the game process on the SUT."""
        pid = process_id or self.current_process_id

        if not pid:
            if self.logger:
                self.logger.debug("No process ID to kill")
            return

        try:
            # Use network manager to send kill request to SUT
            import requests
            response = requests.post(
                f"http://{self.ip}:{self.port}/kill_process",
                json={"process_id": pid},
                timeout=5
            )

            if response.status_code == 200:
                if self.logger:
                    self.logger.info(f">>>> Killed game process (PID: {pid})")
                self.current_process_id = None
                return True
            elif response.status_code == 404:
                # Process not found - likely already closed by automation (expected)
                if self.logger:
                    self.logger.debug(f"Process {pid} not found (likely already closed by automation)")
                self.current_process_id = None
                return True
            else:
                if self.logger:
                    self.logger.warning(f"Failed to kill process {pid}: {response.status_code}")
                return False

        except Exception as e:
            if self.logger:
                self.logger.warning(f"Error killing process {pid}: {str(e)}")
            return False


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def sanitize_folder_name(name):
    """Sanitize a game name for use in folder names."""
    # Remove invalid characters for folder names
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace spaces with hyphens
    name = name.replace(' ', '-')
    # Remove multiple consecutive hyphens
    name = re.sub(r'-+', '-', name)
    # Trim hyphens from start and end
    name = name.strip('-')
    return name or "Unknown-Game"


class MultiSUTGUI:
    """Main GUI for multi-SUT automation control."""

    def __init__(self, root):
        """Initialize the multi-SUT GUI."""
        self.root = root
        self.root.title("Katana Multi-SUT Automator | Ver 1.0 | Alpha")
        self.root.geometry("1600x900")
        self.root.minsize(1200, 700)

        # SUT controllers
        self.sut_controllers = {}  # {name: SUTController}
        self.sut_tabs = {}  # {name: tab_frame}
        self.sut_widgets = {}  # {name: {widget_dict}}

        # Color palette for visual differentiation (subtle, professional colors)
        self.color_palette = [
            {"bg": "#E3F2FD", "accent": "#2196F3", "name": "Blue"},      # Light blue
            {"bg": "#F3E5F5", "accent": "#9C27B0", "name": "Purple"},    # Light purple
            {"bg": "#E8F5E9", "accent": "#4CAF50", "name": "Green"},     # Light green
            {"bg": "#FFF3E0", "accent": "#FF9800", "name": "Orange"},    # Light orange
            {"bg": "#FCE4EC", "accent": "#E91E63", "name": "Pink"},      # Light pink
            {"bg": "#E0F2F1", "accent": "#009688", "name": "Teal"},      # Light teal
            {"bg": "#FFF9C4", "accent": "#FBC02D", "name": "Yellow"},    # Light yellow
            {"bg": "#F1F8E9", "accent": "#8BC34A", "name": "Lime"},      # Light lime
            {"bg": "#E1F5FE", "accent": "#03A9F4", "name": "Cyan"},      # Light cyan
            {"bg": "#EFEBE9", "accent": "#795548", "name": "Brown"},     # Light brown
        ]
        self.next_color_index = 0

        # Shared settings
        self.vision_model = tk.StringVar(value="omniparser")
        self.omniparser_url = tk.StringVar(value="http://localhost:9000")
        self.lm_studio_url = tk.StringVar(value="http://127.0.0.1:1234")
        self.max_iterations = tk.StringVar(value="50")
        self.log_level = tk.StringVar(value="INFO")

        # Configure style
        self.style = ttk.Style()
        self.style.configure("TButton", padding=6)

        # Create GUI
        self.create_master_panel()
        self.create_sut_tabs()

        # Start GUI update loop
        self.root.after(100, self.update_gui)

    def create_master_panel(self):
        """Create the master control panel at the top."""
        # Master frame
        master_frame = ttk.LabelFrame(self.root, text="Master Control Panel", padding="10")
        master_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        # Shared settings section
        settings_frame = ttk.LabelFrame(master_frame, text="Shared Vision Settings", padding="10")
        settings_frame.pack(fill=tk.X, pady=(0, 10))

        # Vision model selection
        model_row = ttk.Frame(settings_frame)
        model_row.pack(fill=tk.X, pady=5)
        ttk.Label(model_row, text="Vision Model:").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(model_row, text="Omniparser", variable=self.vision_model,
                       value="omniparser", command=self.on_vision_model_change).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(model_row, text="Gemma", variable=self.vision_model,
                       value="gemma", command=self.on_vision_model_change).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(model_row, text="Qwen VL", variable=self.vision_model,
                       value="qwen", command=self.on_vision_model_change).pack(side=tk.LEFT)

        # Queue manager to Omniparser
        omni_row = ttk.Frame(settings_frame)
        omni_row.pack(fill=tk.X, pady=5)
        ttk.Label(omni_row, text="Queue Manager -> Omniparser :").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Entry(omni_row, textvariable=self.omniparser_url, width=30).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(omni_row, text="Test Connection", command=self.test_omniparser).pack(side=tk.LEFT, padx=(0, 10))
        self.omniparser_status_label = tk.Label(omni_row, text="Not Tested", foreground="gray", font=("TkDefaultFont", 10))
        self.omniparser_status_label.pack(side=tk.LEFT)

        # LM Studio URL
        lm_row = ttk.Frame(settings_frame)
        lm_row.pack(fill=tk.X, pady=5)
        ttk.Label(lm_row, text="LM Studio URL:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Entry(lm_row, textvariable=self.lm_studio_url, width=30).pack(side=tk.LEFT, padx=(0, 10))

        # Max iterations
        iter_row = ttk.Frame(settings_frame)
        iter_row.pack(fill=tk.X, pady=5)
        ttk.Label(iter_row, text="Max Iterations:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Entry(iter_row, textvariable=self.max_iterations, width=10).pack(side=tk.LEFT)

        # Log level
        log_row = ttk.Frame(settings_frame)
        log_row.pack(fill=tk.X, pady=5)
        ttk.Label(log_row, text="Log Level:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Combobox(log_row, textvariable=self.log_level,
                     values=["DEBUG", "INFO", "WARNING", "ERROR"],
                     state='readonly', width=10).pack(side=tk.LEFT)

        # Multi-SUT controls section
        controls_frame = ttk.LabelFrame(master_frame, text="Multi-SUT Controls", padding="10")
        controls_frame.pack(fill=tk.X)

        button_row = ttk.Frame(controls_frame)
        button_row.pack(fill=tk.X)

        ttk.Button(button_row, text="+ Add SUT", command=self.add_sut_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_row, text="- Remove SUT", command=self.remove_current_sut).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_row, text="Start All", command=self.start_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_row, text="Stop All", command=self.stop_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_row, text="Load Config", command=self.load_multi_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_row, text="Save Config", command=self.save_multi_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_row, text="Clear All Logs", command=self.clear_all_logs).pack(side=tk.LEFT, padx=5)

    def create_sut_tabs(self):
        """Create the SUT tabs section at the bottom."""
        # Tabs frame
        tabs_frame = ttk.Frame(self.root)
        tabs_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # Notebook for tabs
        self.notebook = ttk.Notebook(tabs_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Bind tab change event
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def add_sut_dialog(self):
        """Show dialog to add a new SUT."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add New SUT")
        dialog.geometry("500x250")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        # Dialog content
        content_frame = ttk.Frame(dialog, padding="20")
        content_frame.pack(fill=tk.BOTH, expand=True)

        # Name
        ttk.Label(content_frame, text="SUT Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        name_var = tk.StringVar(value=f"SUT-{len(self.sut_controllers)+1}")
        ttk.Entry(content_frame, textvariable=name_var, width=30).grid(row=0, column=1, pady=5, padx=(10, 0))

        # IP
        ttk.Label(content_frame, text="IP Address:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ip_var = tk.StringVar(value="192.168.50.230")
        ttk.Entry(content_frame, textvariable=ip_var, width=30).grid(row=1, column=1, pady=5, padx=(10, 0))

        # Port
        ttk.Label(content_frame, text="Port:").grid(row=2, column=0, sticky=tk.W, pady=5)
        port_var = tk.StringVar(value="8080")
        ttk.Entry(content_frame, textvariable=port_var, width=30).grid(row=2, column=1, pady=5, padx=(10, 0))

        # Buttons
        button_frame = ttk.Frame(content_frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=20)

        def on_add():
            name = name_var.get().strip()
            ip = ip_var.get().strip()
            port = port_var.get().strip()

            # Validate
            if not name:
                messagebox.showerror("Error", "SUT name is required")
                return
            if name in self.sut_controllers:
                messagebox.showerror("Error", f"SUT '{name}' already exists")
                return
            if not ip:
                messagebox.showerror("Error", "IP address is required")
                return
            if not port:
                messagebox.showerror("Error", "Port is required")
                return

            try:
                port = int(port)
            except ValueError:
                messagebox.showerror("Error", "Port must be a number")
                return

            # Create SUT controller (config and game path will be set in the tab)
            self.add_sut(name, ip, port, "", "")
            dialog.destroy()

        ttk.Button(button_frame, text="Add", command=on_add, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def add_sut(self, name, ip, port, config_path="", game_path=""):
        """Add a new SUT controller and tab."""
        # Assign color from palette (cycles through colors)
        color = self.color_palette[self.next_color_index % len(self.color_palette)]
        self.next_color_index += 1

        # Create controller with assigned color
        controller = SUTController(name, ip, port, config_path, game_path, color)
        self.sut_controllers[name] = controller

        # Create tab
        tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(tab_frame, text=f"{name}: {ip}")
        self.sut_tabs[name] = tab_frame

        # Create widgets for this tab
        self._create_sut_tab_content(name, tab_frame, controller)

        # Auto-load game path if config is provided
        if config_path:
            widgets = self.sut_widgets[name]
            self._auto_load_game_path(controller, widgets['game_var'])

        # Switch to new tab
        self.notebook.select(tab_frame)

    def _create_sut_tab_content(self, name, tab_frame, controller):
        """Create the content for a SUT tab."""
        # Store widget references
        widgets = {}

        # Color-coded accent bar at the top for visual differentiation
        accent_bar = tk.Frame(tab_frame, background=controller.color["accent"], height=4)
        accent_bar.pack(fill=tk.X, side=tk.TOP)

        # Main container with subtle colored background
        main_container = tk.Frame(tab_frame, relief="groove", borderwidth=2,
                                 background=controller.color["bg"])
        main_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # === TOP SECTION: Two columns side by side ===
        top_section = ttk.Frame(main_container)
        top_section.pack(fill=tk.BOTH, expand=False, padx=10, pady=10)

        # LEFT COLUMN: SUT Configuration + Live Preview
        left_column = ttk.Frame(top_section)
        left_column.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 5))

        # SUT Configuration (basic info)
        config_frame = ttk.LabelFrame(left_column, text="SUT Configuration", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))

        # Row 1: Name, IP, Port, Test Connection
        row1 = ttk.Frame(config_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="Name:").pack(side=tk.LEFT, padx=(0, 5))

        # Color indicator dot
        color_dot = tk.Label(row1, text="●", font=("TkDefaultFont", 14),
                            foreground=controller.color["accent"])
        color_dot.pack(side=tk.LEFT, padx=(0, 3))

        name_label = tk.Label(row1, text=controller.name, font=("TkDefaultFont", 10),
                             relief="sunken", width=15, anchor="w")
        name_label.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row1, text="IP:").pack(side=tk.LEFT, padx=(0, 5))
        ip_var = tk.StringVar(value=controller.ip)
        ip_entry = ttk.Entry(row1, textvariable=ip_var, width=15)
        ip_entry.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row1, text="Port:").pack(side=tk.LEFT, padx=(0, 5))
        port_var = tk.StringVar(value=str(controller.port))
        port_entry = ttk.Entry(row1, textvariable=port_var, width=8)
        port_entry.pack(side=tk.LEFT, padx=(0, 20))

        test_conn_btn = ttk.Button(row1, text="Test Connection",
                                   command=lambda: self._test_connection(controller))
        test_conn_btn.pack(side=tk.LEFT)

        widgets['ip_var'] = ip_var
        widgets['port_var'] = port_var

        # Live Preview in left column
        preview_lf = ttk.LabelFrame(left_column, text="Live Preview (1 frame/4s)", padding="5")
        preview_lf.pack(fill=tk.BOTH, expand=True)

        preview_canvas = tk.Canvas(preview_lf, width=320, height=180, bg="#1e1e1e", highlightthickness=1)
        preview_canvas.pack()

        preview_canvas.create_text(160, 90, text="No Preview\n(Start automation to see live feed)",
                                  fill="gray", font=("TkDefaultFont", 9), justify="center")
        widgets['preview_canvas'] = preview_canvas
        widgets['preview_image_id'] = None
        widgets['preview_photo'] = None

        # RIGHT COLUMN: Workflow Configuration + Controls
        right_column = ttk.Frame(top_section)
        right_column.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        # Workflow Configuration with Campaign Support
        workflow_frame = ttk.LabelFrame(right_column, text="Workflow Configuration", padding="10")
        workflow_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Define game_var early so it's available for callbacks and preview updates
        game_var = tk.StringVar(value=controller.game_path)

        # === MODE SELECTION ===
        mode_frame = ttk.Frame(workflow_frame)
        mode_frame.pack(fill=tk.X, pady=(0, 10))

        mode_var = tk.StringVar(value="campaign" if controller.campaign_mode else "single")
        ttk.Label(mode_frame, text="Mode:", font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(mode_frame, text="Single Game", variable=mode_var, value="single",
                       command=lambda: self._switch_workflow_mode(controller, widgets, "single")).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(mode_frame, text="Campaign", variable=mode_var, value="campaign",
                       command=lambda: self._switch_workflow_mode(controller, widgets, "campaign")).pack(side=tk.LEFT)

        widgets['mode_var'] = mode_var

        # === SINGLE GAME MODE FRAME ===
        single_game_frame = ttk.Frame(workflow_frame)

        # Row 1: Config dropdown selector
        sg_row1 = ttk.Frame(single_game_frame)
        sg_row1.pack(fill=tk.X, pady=5)
        ttk.Label(sg_row1, text="Config:").pack(side=tk.LEFT, padx=(0, 5))

        # Load available configs
        config_dict = self._load_available_configs()
        config_names = list(config_dict.keys())

        config_selection_var = tk.StringVar()
        config_dropdown = ttk.Combobox(sg_row1, textvariable=config_selection_var,
                                       values=config_names, state='readonly', width=35)
        config_dropdown.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)

        # Set initial selection if config path exists
        if controller.config_path:
            # Normalize paths for comparison (handle both forward and backslashes)
            controller_path_normalized = os.path.normpath(controller.config_path)
            for name, path in config_dict.items():
                path_normalized = os.path.normpath(path)
                if path_normalized == controller_path_normalized:
                    config_selection_var.set(name)
                    break

        def on_config_select(event=None):
            selected_name = config_selection_var.get()
            if selected_name and selected_name in config_dict:
                config_path = config_dict[selected_name]
                controller.config_path = config_path
                # Update preview and auto-load game path
                self._update_config_preview(controller, config_preview_frame, game_var)

        config_dropdown.bind('<<ComboboxSelected>>', on_config_select)

        def refresh_configs():
            new_config_dict = self._load_available_configs()
            new_names = list(new_config_dict.keys())
            config_dropdown['values'] = new_names
            # Update the stored dict
            config_dict.clear()
            config_dict.update(new_config_dict)

        ttk.Button(sg_row1, text="Refresh", command=refresh_configs).pack(side=tk.LEFT)

        widgets['config_selection_var'] = config_selection_var
        widgets['config_dict'] = config_dict

        # Workflow details preview panel
        config_preview_frame = ttk.LabelFrame(single_game_frame, text="Workflow Details", padding="5")
        config_preview_frame.pack(fill=tk.X, pady=(5, 0))

        # Create preview labels (will be populated when config is selected)
        preview_text = tk.Text(config_preview_frame, height=4, wrap=tk.WORD, relief="flat",
                               background="#f0f0f0", font=("consolas", 8))
        preview_text.pack(fill=tk.BOTH, expand=True)
        preview_text.config(state='disabled')

        widgets['config_preview_text'] = preview_text
        widgets['config_preview_frame'] = config_preview_frame

        # Game path row
        sg_row2 = ttk.Frame(single_game_frame)
        sg_row2.pack(fill=tk.X, pady=5)
        ttk.Label(sg_row2, text="Game Path:").pack(side=tk.LEFT, padx=(0, 5))
        game_entry = ttk.Entry(sg_row2, textvariable=game_var, width=30)
        game_entry.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)
        ttk.Button(sg_row2, text="Clear", command=lambda: game_var.set(""),
                  width=6).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(sg_row2, text="Verify", command=lambda: self._verify_game_path(controller),
                  width=6).pack(side=tk.LEFT)

        widgets['game_var'] = game_var

        # Run iteration settings
        sg_row3 = ttk.Frame(single_game_frame)
        sg_row3.pack(fill=tk.X, pady=5)
        ttk.Label(sg_row3, text="Run Count:").pack(side=tk.LEFT, padx=(0, 5))
        run_count_var = tk.IntVar(value=controller.run_count)
        ttk.Spinbox(sg_row3, from_=1, to=100, textvariable=run_count_var,
                   width=8).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(sg_row3, text="Delay (sec):").pack(side=tk.LEFT, padx=(0, 5))
        run_delay_var = tk.IntVar(value=controller.run_delay)
        ttk.Spinbox(sg_row3, from_=0, to=3600, textvariable=run_delay_var,
                   width=8).pack(side=tk.LEFT)

        widgets['run_count_var'] = run_count_var
        widgets['run_delay_var'] = run_delay_var
        widgets['single_game_frame'] = single_game_frame

        # === CAMPAIGN MODE FRAME ===
        campaign_frame = ttk.Frame(workflow_frame)

        # Campaign header with name and controls
        campaign_header = ttk.Frame(campaign_frame)
        campaign_header.pack(fill=tk.X, pady=(0, 10))

        # Row 1: Campaign name and delay
        header_row1 = ttk.Frame(campaign_header)
        header_row1.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(header_row1, text="Campaign Name:").pack(side=tk.LEFT, padx=(0, 5))
        campaign_name_var = tk.StringVar(value=controller.campaign_name)
        ttk.Entry(header_row1, textvariable=campaign_name_var, width=20).pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(header_row1, text="Delay between games (sec):").pack(side=tk.LEFT, padx=(0, 5))
        delay_between_games_var = tk.IntVar(value=controller.delay_between_games)
        ttk.Spinbox(header_row1, from_=0, to=3600, textvariable=delay_between_games_var,
                   width=8).pack(side=tk.LEFT)

        # Row 2: Continue on failure checkbox
        header_row2 = ttk.Frame(campaign_header)
        header_row2.pack(fill=tk.X)

        continue_on_failure_var = tk.BooleanVar(value=controller.continue_on_failure)
        ttk.Checkbutton(header_row2, text="Continue campaign if a game fails (recommended for unattended runs)",
                       variable=continue_on_failure_var).pack(side=tk.LEFT)

        widgets['campaign_name_var'] = campaign_name_var
        widgets['delay_between_games_var'] = delay_between_games_var
        widgets['continue_on_failure_var'] = continue_on_failure_var

        # Campaign controls (save/load)
        campaign_controls = ttk.Frame(campaign_frame)
        campaign_controls.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(campaign_controls, text="Save Campaign",
                  command=lambda: self._save_campaign(controller)).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(campaign_controls, text="Load Campaign",
                  command=lambda: self._load_campaign(controller, widgets)).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(campaign_controls, text="Clear All",
                  command=lambda: self._clear_campaign(controller, widgets)).pack(side=tk.LEFT)

        # Game list section
        game_list_label = ttk.LabelFrame(campaign_frame, text="Games in Campaign", padding="5")
        game_list_label.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Scrollable frame for game list
        canvas = tk.Canvas(game_list_label, height=200)
        scrollbar = ttk.Scrollbar(game_list_label, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        widgets['campaign_list_canvas'] = canvas
        widgets['campaign_list_frame'] = scrollable_frame

        # Add game button
        add_game_btn_frame = ttk.Frame(campaign_frame)
        add_game_btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(add_game_btn_frame, text="+ Add Game to Campaign",
                  command=lambda: self._add_game_dialog(controller, widgets)).pack(side=tk.LEFT, padx=(0, 10))

        # Campaign statistics
        campaign_stats_var = tk.StringVar(value=self._get_campaign_stats(controller))
        campaign_stats_label = ttk.Label(add_game_btn_frame, textvariable=campaign_stats_var,
                                         font=("TkDefaultFont", 9), foreground="blue")
        campaign_stats_label.pack(side=tk.LEFT)
        widgets['campaign_stats_var'] = campaign_stats_var

        widgets['campaign_frame'] = campaign_frame

        # Store widgets dict EARLY so callbacks can access it
        self.sut_widgets[name] = widgets

        # Update preview if config already selected (for single game mode)
        if controller.config_path:
            self._update_config_preview(controller, config_preview_frame, game_var)

        # Show appropriate frame based on mode
        if controller.campaign_mode:
            campaign_frame.pack(fill=tk.BOTH, expand=True)
            self._refresh_campaign_list(controller, widgets)
        else:
            single_game_frame.pack(fill=tk.BOTH, expand=True)

        # Controls frame (in right column)
        controls_frame = ttk.LabelFrame(right_column, text="Controls", padding="10")
        controls_frame.pack(fill=tk.X, pady=(0, 0))

        button_row = ttk.Frame(controls_frame)
        button_row.pack(fill=tk.X)

        start_btn = ttk.Button(button_row, text="Start",
                               command=lambda: self._start_sut(controller))
        start_btn.pack(side=tk.LEFT, padx=5)
        widgets['start_btn'] = start_btn

        stop_btn = ttk.Button(button_row, text="Stop",
                             command=lambda: self._stop_sut(controller))
        stop_btn.pack(side=tk.LEFT, padx=5)
        widgets['stop_btn'] = stop_btn

        ttk.Button(button_row, text="Restart",
                  command=lambda: self._restart_sut(controller)).pack(side=tk.LEFT, padx=5)

        # Preview toggle button
        preview_btn = ttk.Button(button_row, text="🎥 Preview: ON",
                                command=lambda: self._toggle_preview(controller, widgets))
        preview_btn.pack(side=tk.LEFT, padx=5)
        widgets['preview_btn'] = preview_btn

        # Status frame
        status_frame = ttk.Frame(main_container)
        status_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        status_left = ttk.Frame(status_frame)
        status_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(status_left, text="Status:").pack(side=tk.LEFT, padx=(0, 5))
        status_label = tk.Label(status_left, text="Idle", foreground="yellow", font=("TkDefaultFont", 10))
        status_label.pack(side=tk.LEFT, padx=(0, 20))
        widgets['status_label'] = status_label

        ttk.Button(status_frame, text="Export Logs",
                  command=lambda: self._export_logs(controller)).pack(side=tk.RIGHT)

        # Step progress display
        progress_frame = ttk.Frame(main_container)
        progress_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        ttk.Label(progress_frame, text="Progress:").pack(side=tk.LEFT, padx=(0, 5))
        steps_label = ttk.Label(progress_frame, text="Steps: 0/0")
        steps_label.pack(side=tk.LEFT, padx=(0, 15))
        widgets['steps_label'] = steps_label

        # Run iteration display (small text)
        run_label = ttk.Label(progress_frame, text="Run: 0/1", font=("TkDefaultFont", 8))
        run_label.pack(side=tk.LEFT)
        widgets['run_label'] = run_label

        # Logs frame (full width at bottom)
        logs_frame = ttk.LabelFrame(main_container, text="Logs", padding="10")
        logs_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        log_text = scrolledtext.ScrolledText(logs_frame, height=15, wrap=tk.WORD)
        log_text.pack(fill=tk.BOTH, expand=True)

        # Configure color tags for different log levels
        log_text.tag_configure("ERROR", foreground="#FF0000")      # Red
        log_text.tag_configure("WARNING", foreground="#FFA500")    # Orange
        log_text.tag_configure("INFO", foreground="#000000")       # Black
        log_text.tag_configure("DEBUG", foreground="#808080")      # Gray
        log_text.tag_configure("CRITICAL", foreground="#FF0000", background="#FFFF00")  # Red on yellow

        widgets['log_text'] = log_text

        # Note: widgets dict already stored in self.sut_widgets[name] earlier (line 922)
        # since Python uses references, all updates to 'widgets' are automatically reflected

    def _start_sut(self, controller):
        """Start automation for a SUT."""
        # Update controller properties from GUI
        widgets = self.sut_widgets[controller.name]
        controller.ip = widgets['ip_var'].get()
        controller.port = int(widgets['port_var'].get())

        # Update mode-specific settings
        if controller.campaign_mode:
            # Campaign mode: update campaign settings
            controller.campaign_name = widgets.get('campaign_name_var').get()
            controller.delay_between_games = widgets.get('delay_between_games_var').get()
            controller.continue_on_failure = widgets.get('continue_on_failure_var').get()
            # Campaign list is already up-to-date from UI operations
        else:
            # Single game mode: update single game settings
            controller.game_path = widgets['game_var'].get()
            controller.run_count = widgets['run_count_var'].get()
            controller.run_delay = widgets['run_delay_var'].get()

        # Get shared settings
        shared_settings = {
            "vision_model": self.vision_model.get(),
            "omniparser_url": self.omniparser_url.get(),
            "lm_studio_url": self.lm_studio_url.get(),
            "max_iterations": int(self.max_iterations.get()),
            "log_level": self.log_level.get()
        }

        # Start automation
        if controller.start_automation(shared_settings):
            widgets['log_text'].insert(tk.END, f"Starting automation for {controller.name}...\n")
            widgets['log_text'].see(tk.END)
        else:
            # Show appropriate error message
            if controller.campaign_mode:
                messagebox.showwarning("Warning", f"Cannot start {controller.name}: Campaign is empty. Add games first.")
            else:
                messagebox.showwarning("Warning", f"Cannot start {controller.name}: No config file selected.")

    def _stop_sut(self, controller):
        """Stop automation for a SUT."""
        controller.stop_automation()
        widgets = self.sut_widgets[controller.name]
        widgets['log_text'].insert(tk.END, f"Stopping automation for {controller.name}...\n")
        widgets['log_text'].see(tk.END)

    def _restart_sut(self, controller):
        """Restart automation for a SUT."""
        self._stop_sut(controller)
        time.sleep(1)
        self._start_sut(controller)

    def _toggle_preview(self, controller, widgets):
        """Toggle live preview on/off for a SUT."""
        # Toggle the state
        controller.preview_enabled = not controller.preview_enabled

        # Update button text and appearance
        preview_btn = widgets['preview_btn']
        if controller.preview_enabled:
            preview_btn.config(text="🎥 Preview: ON")
        else:
            preview_btn.config(text="📷 Preview: OFF")

            # Clear the preview canvas when disabling
            canvas = widgets['preview_canvas']
            if widgets.get('preview_image_id'):
                canvas.delete(widgets['preview_image_id'])
                widgets['preview_image_id'] = None

            # Show placeholder message
            canvas.delete("all")  # Clear everything
            canvas.create_text(160, 90, text="Preview Disabled\n(Click 'Preview: OFF' to enable)",
                             fill="gray", font=("TkDefaultFont", 9), justify="center",
                             tags="placeholder")

    def _test_connection(self, controller):
        """Test connection to a SUT."""
        widgets = self.sut_widgets[controller.name]
        ip = widgets['ip_var'].get()
        port = widgets['port_var'].get()

        try:
            import requests
            response = requests.get(f"http://{ip}:{port}/health", timeout=5)
            if response.status_code == 200:
                messagebox.showinfo("Success", f"Connected to SUT at {ip}:{port}")
            else:
                messagebox.showerror("Error", f"SUT returned status {response.status_code}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect: {str(e)}")

    def _verify_game_path(self, controller):
        """Verify game path on SUT."""
        messagebox.showinfo("Info", "Game path verification not yet implemented")

    def _reload_config(self, controller, game_var):
        """Reload config file for SUT and auto-populate game path."""
        widgets = self.sut_widgets[controller.name]
        config_path = widgets['config_var'].get()

        if not config_path or not os.path.exists(config_path):
            messagebox.showerror("Error", "Config file not found")
            return

        try:
            config_parser = HybridConfigParser(config_path)
            # Auto-load game path from config
            self._auto_load_game_path(controller, game_var)
            messagebox.showinfo("Success", f"Config loaded: {config_parser.game_name} ({config_parser.config_type})")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {str(e)}")

    def _auto_load_game_path(self, controller, game_var):
        """Auto-populate game path from config file metadata."""
        if not controller.config_path or not os.path.exists(controller.config_path):
            return

        try:
            # Load config and extract metadata
            config_parser = HybridConfigParser(controller.config_path)
            metadata = config_parser.get_game_metadata()

            # Get game path from metadata
            game_path_in_config = metadata.get("path", "")

            if game_path_in_config:
                # Auto-populate game path
                game_var.set(game_path_in_config)
                controller.game_path = game_path_in_config

                # Log to SUT's log queue if logger exists
                if controller.logger:
                    controller.logger.info(f"Auto-populated game path from config: {game_path_in_config}")
            else:
                # No path in config, clear if needed
                if not game_var.get():
                    game_var.set("")

        except Exception as e:
            # Silent fail - config might be loading
            pass

    def _load_available_configs(self):
        """Scan config/games folder and return dict of {game_name: file_path}."""
        config_dict = {}
        config_dir = "config/games"

        if not os.path.exists(config_dir):
            return config_dict

        try:
            for filename in os.listdir(config_dir):
                if filename.endswith('.yaml') or filename.endswith('.yml'):
                    filepath = os.path.join(config_dir, filename)
                    try:
                        # Parse config to get game name
                        config_parser = HybridConfigParser(filepath)
                        game_name = config_parser.game_name

                        # Use game name as key, if duplicate add filename
                        display_name = game_name
                        if display_name in config_dict:
                            display_name = f"{game_name} ({filename})"

                        config_dict[display_name] = filepath
                    except Exception as e:
                        # If parsing fails, use filename as fallback
                        config_dict[filename] = filepath

        except Exception as e:
            # If scanning fails, return empty dict
            pass

        return config_dict

    def _update_config_preview(self, controller, preview_frame, game_var):
        """Update the config preview panel with metadata."""
        widgets = self.sut_widgets[controller.name]
        preview_text = widgets['config_preview_text']

        if not controller.config_path or not os.path.exists(controller.config_path):
            preview_text.config(state='normal')
            preview_text.delete("1.0", tk.END)
            preview_text.insert("1.0", "No config selected")
            preview_text.config(state='disabled')
            return

        try:
            # Parse config
            config_parser = HybridConfigParser(controller.config_path)
            metadata = config_parser.get_game_metadata()
            config_type = config_parser.get_config_type()

            # Build preview text
            preview_lines = []
            preview_lines.append(f"Game Name:      {config_parser.game_name}")
            preview_lines.append(f"Config Type:    {'Step-based (SimpleAutomation)' if config_type == 'steps' else 'State Machine (DecisionEngine)'}")

            if metadata.get("resolution"):
                preview_lines.append(f"Resolution:     {metadata.get('resolution')}")
            if metadata.get("preset"):
                preview_lines.append(f"Preset:         {metadata.get('preset')}")
            if metadata.get("benchmark_duration"):
                preview_lines.append(f"Duration:       {metadata.get('benchmark_duration')} seconds")
            if metadata.get("path"):
                preview_lines.append(f"Game Path:      {metadata.get('path')}")

            preview_lines.append(f"File:           {controller.config_path}")

            # Update preview
            preview_text.config(state='normal')
            preview_text.delete("1.0", tk.END)
            preview_text.insert("1.0", "\n".join(preview_lines))
            preview_text.config(state='disabled')

            # Auto-load game path
            self._auto_load_game_path(controller, game_var)

        except Exception as e:
            preview_text.config(state='normal')
            preview_text.delete("1.0", tk.END)
            preview_text.insert("1.0", f"Error loading config: {str(e)}")
            preview_text.config(state='disabled')

    def _export_logs(self, controller):
        """Export logs for a SUT."""
        if not controller.current_run_dir:
            messagebox.showwarning("Warning", "No logs to export yet")
            return

        filename = filedialog.asksaveasfilename(
            title="Export Logs",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"{controller.name}_logs.txt"
        )

        if filename:
            try:
                widgets = self.sut_widgets[controller.name]
                log_content = widgets['log_text'].get("1.0", tk.END)
                with open(filename, 'w') as f:
                    f.write(log_content)
                messagebox.showinfo("Success", f"Logs exported to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export logs: {str(e)}")

    def _sync_controller_from_gui(self, controller):
        """Update controller object with latest values from GUI widgets."""
        widgets = self.sut_widgets.get(controller.name)
        if not widgets:
            return

        # Basic Config
        controller.ip = widgets['ip_var'].get()
        try:
            controller.port = int(widgets['port_var'].get())
        except ValueError:
            pass  # Keep existing port if invalid

        # Mode
        if 'mode_var' in widgets:
            mode = widgets['mode_var'].get()
            controller.campaign_mode = (mode == "campaign")

        # Campaign Settings
        if 'campaign_name_var' in widgets:
            controller.campaign_name = widgets['campaign_name_var'].get()
        if 'delay_between_games_var' in widgets:
            controller.delay_between_games = widgets['delay_between_games_var'].get()
        if 'continue_on_failure_var' in widgets:
            controller.continue_on_failure = widgets['continue_on_failure_var'].get()

        # Single Game Settings
        if 'game_var' in widgets:
            controller.game_path = widgets['game_var'].get()
        if 'run_count_var' in widgets:
            controller.run_count = widgets['run_count_var'].get()
        if 'run_delay_var' in widgets:
            controller.run_delay = widgets['run_delay_var'].get()

    def start_all(self):
        """Start automation on all SUTs."""
        if not self.sut_controllers:
            messagebox.showwarning("Warning", "No SUTs configured")
            return

        shared_settings = {
            "vision_model": self.vision_model.get(),
            "omniparser_url": self.omniparser_url.get(),
            "lm_studio_url": self.lm_studio_url.get(),
            "max_iterations": int(self.max_iterations.get()),
            "log_level": self.log_level.get()
        }

        for name, controller in self.sut_controllers.items():
            if controller.status == "Idle":
                # Update controller from GUI widgets
                widgets = self.sut_widgets.get(name)
                if widgets:
                    controller.run_count = widgets['run_count_var'].get()
                    controller.run_delay = widgets['run_delay_var'].get()
                controller.start_automation(shared_settings)

    def stop_all(self):
        """Stop automation on all SUTs."""
        for controller in self.sut_controllers.values():
            controller.stop_automation()

    def clear_all_logs(self):
        """Clear logs on all tabs."""
        for name, widgets in self.sut_widgets.items():
            widgets['log_text'].delete("1.0", tk.END)

    def remove_current_sut(self):
        """Remove the currently selected SUT tab."""
        if not self.sut_controllers:
            messagebox.showwarning("Warning", "No SUTs to remove")
            return

        # Get currently selected tab
        try:
            current_tab_index = self.notebook.index(self.notebook.select())
            current_tab = self.notebook.select()

            # Find the SUT name for this tab
            sut_name = None
            for name, tab in self.sut_tabs.items():
                if str(tab) == str(current_tab):
                    sut_name = name
                    break

            if not sut_name:
                messagebox.showwarning("Warning", "No SUT selected")
                return

            # Confirm removal
            if messagebox.askyesno("Confirm Removal", f"Remove SUT '{sut_name}'?\n\nThis will stop any running automation."):
                self._remove_sut(sut_name)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to remove SUT: {str(e)}")

    def save_multi_config(self):
        """Save multi-SUT configuration to JSON."""
        filename = filedialog.asksaveasfilename(
            title="Save Multi-SUT Config",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="multi_sut_config.json"
        )

        if not filename:
            return

        # Synchronize all controllers with GUI state before saving
        for controller in self.sut_controllers.values():
            self._sync_controller_from_gui(controller)

        config = {
            "version": "1.0",
            "shared_settings": {
                "vision_model": self.vision_model.get(),
                "omniparser_url": self.omniparser_url.get(),
                "lm_studio_url": self.lm_studio_url.get(),
                "max_iterations": int(self.max_iterations.get()),
                "log_level": self.log_level.get()
            },
            "suts": [controller.to_dict() for controller in self.sut_controllers.values()]
        }

        try:
            with open(filename, 'w') as f:
                json.dump(config, f, indent=2)
            messagebox.showinfo("Success", f"Configuration saved to {filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {str(e)}")

    def load_multi_config(self):
        """Load multi-SUT configuration from JSON."""
        filename = filedialog.askopenfilename(
            title="Load Multi-SUT Config",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir="."
        )

        if not filename:
            return

        try:
            with open(filename, 'r') as f:
                config = json.load(f)

            # Load shared settings
            shared = config.get("shared_settings", {})
            self.vision_model.set(shared.get("vision_model", "omniparser"))
            self.omniparser_url.set(shared.get("omniparser_url", "http://localhost:9000"))
            self.lm_studio_url.set(shared.get("lm_studio_url", "http://127.0.0.1:1234"))
            self.max_iterations.set(str(shared.get("max_iterations", 50)))
            self.log_level.set(shared.get("log_level", "INFO"))

            # Clear existing SUTs
            for name in list(self.sut_controllers.keys()):
                self._remove_sut(name)

            # Load SUTs
            for sut_data in config.get("suts", []):
                controller = SUTController.from_dict(sut_data)

                # Assign color from palette
                color = self.color_palette[self.next_color_index % len(self.color_palette)]
                self.next_color_index += 1
                controller.color = color

                self.sut_controllers[controller.name] = controller

                # Create tab
                tab_frame = ttk.Frame(self.notebook)
                self.notebook.add(tab_frame, text=f"{controller.name}: {controller.ip}")
                self.sut_tabs[controller.name] = tab_frame

                # Create widgets
                self._create_sut_tab_content(controller.name, tab_frame, controller)

            messagebox.showinfo("Success", f"Loaded {len(config.get('suts', []))} SUTs from {filename}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load config: {str(e)}")

    def _remove_sut(self, name):
        """Remove a SUT controller and tab."""
        if name not in self.sut_controllers:
            return

        # Stop automation if running
        self.sut_controllers[name].stop_automation()

        # Remove tab
        if name in self.sut_tabs:
            tab = self.sut_tabs[name]
            self.notebook.forget(tab)
            del self.sut_tabs[name]

        # Remove widgets
        if name in self.sut_widgets:
            del self.sut_widgets[name]

        # Remove controller
        del self.sut_controllers[name]

    def on_vision_model_change(self):
        """Handle vision model selection change."""
        # Update UI based on selection
        pass

    def test_omniparser(self):
        """Test connection to Omniparser server."""
        try:
            import requests
            response = requests.get(f"{self.omniparser_url.get()}/probe", timeout=5)
            if response.status_code == 200:
                self.omniparser_status_label.config(text="Connected", foreground="green")
                messagebox.showinfo("Success", "Connected to Omniparser server")
            else:
                self.omniparser_status_label.config(text="Failed", foreground="red")
                messagebox.showerror("Error", f"Server returned status {response.status_code}")
        except Exception as e:
            self.omniparser_status_label.config(text="Failed", foreground="red")
            messagebox.showerror("Error", f"Failed to connect: {str(e)}")

    def on_tab_changed(self, event):
        """Handle tab change event."""
        pass

    def _update_preview(self, controller, widgets):
        """Update the live preview canvas with latest screenshot from SUT."""
        try:
            # Fetch screenshot from SUT
            response = requests.get(
                f"http://{controller.ip}:{controller.port}/screenshot",
                timeout=2  # Short timeout for responsive UI
            )

            if response.status_code == 200:
                # Load image from response
                image_data = BytesIO(response.content)
                image = Image.open(image_data)

                # Resize to fit preview canvas (320x180)
                # Use LANCZOS for better quality at small sizes
                image_resized = image.resize((320, 180), Image.Resampling.LANCZOS)

                # Convert to PhotoImage for tkinter
                photo = ImageTk.PhotoImage(image_resized)

                # Update canvas
                canvas = widgets['preview_canvas']

                # Remove old image if exists
                if widgets['preview_image_id'] is not None:
                    canvas.delete(widgets['preview_image_id'])

                # Add new image
                image_id = canvas.create_image(160, 90, image=photo)

                # Store references to prevent garbage collection
                widgets['preview_photo'] = photo
                widgets['preview_image_id'] = image_id

        except requests.exceptions.Timeout:
            # Timeout - SUT might be busy, skip this frame
            pass
        except requests.exceptions.ConnectionError:
            # SUT not connected - clear preview
            canvas = widgets.get('preview_canvas')
            if canvas and widgets.get('preview_image_id'):
                canvas.delete(widgets['preview_image_id'])
                widgets['preview_image_id'] = None
                canvas.create_text(160, 90, text="SUT Disconnected",
                                 fill="red", font=("TkDefaultFont", 10))
        except Exception as e:
            # Other errors - silently skip
            pass

    def _switch_workflow_mode(self, controller, widgets, mode):
        """Switch between single game and campaign mode."""
        single_frame = widgets.get('single_game_frame')
        campaign_frame = widgets.get('campaign_frame')

        if mode == "single":
            # Switch to single game mode
            controller.campaign_mode = False
            if campaign_frame:
                campaign_frame.pack_forget()
            if single_frame:
                single_frame.pack(fill=tk.BOTH, expand=True)

        elif mode == "campaign":
            # Switch to campaign mode
            controller.campaign_mode = True
            if single_frame:
                single_frame.pack_forget()
            if campaign_frame:
                campaign_frame.pack(fill=tk.BOTH, expand=True)
                # Refresh the campaign list
                self._refresh_campaign_list(controller, widgets)

    def _get_campaign_stats(self, controller):
        """Calculate and return campaign statistics string."""
        total_games = len(controller.campaign)
        total_runs = sum(game.run_count for game in controller.campaign)
        return f"Total: {total_games} games, {total_runs} runs"

    def _refresh_campaign_list(self, controller, widgets):
        """Refresh the campaign game list display."""
        list_frame = widgets.get('campaign_list_frame')
        if not list_frame:
            return

        # Clear existing widgets
        for widget in list_frame.winfo_children():
            widget.destroy()

        # Show games
        if not controller.campaign:
            # Empty state
            ttk.Label(list_frame, text="No games in campaign. Click '+ Add Game to Campaign' to start.",
                     foreground="gray", font=("TkDefaultFont", 9)).pack(pady=20)
        else:
            # Display each game
            for idx, game in enumerate(controller.campaign):
                self._create_game_entry_widget(list_frame, controller, widgets, idx, game)

        # Update statistics
        stats_var = widgets.get('campaign_stats_var')
        if stats_var:
            stats_var.set(self._get_campaign_stats(controller))

    def _create_game_entry_widget(self, parent, controller, widgets, index, game):
        """Create a single game entry widget in the campaign list."""
        # Game entry frame with border
        game_frame = ttk.Frame(parent, relief="solid", borderwidth=1)
        game_frame.pack(fill=tk.X, pady=2, padx=2)

        # Left section: Game number and info
        left_section = ttk.Frame(game_frame)
        left_section.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)

        # Row 1: Number and game name
        row1 = ttk.Frame(left_section)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text=f"#{index+1}", font=("TkDefaultFont", 10, "bold"),
                 foreground="blue").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row1, text=game.game_name, font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT)

        # Row 2: Config file
        row2 = ttk.Frame(left_section)
        row2.pack(fill=tk.X)
        ttk.Label(row2, text=f"Config: {os.path.basename(game.config_path)}",
                 font=("TkDefaultFont", 8), foreground="gray").pack(side=tk.LEFT)

        # Row 3: Run settings
        row3 = ttk.Frame(left_section)
        row3.pack(fill=tk.X)
        ttk.Label(row3, text=f"Runs: {game.run_count} | Delay: {game.run_delay}s",
                 font=("TkDefaultFont", 8), foreground="gray").pack(side=tk.LEFT)

        # Right section: Action buttons
        right_section = ttk.Frame(game_frame)
        right_section.pack(side=tk.RIGHT, padx=5, pady=5)

        button_size = 6
        ttk.Button(right_section, text="Edit", width=button_size,
                  command=lambda: self._edit_game_dialog(controller, widgets, index)).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_section, text="↑", width=3,
                  command=lambda: self._move_game_up(controller, widgets, index)).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_section, text="↓", width=3,
                  command=lambda: self._move_game_down(controller, widgets, index)).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_section, text="Remove", width=button_size,
                  command=lambda: self._remove_game_from_campaign(controller, widgets, index)).pack(side=tk.LEFT, padx=2)

    def _add_game_dialog(self, controller, widgets):
        """Show dialog to add a new game to the campaign."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Game to Campaign")
        dialog.geometry("600x400")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        # Dialog content
        content_frame = ttk.Frame(dialog, padding="20")
        content_frame.pack(fill=tk.BOTH, expand=True)

        # Config selection
        ttk.Label(content_frame, text="Select Config:", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(0, 10), columnspan=2)

        config_dict = self._load_available_configs()
        config_names = list(config_dict.keys())

        config_var = tk.StringVar()
        config_dropdown = ttk.Combobox(content_frame, textvariable=config_var,
                                       values=config_names, state='readonly', width=50)
        config_dropdown.grid(row=1, column=0, columnspan=2, pady=(0, 20), sticky=tk.W)

        # Game name (auto-filled from config)
        ttk.Label(content_frame, text="Game Name:").grid(row=2, column=0, sticky=tk.W, pady=5)
        game_name_var = tk.StringVar()
        ttk.Entry(content_frame, textvariable=game_name_var, width=40).grid(row=2, column=1, pady=5, padx=(10, 0))

        # Game path (auto-filled from config)
        ttk.Label(content_frame, text="Game Path:").grid(row=3, column=0, sticky=tk.W, pady=5)
        game_path_var = tk.StringVar()
        ttk.Entry(content_frame, textvariable=game_path_var, width=40).grid(row=3, column=1, pady=5, padx=(10, 0))

        # Run count
        ttk.Label(content_frame, text="Run Count:").grid(row=4, column=0, sticky=tk.W, pady=5)
        run_count_var = tk.IntVar(value=3)
        ttk.Spinbox(content_frame, from_=1, to=100, textvariable=run_count_var, width=10).grid(row=4, column=1, pady=5, padx=(10, 0), sticky=tk.W)

        # Run delay
        ttk.Label(content_frame, text="Delay between runs (sec):").grid(row=5, column=0, sticky=tk.W, pady=5)
        run_delay_var = tk.IntVar(value=30)
        ttk.Spinbox(content_frame, from_=0, to=3600, textvariable=run_delay_var, width=10).grid(row=5, column=1, pady=5, padx=(10, 0), sticky=tk.W)

        # Auto-populate when config is selected
        def on_config_selected(event=None):
            selected_name = config_var.get()
            if selected_name and selected_name in config_dict:
                config_path = config_dict[selected_name]
                try:
                    config_parser = HybridConfigParser(config_path)
                    metadata = config_parser.get_game_metadata()
                    game_name_var.set(config_parser.game_name)
                    game_path_var.set(metadata.get("path", ""))
                except Exception as e:
                    game_name_var.set(selected_name)

        config_dropdown.bind('<<ComboboxSelected>>', on_config_selected)

        # Buttons
        button_frame = ttk.Frame(content_frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=20)

        def on_add():
            if not config_var.get():
                messagebox.showerror("Error", "Please select a config")
                return
            if not game_name_var.get():
                messagebox.showerror("Error", "Please enter a game name")
                return

            # Create game entry
            game_entry = GameEntry(
                game_name=game_name_var.get(),
                config_path=config_dict[config_var.get()],
                game_path=game_path_var.get(),
                run_count=run_count_var.get(),
                run_delay=run_delay_var.get()
            )

            # Add to campaign
            controller.add_game_to_campaign(game_entry)

            # Refresh UI
            self._refresh_campaign_list(controller, widgets)

            dialog.destroy()

        ttk.Button(button_frame, text="Add", command=on_add, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _edit_game_dialog(self, controller, widgets, index):
        """Show dialog to edit a game in the campaign."""
        if index < 0 or index >= len(controller.campaign):
            return

        game = controller.campaign[index]

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit Game: {game.game_name}")
        dialog.geometry("600x350")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        # Dialog content
        content_frame = ttk.Frame(dialog, padding="20")
        content_frame.pack(fill=tk.BOTH, expand=True)

        # Config selection
        ttk.Label(content_frame, text="Config:", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Label(content_frame, text=os.path.basename(game.config_path), foreground="blue").grid(row=0, column=1, sticky=tk.W, pady=5, padx=(10, 0))

        # Game name
        ttk.Label(content_frame, text="Game Name:").grid(row=1, column=0, sticky=tk.W, pady=5)
        game_name_var = tk.StringVar(value=game.game_name)
        ttk.Entry(content_frame, textvariable=game_name_var, width=40).grid(row=1, column=1, pady=5, padx=(10, 0))

        # Game path
        ttk.Label(content_frame, text="Game Path:").grid(row=2, column=0, sticky=tk.W, pady=5)
        game_path_var = tk.StringVar(value=game.game_path)
        ttk.Entry(content_frame, textvariable=game_path_var, width=40).grid(row=2, column=1, pady=5, padx=(10, 0))

        # Run count
        ttk.Label(content_frame, text="Run Count:").grid(row=3, column=0, sticky=tk.W, pady=5)
        run_count_var = tk.IntVar(value=game.run_count)
        ttk.Spinbox(content_frame, from_=1, to=100, textvariable=run_count_var, width=10).grid(row=3, column=1, pady=5, padx=(10, 0), sticky=tk.W)

        # Run delay
        ttk.Label(content_frame, text="Delay between runs (sec):").grid(row=4, column=0, sticky=tk.W, pady=5)
        run_delay_var = tk.IntVar(value=game.run_delay)
        ttk.Spinbox(content_frame, from_=0, to=3600, textvariable=run_delay_var, width=10).grid(row=4, column=1, pady=5, padx=(10, 0), sticky=tk.W)

        # Buttons
        button_frame = ttk.Frame(content_frame)
        button_frame.grid(row=5, column=0, columnspan=2, pady=20)

        def on_save():
            # Update game entry
            game.game_name = game_name_var.get()
            game.game_path = game_path_var.get()
            game.run_count = run_count_var.get()
            game.run_delay = run_delay_var.get()

            # Refresh UI
            self._refresh_campaign_list(controller, widgets)
            dialog.destroy()

        ttk.Button(button_frame, text="Save", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _move_game_up(self, controller, widgets, index):
        """Move a game up in the campaign order."""
        controller.move_game_up(index)
        self._refresh_campaign_list(controller, widgets)

    def _move_game_down(self, controller, widgets, index):
        """Move a game down in the campaign order."""
        controller.move_game_down(index)
        self._refresh_campaign_list(controller, widgets)

    def _remove_game_from_campaign(self, controller, widgets, index):
        """Remove a game from the campaign."""
        if index < 0 or index >= len(controller.campaign):
            return

        game = controller.campaign[index]
        if messagebox.askyesno("Confirm Removal", f"Remove '{game.game_name}' from campaign?"):
            controller.remove_game_from_campaign(index)
            self._refresh_campaign_list(controller, widgets)

    def _clear_campaign(self, controller, widgets):
        """Clear all games from the campaign."""
        if not controller.campaign:
            messagebox.showinfo("Info", "Campaign is already empty")
            return

        if messagebox.askyesno("Confirm Clear", f"Remove all {len(controller.campaign)} games from campaign?"):
            controller.clear_campaign()
            self._refresh_campaign_list(controller, widgets)

    def _save_campaign(self, controller):
        """Save campaign to JSON file."""
        # Update campaign name and delay from UI
        widgets = self.sut_widgets.get(controller.name)
        if widgets:
            controller.campaign_name = widgets.get('campaign_name_var').get()
            controller.delay_between_games = widgets.get('delay_between_games_var').get()

        if not controller.campaign:
            messagebox.showwarning("Warning", "Campaign is empty. Add games before saving.")
            return

        # Default filename
        default_filename = f"{sanitize_folder_name(controller.campaign_name)}_campaign.json"

        filename = filedialog.asksaveasfilename(
            title="Save Campaign",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir="config/campaigns",
            initialfile=default_filename
        )

        if not filename:
            return

        # Ensure directory exists
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        # Build campaign data
        campaign_data = {
            "version": "1.0",
            "campaign_name": controller.campaign_name,
            "delay_between_games": controller.delay_between_games,
            "continue_on_failure": controller.continue_on_failure,
            "games": [game.to_dict() for game in controller.campaign]
        }

        try:
            with open(filename, 'w') as f:
                json.dump(campaign_data, f, indent=2)
            messagebox.showinfo("Success", f"Campaign saved to {filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save campaign: {str(e)}")

    def _load_campaign(self, controller, widgets):
        """Load campaign from JSON file."""
        filename = filedialog.askopenfilename(
            title="Load Campaign",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir="config/campaigns"
        )

        if not filename:
            return

        try:
            with open(filename, 'r') as f:
                campaign_data = json.load(f)

            # Load campaign metadata
            controller.campaign_name = campaign_data.get("campaign_name", "Loaded Campaign")
            controller.delay_between_games = campaign_data.get("delay_between_games", 120)
            controller.continue_on_failure = campaign_data.get("continue_on_failure", True)

            # Load games
            controller.campaign.clear()
            for game_data in campaign_data.get("games", []):
                game_entry = GameEntry.from_dict(game_data)
                controller.campaign.append(game_entry)

            controller.total_games = len(controller.campaign)
            controller.campaign_mode = True

            # Update UI
            if widgets:
                widgets.get('campaign_name_var').set(controller.campaign_name)
                widgets.get('delay_between_games_var').set(controller.delay_between_games)
                widgets.get('continue_on_failure_var').set(controller.continue_on_failure)
                widgets.get('mode_var').set("campaign")  # Update radio button
                # Switch to campaign frame
                self._switch_workflow_mode(controller, widgets, "campaign")

            messagebox.showinfo("Success", f"Loaded {len(controller.campaign)} games from {os.path.basename(filename)}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load campaign: {str(e)}")

    def update_gui(self):
        """Periodic GUI update - poll log queues and update status."""
        # Update each SUT
        for name, controller in self.sut_controllers.items():
            if name not in self.sut_widgets:
                continue

            widgets = self.sut_widgets[name]

            # Update status
            status_label = widgets['status_label']
            status_label.config(text=controller.status, foreground=controller.get_status_color())

            # Update step progress
            steps_label = widgets['steps_label']
            steps_label.config(text=f"Steps: {controller.completed_steps}/{controller.total_steps}")

            # Update run iteration progress (campaign-aware)
            run_label = widgets['run_label']
            if controller.campaign_mode and controller.status == "Running":
                # Campaign mode: show game and run progress
                game_progress = f"Game: {controller.current_game_index + 1}/{controller.total_games}"
                run_progress = f"Run: {controller.current_run}/{controller.total_runs}"
                game_name_short = controller.current_game_name[:20] + "..." if len(controller.current_game_name) > 20 else controller.current_game_name
                run_label.config(text=f"{game_progress} ({game_name_short}) | {run_progress}")
            else:
                # Single game mode: show run progress only
                run_label.config(text=f"Run: {controller.current_run}/{controller.total_runs}")

            # Update live preview (0.25 FPS = update every 4000ms = 1 frame per 4 seconds)
            current_time = time.time()
            if controller.preview_enabled and (current_time - controller.last_preview_update) >= 4.0:
                self._update_preview(controller, widgets)
                controller.last_preview_update = current_time

            # Update tab title with status indicator
            tab_text = f"{controller.name}: {controller.ip}"
            if controller.status == "Running":
                tab_text += " ●"
            elif controller.status == "Failed" or controller.status == "Error":
                tab_text += " ●"

            # Find tab index and update
            for idx in range(self.notebook.index("end")):
                if self.notebook.tab(idx, "text").startswith(controller.name):
                    self.notebook.tab(idx, text=tab_text)
                    break

            # Poll log queue
            log_text = widgets['log_text']
            while not controller.log_queue.empty():
                try:
                    record = controller.log_queue.get_nowait()
                    msg = record.getMessage()
                    timestamp = time.strftime('%H:%M:%S', time.localtime(record.created))
                    log_line = f"{timestamp} - {record.name} - {record.levelname} - {msg}\n"

                    # Insert log line with color tag based on level
                    tag = record.levelname if record.levelname in ["ERROR", "WARNING", "INFO", "DEBUG", "CRITICAL"] else "INFO"
                    log_text.insert(tk.END, log_line, tag)
                    log_text.see(tk.END)
                except queue.Empty:
                    break

        # Schedule next update
        self.root.after(100, self.update_gui)


def main():
    """Main entry point."""
    root = tk.Tk()
    app = MultiSUTGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
