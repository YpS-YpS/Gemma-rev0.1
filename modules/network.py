"""
Network communication module for ARL-SUT interaction.
Handles all network operations between the development PC and the system under test.
"""

import socket
import json
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class NetworkManager:
    """Manages network communication with the SUT."""
    
    def __init__(self, sut_ip: str, sut_port: int):
        """
        Initialize the network manager.
        
        Args:
            sut_ip: IP address of the system under test
            sut_port: Port number for communication
        """
        self.sut_ip = sut_ip
        self.sut_port = sut_port
        self.base_url = f"http://{sut_ip}:{sut_port}"
        self.session = requests.Session()
        logger.info(f"NetworkManager initialized with SUT at {self.base_url}")
        
        # Verify connection
        try:
            self._check_connection()
        except Exception as e:
            logger.error(f"Failed to connect to SUT: {str(e)}")
            raise
    
    def _check_connection(self) -> bool:
        """
        Check if the SUT is reachable.
        
        Returns:
            True if connection is successful
        
        Raises:
            ConnectionError: If SUT is not reachable
        """
        try:
            response = self.session.get(f"{self.base_url}/status", timeout=5)
            response.raise_for_status()
            logger.info("Successfully connected to SUT")
            return True
        except requests.RequestException as e:
            logger.error(f"Connection check failed: {str(e)}")
            raise ConnectionError(f"Cannot connect to SUT at {self.base_url}: {str(e)}")
    
    def get_resolution(self) -> dict:
        """
        Get the screen resolution of the SUT.
        
        Returns:
            Dictionary with 'width' and 'height' keys
        """
        try:
            response = self.session.get(f"{self.base_url}/status", timeout=5)
            response.raise_for_status()
            data = response.json()
            return {
                "width": data.get("screen_width", 1920),
                "height": data.get("screen_height", 1080)
            }
        except Exception as e:
            logger.warning(f"Failed to get resolution from SUT, defaulting to 1920x1080: {e}")
            return {"width": 1920, "height": 1080}

    def login_steam(self, username: str, password: str) -> bool:
        """
        Login to Steam via SUT service.
        
        Args:
            username: Steam username
            password: Steam password
        
        Returns:
            True if login successful, False otherwise
        """
        try:
            payload = {"username": username, "password": password}
            logger.info(f"[Steam] Logging in as: {username}")
            
            # Timeout: kill processes (~3s) + steam.exe launch + login (~60s)
            response = self.session.post(f"{self.base_url}/login_steam", json=payload, timeout=120)
            
            result = response.json()
            status = result.get('status', 'unknown')
            message = result.get('message', '')
            user_id = result.get('user_id', '')
            
            if status == 'success':
                logger.info(f"[Steam] ✓ Login successful: {message}")
                if user_id:
                    logger.info(f"[Steam] User ID: {user_id}")
                return True
            elif status == 'warning':
                logger.warning(f"[Steam] ⚠ {message}")
                return True  # Proceed with caution
            else:
                logger.error(f"[Steam] ✗ Login failed: {message or result.get('error', 'Unknown error')}")
                return False
                
        except Exception as e:
            logger.error(f"[Steam] Request failed: {e}")
            return False

    def send_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send an action command to the SUT.
        
        Args:
            action: Dictionary containing action details
                   Example: {"type": "click", "x": 100, "y": 200}
        
        Returns:
            Response from the SUT as a dictionary
        
        Raises:
            RequestException: If the request fails
        """
        try:
            response = self.session.post(
                f"{self.base_url}/action",
                json=action,
                timeout=10
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"Action sent: {action}, Response: {result}")
            return result
        except requests.RequestException as e:
            logger.error(f"Failed to send action {action}: {str(e)}")
            raise
    
    def get_screenshot(self) -> bytes:
        """
        Request a screenshot from the SUT.
        
        Returns:
            Raw screenshot data as bytes
        
        Raises:
            RequestException: If the request fails
        """
        try:
            response = self.session.get(
                f"{self.base_url}/screenshot",
                timeout=15
            )
            response.raise_for_status()
            logger.debug("Screenshot retrieved successfully")
            return response.content
        except requests.RequestException as e:
            logger.error(f"Failed to get screenshot: {str(e)}")
            raise
    
    def launch_game(self, game_path: str, process_id: str = '', startup_wait: int = 15) -> Dict[str, Any]:
        """
        Request the SUT to launch a game.

        Args:
            game_path: Path to the game executable or Steam app ID on the SUT
            process_id: Optional process name to wait for after launch (e.g., 'Launcher', 'Game')
            startup_wait: Maximum seconds to wait for process to appear (default: 15)

        Returns:
            Response from the SUT as a dictionary

        Raises:
            RequestException: If the request fails
        """
        try:
            # Prepare request payload with game metadata
            payload = {
                "path": game_path,
                "process_id": process_id,
                "startup_wait": startup_wait
            }
            
            logger.debug(f"Sending launch request to {self.base_url}/launch with payload: {payload}")

            response = self.session.post(
                f"{self.base_url}/launch",
                json=payload,
                timeout=90
            )
            response.raise_for_status()
            result = response.json()
            
            logger.debug(f"Launch response received: status={result.get('status')}, foreground={result.get('foreground_confirmed')}")
            logger.info(f"Game launch request sent: {game_path}, process_id: {process_id}, startup_wait: {startup_wait}")
            return result
        except requests.RequestException as e:
            logger.error(f"Failed to launch game {game_path}: {str(e)}")
            raise
    
    def close(self):
        """Close the network session."""
        self.session.close()
        logger.info("Network session closed")