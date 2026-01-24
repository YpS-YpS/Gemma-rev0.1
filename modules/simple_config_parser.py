"""
Configuration parser for the simplified step-based YAML format.
"""

import os
import yaml
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class SimpleConfigParser:
    """Handles loading and parsing the simplified step-based YAML configuration."""
    
    def __init__(self, config_path: str):
        """
        Initialize the simple config parser.
        
        Args:
            config_path: Path to the YAML configuration file
        
        Raises:
            FileNotFoundError: If the config file doesn't exist
            ValueError: If the config file is invalid
        """
        self.config_path = config_path
        self.config = self._load_config()
        self._validate_config()
        
        # Extract basic metadata
        self.game_name = self.config.get("metadata", {}).get("game_name", "Unknown Game")
        logger.info(f"SimpleConfigParser initialized for {self.game_name} using {config_path}")
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load the YAML configuration file.
        
        Returns:
            Parsed configuration as a dictionary
        
        Raises:
            FileNotFoundError: If the config file doesn't exist
            yaml.YAMLError: If the YAML is invalid
        """
        if not os.path.exists(self.config_path):
            logger.error(f"Config file not found: {self.config_path}")
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            logger.info(f"Loaded configuration from {self.config_path}")
            return config
            
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML config: {str(e)}")
            raise
    
    # Valid action types for step actions
    VALID_ACTION_TYPES = {
        "click", "key", "keypress", "hotkey", "type", "text", "input",
        "double_click", "right_click", "middle_click", "drag", "drag_drop",
        "scroll", "wait", "conditional", "sequence", "sideload"
    }

    def _validate_config(self) -> bool:
        """
        Validate the configuration structure.

        Returns:
            True if valid

        Raises:
            ValueError: If the config is invalid
        """
        # Check for required sections
        if "steps" not in self.config:
            logger.error("Missing required 'steps' section in config")
            raise ValueError("Invalid config: missing 'steps' section")

        # Validate hooks section if present
        if "hooks" in self.config:
            self._validate_hooks(self.config["hooks"])

        # Validate steps
        steps = self.config.get("steps", {})
        if not isinstance(steps, dict) or not steps:
            logger.error("Steps section must be a non-empty dictionary")
            raise ValueError("Invalid config: steps section must be a non-empty dictionary")

        # Validate each step
        for step_num, step in steps.items():
            if "description" not in step:
                logger.warning(f"Step {step_num} missing description")

            # Check that step has either:
            # 1. A 'find' section (for steps that need to locate UI elements)
            # 2. An 'action' section without 'find' (for action-only steps like wait, key press)
            has_find = "find" in step
            has_action = "action" in step

            if not has_find and not has_action:
                logger.error(f"Step {step_num} must have either 'find' or 'action' section")
                raise ValueError(f"Invalid step {step_num}: missing 'find' or 'action'")

            # Validate find section if present
            if has_find:
                find_section = step["find"]
                if not isinstance(find_section, dict):
                    logger.error(f"Step {step_num} 'find' must be a dictionary")
                    raise ValueError(f"Invalid step {step_num}: 'find' must be a dictionary")

                # Check for required fields in find section
                if "type" not in find_section and "text" not in find_section:
                    logger.warning(f"Step {step_num} 'find' should have 'type' and/or 'text'")

            # Validate action section if present
            if has_action:
                action_section = step["action"]
                if not isinstance(action_section, dict):
                    logger.error(f"Step {step_num} 'action' must be a dictionary")
                    raise ValueError(f"Invalid step {step_num}: 'action' must be a dictionary")

                # Check for action type
                if "type" not in action_section:
                    logger.error(f"Step {step_num} 'action' must have 'type' field")
                    raise ValueError(f"Invalid step {step_num}: 'action' missing 'type'")

                # Validate action type is known
                action_type = action_section.get("type", "").lower()
                if action_type and action_type not in self.VALID_ACTION_TYPES:
                    logger.warning(f"Step {step_num} has unknown action type: '{action_type}'")

                # Validate sideload action has required fields
                if action_type == "sideload":
                    self._validate_sideload_action(action_section, step_num)

        logger.info("Simple configuration validation successful")
        return True

    def _validate_hooks(self, hooks: Dict[str, Any]) -> None:
        """
        Validate the hooks configuration section.

        Args:
            hooks: Hooks configuration dictionary

        Raises:
            ValueError: If hooks configuration is invalid
        """
        if not isinstance(hooks, dict):
            logger.error("'hooks' must be a dictionary")
            raise ValueError("Invalid config: 'hooks' must be a dictionary")

        # Validate pre hooks
        if "pre" in hooks:
            pre_hooks = hooks["pre"]
            if not isinstance(pre_hooks, list):
                logger.error("'hooks.pre' must be a list")
                raise ValueError("Invalid config: 'hooks.pre' must be a list")

            for i, hook in enumerate(pre_hooks):
                self._validate_hook_entry(hook, f"pre-hook {i+1}")

        # Validate post hooks
        if "post" in hooks:
            post_hooks = hooks["post"]
            if not isinstance(post_hooks, list):
                logger.error("'hooks.post' must be a list")
                raise ValueError("Invalid config: 'hooks.post' must be a list")

            for i, hook in enumerate(post_hooks):
                self._validate_hook_entry(hook, f"post-hook {i+1}")

        logger.info(f"Hooks validation passed: {len(hooks.get('pre', []))} pre, {len(hooks.get('post', []))} post")

    def _validate_hook_entry(self, hook: Dict[str, Any], hook_name: str) -> None:
        """
        Validate a single hook entry.

        Args:
            hook: Hook configuration dictionary
            hook_name: Name for error messages

        Raises:
            ValueError: If hook configuration is invalid
        """
        if not isinstance(hook, dict):
            logger.error(f"{hook_name} must be a dictionary")
            raise ValueError(f"Invalid config: {hook_name} must be a dictionary")

        # 'path' is required
        if "path" not in hook:
            logger.error(f"{hook_name} missing required 'path' field")
            raise ValueError(f"Invalid config: {hook_name} missing 'path'")

        path = hook["path"]
        if not isinstance(path, str) or not path.strip():
            logger.error(f"{hook_name} 'path' must be a non-empty string")
            raise ValueError(f"Invalid config: {hook_name} 'path' must be a non-empty string")

        # Validate optional fields types
        if "args" in hook and not isinstance(hook["args"], list):
            logger.error(f"{hook_name} 'args' must be a list")
            raise ValueError(f"Invalid config: {hook_name} 'args' must be a list")

        if "timeout" in hook:
            timeout = hook["timeout"]
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                logger.warning(f"{hook_name} 'timeout' should be a positive number")

        if "persistent" in hook and not isinstance(hook["persistent"], bool):
            logger.warning(f"{hook_name} 'persistent' should be a boolean")

    def _validate_sideload_action(self, action: Dict[str, Any], step_num: str) -> None:
        """
        Validate a sideload action configuration.

        Args:
            action: Action configuration dictionary
            step_num: Step number for error messages

        Raises:
            ValueError: If sideload configuration is invalid
        """
        # 'path' is required for sideload
        if "path" not in action:
            logger.error(f"Step {step_num} sideload action missing required 'path' field")
            raise ValueError(f"Invalid step {step_num}: sideload action missing 'path'")

        path = action["path"]
        if not isinstance(path, str) or not path.strip():
            logger.error(f"Step {step_num} sideload 'path' must be a non-empty string")
            raise ValueError(f"Invalid step {step_num}: sideload 'path' must be a non-empty string")

        # Validate optional fields
        if "args" in action and not isinstance(action["args"], list):
            logger.warning(f"Step {step_num} sideload 'args' should be a list")

        if "timeout" in action:
            timeout = action["timeout"]
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                logger.warning(f"Step {step_num} sideload 'timeout' should be a positive number")
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get the parsed configuration.
        
        Returns:
            Configuration dictionary
        """
        return self.config
    
    def get_step(self, step_num: str) -> Optional[Dict[str, Any]]:
        """
        Get the definition for a specific step.
        
        Args:
            step_num: Step number as string
        
        Returns:
            Step definition dictionary or None if not found
        """
        steps = self.config.get("steps", {})
        return steps.get(step_num)
    
    def get_metadata(self) -> Dict[str, Any]:
        """
        Get game metadata from the configuration.
        
        Returns:
            Metadata dictionary with game information
        """
        return self.config.get("metadata", {})