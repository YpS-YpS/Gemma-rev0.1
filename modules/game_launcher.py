"""
Game Launcher module for starting games on the SUT.
"""

import logging
from typing import Dict, Any

from modules.network import NetworkManager

logger = logging.getLogger(__name__)

class GameLauncher:
    """Handles launching games on the SUT."""
    
    def __init__(self, network_manager: NetworkManager):
        """
        Initialize the game launcher.
        
        Args:
            network_manager: NetworkManager instance for communication with SUT
        """
        self.network_manager = network_manager
        logger.info("GameLauncher initialized")
    
    def launch(self, game_path: str, process_id: str = '', startup_wait: int = 15) -> bool:
        """
        Launch a game on the SUT.

        Args:
            game_path: Path to the game executable or Steam app ID on the SUT
            process_id: Optional process name to wait for after launch (e.g., 'Launcher', 'Game')
            startup_wait: Maximum seconds to wait for process to appear (default: 15)

        Returns:
            True if the game was successfully launched

        Raises:
            RuntimeError: If the game fails to launch
        """
        try:
            # Log launch parameters at debug level
            logger.debug(f"Launch request - path: {game_path}, process_id: {process_id}, startup_wait: {startup_wait}")
            
            # Send launch command to SUT with process tracking metadata
            response = self.network_manager.launch_game(game_path, process_id, startup_wait)
            
            # Log full response at debug level
            logger.debug(f"SUT launch response: {response}")

            # Check response
            status = response.get("status")
            if status == "success":
                # Parse detailed status
                proc_name = response.get("game_process_name", "Unknown")
                proc_pid = response.get("game_process_pid", "N/A")
                fg_confirmed = response.get("foreground_confirmed", False)
                launch_method = response.get("launch_method", "unknown")
                subprocess_pid = response.get("subprocess_pid", "N/A")
                subprocess_status = response.get("subprocess_status", "unknown")
                
                logger.info(f"Game launched successfully: {game_path}")
                logger.debug(f"  - Subprocess PID: {subprocess_pid} ({subprocess_status})")
                logger.info(f"  - Launch Method: {launch_method}")
                logger.info(f"  - Process Detected: {proc_name} (PID: {proc_pid})")
                logger.info(f"  - Foreground Confirmed: {fg_confirmed}")
                return True
            elif status == "warning":
                 # Foreground is required for automation - treat warning as failure
                 # The SUT already retries 3 times with 10s intervals
                 warning_msg = response.get("warning", "Unknown warning")
                 logger.error(f"Game launch failed: {warning_msg}")
                 raise RuntimeError(f"Game launch failed: {warning_msg}")
            else:
                error_msg = response.get("error", "Unknown error")
                logger.error(f"Failed to launch game: {error_msg}")
                raise RuntimeError(f"Game launch failed: {error_msg}")

        except Exception as e:
            logger.error(f"Error launching game: {str(e)}")
            raise RuntimeError(f"Game launch error: {str(e)}")
    
    def terminate(self) -> bool:
        """
        Terminate the currently running game on the SUT.
        
        Returns:
            True if the game was successfully terminated
        
        Raises:
            RuntimeError: If the game fails to terminate
        """
        try:
            # Send terminate command to SUT
            response = self.network_manager.send_action({
                "type": "terminate_game"
            })
            
            # Check response
            if response.get("status") == "success":
                logger.info("Game terminated successfully")
                return True
            else:
                error_msg = response.get("error", "Unknown error")
                logger.error(f"Failed to terminate game: {error_msg}")
                raise RuntimeError(f"Game termination failed: {error_msg}")
                
        except Exception as e:
            logger.error(f"Error terminating game: {str(e)}")
            raise RuntimeError(f"Game termination error: {str(e)}")