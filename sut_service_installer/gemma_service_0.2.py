"""
KATANA SUT Service v0.2 - Enhanced Window Detection
Uses pywinauto for robust wait('visible') and wait('ready') detection.
Falls back to Win32 API if pywinauto unavailable.

Author: SATYAJIT BHUYAN
Email:  satyajit.bhuyan@intel.com
Date:   14th December 2025

v0.2 Changes:
- Added pywinauto for reliable window ready detection
- New wait_for_window_ready_pywinauto() function
- Improved ensure_window_foreground() with configurable timeouts
- Better handling of slow-loading games (RDR2, etc.)
"""

import os
import time
import json
import subprocess
import threading
import psutil
from flask import Flask, request, jsonify, send_file
import pyautogui
from io import BytesIO
import logging
import win32api
import win32con
import win32gui
import ctypes
from ctypes import wintypes
import sys
import winreg
import re
import glob
import win32process

# ============================================================================
# v0.2 NEW: pywinauto for robust window detection
# ============================================================================
try:
    from pywinauto import Application
    from pywinauto.timings import TimeoutError as PywinautoTimeoutError
    PYWINAUTO_AVAILABLE = True
    logger_temp_msg = "pywinauto loaded successfully - using enhanced window detection"
except ImportError:
    PYWINAUTO_AVAILABLE = False
    logger_temp_msg = "pywinauto not installed - using Win32 fallback for window detection"
# ============================================================================

# Configure logging with UTF-8 encoding to support Unicode characters
import sys
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("improved_sut_service.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)  # Use stdout for better encoding support
    ]
)
logger = logging.getLogger(__name__)

# v0.2: Log pywinauto availability
logger.info(f"[v0.2] {logger_temp_msg}")

# Initialize Flask app
app = Flask(__name__)

# Global variables
game_process = None
game_lock = threading.Lock()
current_game_process_name = None

# v0.2: Launch cancellation support
launch_cancel_flag = threading.Event()  # Set this to cancel ongoing launch

# Disable PyAutoGUI failsafe
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.01

# Check for admin privileges
def is_admin():
    """Check if running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# Enable DPI awareness for accurate screen resolution
def set_dpi_awareness():
    """
    Set DPI awareness to get real physical screen resolution.
    Without this, GetSystemMetrics returns scaled coordinates on HiDPI displays.
    """
    try:
        # Try Windows 10+ method first (Per-Monitor DPI Aware v2)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        logger.info("DPI Awareness: Per-Monitor DPI Aware v2")
    except Exception:
        try:
            # Fall back to Windows 8.1+ method
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            logger.info("DPI Awareness: System DPI Aware")
        except Exception:
            try:
                # Fall back to Windows Vista+ method
                ctypes.windll.user32.SetProcessDPIAware()
                logger.info("DPI Awareness: Legacy DPI Aware")
            except Exception as e:
                logger.warning(f"Could not set DPI awareness: {e}")

# Set DPI awareness early, before any resolution queries
set_dpi_awareness()

# Windows API structures for SendInput
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD)
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT)
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION)
    ]

# Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_WHEEL = 0x0800

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# Virtual key codes
VK_CODES = {
    'left': 0x01, 'right': 0x02, 'middle': 0x04,
    'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10,
    'ctrl': 0x11, 'alt': 0x12, 'pause': 0x13, 'caps_lock': 0x14,
    'escape': 0x1B, 'space': 0x20, 'page_up': 0x21, 'page_down': 0x22,
    'end': 0x23, 'home': 0x24, 'left_arrow': 0x25, 'up_arrow': 0x26,
    'right_arrow': 0x27, 'down_arrow': 0x28, 'insert': 0x2D, 'delete': 0x2E,
    'win': 0x5B, 'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
    'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77, 'f9': 0x78,
    'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B
}

class ImprovedInputController:
    """Enhanced input controller using Windows SendInput API."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        # Removed cached resolution to allow dynamic updates
        # self.screen_width = self.user32.GetSystemMetrics(0)
        # self.screen_height = self.user32.GetSystemMetrics(1)

        # Reusable null pointer for dwExtraInfo to reduce allocations
        self._null_ptr = ctypes.cast(ctypes.pointer(wintypes.ULONG(0)), ctypes.POINTER(wintypes.ULONG))

        logger.info(f"Initial screen resolution: {self.screen_width}x{self.screen_height}")

    @property
    def screen_width(self):
        """Get current screen width."""
        return self.user32.GetSystemMetrics(0)

    @property
    def screen_height(self):
        """Get current screen height."""
        return self.user32.GetSystemMetrics(1)

    def _normalize_coordinates(self, x, y):
        """Convert screen coordinates to normalized coordinates (0-65535)."""
        # Fetch current resolution to handle dynamic changes
        width = self.screen_width
        height = self.screen_height
        
        normalized_x = int(x * 65535 / width)
        normalized_y = int(y * 65535 / height)
        return normalized_x, normalized_y

    def move_mouse(self, x, y, smooth=True, duration=0.3):
        """
        Move mouse to absolute position using SendInput.

        Args:
            x, y: Screen coordinates
            smooth: If True, move smoothly; if False, move instantly
            duration: Duration of smooth movement in seconds
        """
        try:
            if smooth and duration > 0:
                # Get current position
                current_x, current_y = win32api.GetCursorPos()

                # Optimize: cap steps at 50 to reduce CPU load (was 100)
                steps = min(50, max(10, int(duration * 60)))

                for i in range(steps + 1):
                    progress = i / steps
                    # Ease in-out cubic
                    if progress < 0.5:
                        eased = 4 * progress * progress * progress
                    else:
                        eased = 1 - pow(-2 * progress + 2, 3) / 2

                    inter_x = int(current_x + (x - current_x) * eased)
                    inter_y = int(current_y + (y - current_y) * eased)

                    self._move_mouse_absolute(inter_x, inter_y)
                    time.sleep(duration / steps)
            else:
                self._move_mouse_absolute(x, y)

            logger.debug(f"Mouse moved to ({x}, {y})")  # Changed to debug to reduce log spam
            return True
        except Exception as e:
            logger.error(f"Mouse move failed: {e}")
            return False

    def _move_mouse_absolute(self, x, y):
        """Move mouse using SendInput with absolute positioning."""
        norm_x, norm_y = self._normalize_coordinates(x, y)

        # Create mouse input structure
        mouse_input = MOUSEINPUT()
        mouse_input.dx = norm_x
        mouse_input.dy = norm_y
        mouse_input.mouseData = 0
        mouse_input.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        mouse_input.time = 0
        mouse_input.dwExtraInfo = self._null_ptr

        # Create INPUT structure
        input_struct = INPUT()
        input_struct.type = INPUT_MOUSE
        input_struct.union.mi = mouse_input

        # Send input
        result = self.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))

        if result == 0:
            # Fallback to win32api (only log on first failure to reduce spam)
            win32api.SetCursorPos((x, y))

    def click_mouse(self, x, y, button='left', move_duration=0.3, click_delay=0.1):
        """
        Click mouse at position using SendInput.

        Args:
            x, y: Screen coordinates
            button: 'left', 'right', or 'middle'
            move_duration: Time to move to position
            click_delay: Delay before clicking
        """
        try:
            # Move to position
            self.move_mouse(x, y, smooth=True, duration=move_duration)

            # Wait before clicking
            if click_delay > 0:
                time.sleep(click_delay)

            # Determine button flags
            if button == 'left':
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            elif button == 'right':
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
            elif button == 'middle':
                down_flag = MOUSEEVENTF_MIDDLEDOWN
                up_flag = MOUSEEVENTF_MIDDLEUP
            else:
                logger.error(f"Invalid button: {button}")
                return False

            # Mouse down
            self._send_mouse_event(down_flag)
            time.sleep(0.05)  # Brief hold

            # Mouse up
            self._send_mouse_event(up_flag)

            logger.info(f"{button.capitalize()} click at ({x}, {y})")
            return True

        except Exception as e:
            logger.error(f"Click failed: {e}")
            return False

    def hold_click(self, x, y, button='left', duration=1.0, move_duration=0.3):
        """
        Hold mouse button at position for specified duration.

        Args:
            x, y: Screen coordinates
            button: 'left', 'right', or 'middle'
            duration: How long to hold the button in seconds
            move_duration: Time to move to position before clicking
        """
        try:
            # Move to position
            self.move_mouse(x, y, smooth=True, duration=move_duration)
            time.sleep(0.1)

            # Determine button flags
            if button == 'left':
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            elif button == 'right':
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
            elif button == 'middle':
                down_flag = MOUSEEVENTF_MIDDLEDOWN
                up_flag = MOUSEEVENTF_MIDDLEUP
            else:
                logger.error(f"Invalid button for hold_click: {button}")
                return False

            # Mouse down
            self._send_mouse_event(down_flag)
            
            # Hold for specified duration
            time.sleep(duration)
            
            # Mouse up
            self._send_mouse_event(up_flag)

            logger.info(f"Held {button} click at ({x}, {y}) for {duration}s")
            return True

        except Exception as e:
            logger.error(f"Hold click failed: {e}")
            return False

    def _send_mouse_event(self, flags):
        """Send a mouse event using SendInput."""
        mouse_input = MOUSEINPUT()
        mouse_input.dx = 0
        mouse_input.dy = 0
        mouse_input.mouseData = 0
        mouse_input.dwFlags = flags
        mouse_input.time = 0
        mouse_input.dwExtraInfo = self._null_ptr

        input_struct = INPUT()
        input_struct.type = INPUT_MOUSE
        input_struct.union.mi = mouse_input

        result = self.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))

        if result == 0:
            # Fallback to win32api
            if flags == MOUSEEVENTF_LEFTDOWN:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
            elif flags == MOUSEEVENTF_LEFTUP:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            elif flags == MOUSEEVENTF_RIGHTDOWN:
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0)
            elif flags == MOUSEEVENTF_RIGHTUP:
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0)

    def press_key(self, key_name):
        """Press and release a key using SendInput."""
        try:
            # Normalize key name
            key_lower = key_name.lower().replace('_', '')

            # Map common variations
            key_map = {
                'esc': 'escape',
                'return': 'enter',
                'up': 'up_arrow',
                'down': 'down_arrow',
                'left': 'left_arrow',
                'right': 'right_arrow',
                'pageup': 'page_up',
                'pagedown': 'page_down',
                'capslock': 'caps_lock'
            }

            key_lower = key_map.get(key_lower, key_lower)

            # Get virtual key code
            if key_lower in VK_CODES:
                vk_code = VK_CODES[key_lower]
            elif len(key_name) == 1:
                # Single character
                vk_code = ord(key_name.upper())
            else:
                logger.warning(f"Unknown key '{key_name}', trying pyautogui fallback")
                try:
                    pyautogui.press(key_name)
                    logger.info(f"Pressed key via pyautogui: {key_name}")
                    return True
                except:
                    logger.error(f"Unknown key and fallback failed: {key_name}")
                    return False

            # Key down
            result1 = self._send_key_event(vk_code, False)
            time.sleep(0.05)

            # Key up
            result2 = self._send_key_event(vk_code, True)

            # Check if SendInput succeeded
            if result1 == 0 or result2 == 0:
                logger.warning(f"SendInput failed for key '{key_name}', using pyautogui fallback")
                try:
                    pyautogui.press(key_name)
                    logger.info(f"Pressed key via pyautogui: {key_name}")
                    return True
                except Exception as e:
                    logger.error(f"Fallback also failed: {e}")
                    return False

            logger.info(f"Pressed key: {key_name} (VK: 0x{vk_code:02X})")
            return True

        except Exception as e:
            logger.error(f"Key press failed: {e}")
            return False

    def hold_key(self, key_name, duration=1.0):
        """
        Hold a key for specified duration.

        Args:
            key_name: Name of key to hold (e.g., 'enter', 'space', 'f5')
            duration: How long to hold the key in seconds
        """
        try:
            # Normalize key name
            key_lower = key_name.lower().replace('_', '')

            # Map common variations
            key_map = {
                'esc': 'escape',
                'return': 'enter',
                'up': 'up_arrow',
                'down': 'down_arrow',
                'left': 'left_arrow',
                'right': 'right_arrow',
                'pageup': 'page_up',
                'pagedown': 'page_down',
                'capslock': 'caps_lock'
            }

            key_lower = key_map.get(key_lower, key_lower)

            # Get VK code
            if key_lower in VK_CODES:
                vk_code = VK_CODES[key_lower]
            elif len(key_lower) == 1 and key_lower.isalnum():
                vk_code = ord(key_lower.upper())
            else:
                logger.error(f"Unknown key for hold: {key_name}")
                return False

            # Key down
            self._send_key_event(vk_code, False)
            
            # Hold for specified duration
            time.sleep(duration)
            
            # Key up
            self._send_key_event(vk_code, True)

            logger.info(f"Held key: {key_name} for {duration}s (VK: 0x{vk_code:02X})")
            return True

        except Exception as e:
            logger.error(f"Hold key failed: {e}")
            return False

    def _send_key_event(self, vk_code, key_up=False):
        """Send a keyboard event using SendInput."""
        # Get hardware scan code for the virtual key
        scan_code = self.user32.MapVirtualKeyW(vk_code, 0)

        kbd_input = KEYBDINPUT()
        kbd_input.wVk = vk_code
        kbd_input.wScan = scan_code
        kbd_input.dwFlags = KEYEVENTF_KEYUP if key_up else 0
        kbd_input.time = 0
        kbd_input.dwExtraInfo = self._null_ptr

        input_struct = INPUT()
        input_struct.type = INPUT_KEYBOARD
        input_struct.union.ki = kbd_input

        result = self.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        return result

    def press_hotkey(self, keys):
        """
        Press multiple keys together (hotkey combination).

        Args:
            keys: List of key names to press together (e.g., ['ctrl', 's'])
        """
        try:
            # Normalize and get VK codes
            vk_codes = []
            for key in keys:
                key_lower = key.lower().replace('_', '')
                key_map = {
                    'esc': 'escape',
                    'return': 'enter',
                    'up': 'up_arrow',
                    'down': 'down_arrow',
                    'left': 'left_arrow',
                    'right': 'right_arrow'
                }
                key_lower = key_map.get(key_lower, key_lower)

                if key_lower in VK_CODES:
                    vk_codes.append(VK_CODES[key_lower])
                elif len(key) == 1:
                    vk_codes.append(ord(key.upper()))
                else:
                    logger.error(f"Unknown key in hotkey: {key}")
                    return False

            # Press all keys down
            for vk_code in vk_codes:
                self._send_key_event(vk_code, False)
                time.sleep(0.01)

            time.sleep(0.05)

            # Release all keys in reverse order
            for vk_code in reversed(vk_codes):
                self._send_key_event(vk_code, True)
                time.sleep(0.01)

            logger.info(f"Pressed hotkey: {'+'.join(keys)}")
            return True

        except Exception as e:
            logger.error(f"Hotkey press failed: {e}")
            return False

    def double_click(self, x, y, button='left', move_duration=0.3):
        """Double-click at position."""
        try:
            # Move to position
            self.move_mouse(x, y, smooth=True, duration=move_duration)
            time.sleep(0.1)

            # Determine button flags
            if button == 'left':
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            elif button == 'right':
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
            else:
                logger.error(f"Invalid button for double-click: {button}")
                return False

            # First click
            self._send_mouse_event(down_flag)
            time.sleep(0.05)
            self._send_mouse_event(up_flag)
            time.sleep(0.05)

            # Second click
            self._send_mouse_event(down_flag)
            time.sleep(0.05)
            self._send_mouse_event(up_flag)

            logger.info(f"Double-clicked {button} at ({x}, {y})")
            return True

        except Exception as e:
            logger.error(f"Double-click failed: {e}")
            return False

    def drag(self, x1, y1, x2, y2, button='left', duration=1.0):
        """
        Drag from one position to another.

        Args:
            x1, y1: Starting coordinates
            x2, y2: Ending coordinates
            button: Mouse button to use
            duration: Duration of drag in seconds
        """
        try:
            # Move to start position
            self.move_mouse(x1, y1, smooth=True, duration=0.3)
            time.sleep(0.1)

            # Determine button flags
            if button == 'left':
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            elif button == 'right':
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
            else:
                logger.error(f"Invalid button for drag: {button}")
                return False

            # Press button down
            self._send_mouse_event(down_flag)
            time.sleep(0.1)

            # Move to end position while holding button
            self.move_mouse(x2, y2, smooth=True, duration=duration)
            time.sleep(0.1)

            # Release button
            self._send_mouse_event(up_flag)

            logger.info(f"Dragged from ({x1}, {y1}) to ({x2}, {y2})")
            return True

        except Exception as e:
            logger.error(f"Drag failed: {e}")
            return False

    def type_text(self, text, char_delay=0.05):
        """Type text character by character."""
        try:
            for char in text:
                if char == '\n':
                    self.press_key('enter')
                elif char == '\t':
                    self.press_key('tab')
                else:
                    # Use pyautogui as fallback for complex characters
                    pyautogui.write(char, interval=0)

                if char_delay > 0:
                    time.sleep(char_delay)

            logger.info(f"Typed text: {text[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Type text failed: {e}")
            return False

    def scroll(self, x, y, clicks, direction='up'):
        """Scroll at position."""
        try:
            # Move to position first
            self.move_mouse(x, y, smooth=False, duration=0)
            time.sleep(0.05)

            # Calculate scroll amount (120 units per click)
            scroll_amount = 120 if direction == 'up' else -120

            # Optimize: send all scroll events without recalculating position
            for _ in range(clicks):
                mouse_input = MOUSEINPUT()
                mouse_input.dx = 0  # Relative scrolling, no position needed
                mouse_input.dy = 0
                mouse_input.mouseData = scroll_amount
                mouse_input.dwFlags = MOUSEEVENTF_WHEEL  # Removed ABSOLUTE flag
                mouse_input.time = 0
                mouse_input.dwExtraInfo = self._null_ptr

                input_struct = INPUT()
                input_struct.type = INPUT_MOUSE
                input_struct.union.mi = mouse_input

                self.user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
                time.sleep(0.02)  # Reduced delay from 0.05 to 0.02

            logger.debug(f"Scrolled {direction} {clicks} times")  # Changed to debug
            return True

        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return False

# Initialize controller
input_controller = ImprovedInputController()

# Process management functions
def find_process_by_name(process_name, exact_only=True):
    """
    Find a running process by its name.
    
    v0.2 FIX: Only accepts EXACT match by default to prevent finding 
    launcher (PlayRDR2.exe) instead of game (RDR2.exe).
    
    Args:
        process_name: Name of process to find (e.g., "RDR2.exe")
        exact_only: If True (default), only exact matches are returned.
                    If False, substring matches are also allowed.
    """
    try:
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                proc_name = proc.info['name']
                proc_exe = os.path.basename(proc.info['exe']) if proc.info['exe'] else None
                
                if exact_only:
                    # EXACT match only (case-insensitive)
                    if (proc_name and proc_name.lower() == process_name.lower()) or \
                       (proc_exe and proc_exe.lower() == process_name.lower()):
                        logger.info(f"[EXACT] Found process: {proc_name} (PID: {proc.info['pid']})")
                        return psutil.Process(proc.info['pid'])
                else:
                    # Partial/substring match (legacy behavior)
                    if (proc_name and process_name.lower() in proc_name.lower()) or \
                       (proc_exe and process_name.lower() in proc_exe.lower()):
                        logger.info(f"[PARTIAL] Found process: {proc_name} (PID: {proc.info['pid']})")
                        return psutil.Process(proc.info['pid'])
                        
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
    except Exception as e:
        logger.error(f"Error searching for process {process_name}: {str(e)}")
    return None

def terminate_process_by_name(process_name):
    """Terminate a process by its name."""
    try:
        processes_terminated = []
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if (proc.info['name'] and process_name.lower() in proc.info['name'].lower()) or \
                   (proc.info['exe'] and process_name.lower() in os.path.basename(proc.info['exe']).lower()):

                    process = psutil.Process(proc.info['pid'])
                    logger.info(f"Terminating process: {proc.info['name']} (PID: {proc.info['pid']})")

                    process.terminate()
                    try:
                        process.wait(timeout=5)
                        processes_terminated.append(proc.info['name'])
                    except psutil.TimeoutExpired:
                        logger.warning(f"Force killing process: {proc.info['name']}")
                        process.kill()
                        processes_terminated.append(proc.info['name'])

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if processes_terminated:
            logger.info(f"Successfully terminated processes: {processes_terminated}")
            return True
        else:
            logger.info(f"No processes found with name: {process_name}")
            return False

    except Exception as e:
        logger.error(f"Error terminating process {process_name}: {str(e)}")
        return False

# ============================================================================
# STEAM LOGIN HELPERS
# ============================================================================

def set_steam_auto_login(username):
    """
    Set the AutoLoginUser registry key to enable auto-login for specified user.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, 
            r"Software\Valve\Steam",
            0, 
            winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "AutoLoginUser", 0, winreg.REG_SZ, username)
        winreg.SetValueEx(key, "RememberPassword", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        logger.info(f"Registry: Set AutoLoginUser to '{username}'")
        return True
    except Exception as e:
        logger.warning(f"Failed to set AutoLoginUser registry: {e}")
        return False


def verify_steam_login(timeout=45):
    """
    Verify that Steam is logged in via registry check (ActiveUser != 0).
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess")
            active_user, _ = winreg.QueryValueEx(key, "ActiveUser")
            winreg.CloseKey(key)
            
            if active_user and active_user != 0:
                logger.info(f"Steam login verified! ActiveUser ID: {active_user}")
                return True, active_user
            else:
                logger.debug("Steam ActiveUser is 0, login in progress...")
                
        except Exception as e:
            logger.debug(f"Waiting for Steam registry... {e}")
        
        time.sleep(2)
    
    logger.warning(f"Steam login verification timed out after {timeout}s")
    return False, None


# Flask routes
@app.route('/login_steam', methods=['POST'])
def login_steam():
    """
    Login to Steam using steam.exe -login command.
    
    Flow:
    1. Check if already logged in (skip if so)
    2. Kill existing Steam processes
    3. Set registry for the desired user
    4. Launch steam.exe with -login credentials
    5. Wait and verify login via registry
    """
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return jsonify({"status": "error", "message": "Username and password required"}), 400

        logger.info(f"===== Steam Login Request: {username} =====")

        # 1. Check if already logged in as this user
        steam_running = find_process_by_name("steam.exe")
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            current_user, _ = winreg.QueryValueEx(key, "AutoLoginUser")
            winreg.CloseKey(key)
            logger.info(f"Current AutoLoginUser: {current_user}")

            if steam_running and current_user.lower() == username.lower():
                verified, user_id = verify_steam_login(timeout=5)
                if verified:
                    logger.info(f"Already logged in as {username}")
                    return jsonify({"status": "success", "message": "Already logged in", "user_id": user_id}), 200
        except Exception as e:
            logger.debug(f"Registry check: {e}")

        # 2. Kill all Steam processes
        logger.info("Killing Steam processes...")
        terminate_process_by_name("steam.exe")
        terminate_process_by_name("steamwebhelper.exe")
        time.sleep(3)

        # 3. Set registry for auto-login
        set_steam_auto_login(username)

        # 4. Get Steam path
        steam_path = get_steam_install_path()
        if not steam_path:
            return jsonify({"status": "error", "message": "Steam not found"}), 500
        
        steam_exe = os.path.join(steam_path, "steam.exe")
        if not os.path.exists(steam_exe):
            return jsonify({"status": "error", "message": f"steam.exe not found: {steam_exe}"}), 500

        # 5. Launch Steam with -login credentials
        cmd = [steam_exe, "-login", username, password]
        logger.info(f"Launching: steam.exe -login {username} ********")
        subprocess.Popen(cmd)

        # 6. Wait for login verification
        logger.info("Waiting for Steam login...")
        verified, user_id = verify_steam_login(timeout=60)

        if verified:
            logger.info(f"===== Steam Login SUCCESS: {username} (ID: {user_id}) =====")
            return jsonify({
                "status": "success",
                "message": "Steam login successful",
                "user_id": user_id
            }), 200
        else:
            logger.warning("Steam login verification failed - check SUT")
            return jsonify({
                "status": "warning",
                "message": "Steam launched but login unverified"
            }), 200

    except Exception as e:
        logger.error(f"Steam login failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    """Enhanced status endpoint."""
    return jsonify({
        "status": "running",
        "version": "3.1-optimized",
        "input_method": "SendInput",
        "screen_width": input_controller.screen_width,
        "screen_height": input_controller.screen_height,
        "admin_privileges": is_admin(),
        "capabilities": [
            "sendinput_clicks", "sendinput_mouse", "smooth_movement",
            "keyboard_input", "hotkey_support", "double_click", "drag_drop",
            "scroll", "process_management", "text_input"
        ]
    })


@app.route('/check_process', methods=['POST'])
def check_process():
    """Check if a process is running by name."""
    try:
        data = request.json
        process_name = data.get('process_name', '')
        
        if not process_name:
            return jsonify({"status": "error", "message": "process_name required"}), 400
        
        proc = find_process_by_name(process_name)
        
        if proc:
            return jsonify({
                "status": "success",
                "running": True,
                "pid": proc.pid,
                "name": proc.name()
            })
        else:
            return jsonify({
                "status": "success",
                "running": False
            })
            
    except Exception as e:
        logger.error(f"Check process error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/kill_process', methods=['POST'])
def kill_process():
    """Kill a process by name."""
    try:
        data = request.json
        process_name = data.get('process_name', '')
        
        if not process_name:
            return jsonify({"status": "error", "message": "process_name required"}), 400
        
        killed = terminate_process_by_name(process_name)
        
        return jsonify({
            "status": "success",
            "killed": killed,
            "process_name": process_name
        })
            
    except Exception as e:
        logger.error(f"Kill process error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/screenshot', methods=['GET'])
def screenshot():
    """Capture and return a screenshot."""
    try:
        monitor = request.args.get('monitor', '0')
        region = request.args.get('region')

        if region:
            x, y, width, height = map(int, region.split(','))
            screenshot = pyautogui.screenshot(region=(x, y, width, height))
        else:
            screenshot = pyautogui.screenshot()

        img_buffer = BytesIO()
        screenshot.save(img_buffer, format='PNG')
        img_buffer.seek(0)

        logger.info(f"Screenshot captured (region: {region})")
        return send_file(img_buffer, mimetype='image/png')
    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        return jsonify({"status": "error", "error": str(e)}), 500

def get_steam_install_path():
    """Get Steam installation path from Windows Registry."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam")
        path, _ = winreg.QueryValueEx(key, "SteamPath")
        winreg.CloseKey(key)
        return path
    except:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Valve\\Steam")
            path, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            return path
        except:
            return None

def resolve_steam_app_path(app_id, target_process_name=''):
    """Resolve Steam App ID to executable path by parsing manifest files."""
    steam_path = get_steam_install_path()
    if not steam_path:
        return None, "Steam installation not found in registry"

    logger.info(f"Steam path found: {steam_path}")
    
    # Library folders config
    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if not os.path.exists(vdf_path):
        # Fallback to single library
        libraries = [steam_path]
    else:
        libraries = []
        try:
            with open(vdf_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Regex to find "path" "..." entries
            matches = re.findall(r'"path"\s+"([^"]+)"', content)
            if matches:
                libraries.extend(matches)
                # Unescape double backslashes
                libraries = [lib.replace('\\\\', '\\') for lib in libraries]
            else:
                 libraries = [steam_path]
        except Exception as e:
            logger.warning(f"Failed to parse libraryfolders.vdf: {e}")
            libraries = [steam_path]

    logger.info(f"Checking Steam libraries: {libraries}")

    # Search for app manifest
    manifest_file = f"appmanifest_{app_id}.acf"
    found_manifest = None
    game_library = None
    
    for lib in libraries:
        manifest_path = os.path.join(lib, "steamapps", manifest_file)
        if os.path.exists(manifest_path):
            found_manifest = manifest_path
            game_library = lib
            break
            
    if not found_manifest:
        return None, f"App ID {app_id} not installed (manifest not found)"

    # Parse manifest for installdir
    install_dir_name = None
    try:
        with open(found_manifest, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r'"installdir"\s+"([^"]+)"', content)
        if match:
            install_dir_name = match.group(1)
    except Exception as e:
        return None, f"Failed to parse manifest: {e}"
        
    if not install_dir_name:
        return None, "Could not find 'installdir' in manifest"
        
    full_game_path = os.path.join(game_library, "steamapps", "common", install_dir_name)
    logger.info(f"Game folder resolved: {full_game_path}")
    
    if not os.path.exists(full_game_path):
         return None, f"Game folder does not exist: {full_game_path}"

    # Find executable
    candidates = []
    
    # Strategy 1: Target process name
    if target_process_name:
        exe_path = os.path.join(full_game_path, f"{target_process_name}.exe")
        if os.path.exists(exe_path):
            return exe_path, None
        
        # Search recursively for target process name
        for root, dirs, files in os.walk(full_game_path):
            if f"{target_process_name}.exe" in files:
                return os.path.join(root, f"{target_process_name}.exe"), None

    # Strategy 2: Folder name matcher
    exe_path = os.path.join(full_game_path, f"{install_dir_name}.exe")
    if os.path.exists(exe_path):
        return exe_path, None

    # Strategy 3: Find largest .exe in the folder
    exe_files = []
    for root, dirs, files in os.walk(full_game_path):
        for file in files:
            if file.lower().endswith(".exe"):
                path = os.path.join(root, file)
                size = os.path.getsize(path)
                exe_files.append((path, size))
    
    if exe_files:
        exe_files.sort(key=lambda x: x[1], reverse=True)
        best_match = exe_files[0][0]
        logger.info(f"Selected largest executable: {best_match}")
        return best_match, None
    
    return None, "No executable found in game directory"

# ============================================================================
# v0.2 NEW: Enhanced Window Detection with pywinauto
# ============================================================================

def wait_for_window_ready_pywinauto(pid, process_name=None, visible_timeout=60, ready_timeout=30):
    """
    v0.2 NEW: Wait for window to be visible AND ready using pywinauto.
    
    'ready' means the window's message queue is idle (fully loaded!).
    This is much more reliable than just checking if window exists.
    
    Args:
        pid: Process ID to wait for
        process_name: Optional process name for fallback connection
        visible_timeout: Max seconds to wait for window to become visible
        ready_timeout: Max seconds to wait for window to become ready/idle
    
    Returns:
        tuple: (success: bool, window_handle: int or None, window_title: str or None)
    """
    if not PYWINAUTO_AVAILABLE:
        logger.debug("pywinauto not available, skipping wait_for_window_ready_pywinauto")
        return False, None, None
    
    logger.info(f"[v0.2] Waiting for window ready state (PID: {pid})")
    
    try:
        # Connect to the process using pywinauto
        # Try 'uia' backend first (works with modern apps), fall back to 'win32'
        app = None
        for backend in ['uia', 'win32']:
            try:
                logger.debug(f"Trying pywinauto backend: {backend}")
                app = Application(backend=backend).connect(process=pid, timeout=10)
                logger.debug(f"Connected with backend: {backend}")
                break
            except Exception as e:
                logger.debug(f"Backend {backend} failed: {e}")
                continue
        
        if not app:
            logger.warning(f"Could not connect pywinauto to PID {pid}")
            return False, None, None
        
        # Get the top-level window
        try:
            main_window = app.top_window()
            window_title = main_window.window_text()
            logger.debug(f"Found top window: '{window_title}'")
        except Exception as e:
            logger.warning(f"Could not get top window: {e}")
            return False, None, None
        
        # Phase 1: Wait for window to be VISIBLE
        logger.info(f"[v0.2] Phase 1: Waiting up to {visible_timeout}s for window to be visible...")
        try:
            # v0.2 OPTIMIZED: Added retry_interval=2.0 to reduce CPU polling frequency
            main_window.wait('visible', timeout=visible_timeout, retry_interval=2.0)
            logger.info(f"[v0.2] [OK] Window is visible: '{window_title}'")
        except PywinautoTimeoutError:
            logger.warning(f"[v0.2] Window not visible after {visible_timeout}s")
            return False, None, window_title
        
        # Phase 2: Wait for window to be READY (message queue idle)
        # This is the KEY improvement - waits until the app is fully loaded
        logger.info(f"[v0.2] Phase 2: Waiting up to {ready_timeout}s for window to be ready/idle...")
        try:
            # v0.2 OPTIMIZED: Added retry_interval=2.0 to reduce CPU polling frequency
            main_window.wait('ready', timeout=ready_timeout, retry_interval=2.0)
            logger.info(f"[v0.2] [OK] Window is ready (message queue idle): '{window_title}'")
        except PywinautoTimeoutError:
            logger.warning(f"[v0.2] Window not ready after {ready_timeout}s (may still work)")
            # Continue anyway - window is at least visible
        
        # Get window handle for foreground operations
        try:
            hwnd = main_window.handle
            logger.info(f"[v0.2] Window ready! HWND={hwnd}, Title='{window_title}'")
            return True, hwnd, window_title
        except Exception as e:
            logger.warning(f"Could not get window handle: {e}")
            return True, None, window_title
            
    except Exception as e:
        logger.error(f"[v0.2] wait_for_window_ready_pywinauto failed: {e}")
        return False, None, None


def bring_to_foreground_pywinauto(pid):
    """
    v0.2 NEW: Use pywinauto's set_focus() which is more reliable than Win32 APIs.
    
    Returns:
        bool: True if successfully brought to foreground
    """
    if not PYWINAUTO_AVAILABLE:
        return False
    
    try:
        for backend in ['uia', 'win32']:
            try:
                app = Application(backend=backend).connect(process=pid, timeout=5)
                main_window = app.top_window()
                
                # pywinauto's set_focus() handles all the Win32 complexity internally
                main_window.set_focus()
                time.sleep(0.3)
                
                # Verify
                if main_window.has_focus():
                    logger.info(f"[v0.2] [OK] pywinauto.set_focus() succeeded")
                    return True
                    
            except Exception as e:
                logger.debug(f"pywinauto set_focus with {backend} failed: {e}")
                continue
                
        return False
        
    except Exception as e:
        logger.debug(f"bring_to_foreground_pywinauto failed: {e}")
        return False


def ensure_window_foreground_v2(pid, timeout=5, use_pywinauto=True):
    """
    v0.2 IMPROVED: Ensure window is in foreground using best available method.
    
    Tries pywinauto first (more reliable), falls back to Win32 API.
    
    Args:
        pid: Process ID
        timeout: Timeout for Win32 fallback method
        use_pywinauto: If True, try pywinauto first
    
    Returns:
        bool: True if window is confirmed in foreground
    """
    # Try pywinauto first if available
    if use_pywinauto and PYWINAUTO_AVAILABLE:
        logger.debug(f"[v0.2] Trying pywinauto set_focus for PID {pid}")
        if bring_to_foreground_pywinauto(pid):
            return True
        logger.debug("[v0.2] pywinauto failed, falling back to Win32")
    
    # Fallback to original Win32 method
    return ensure_window_foreground(pid, timeout)


def ensure_window_foreground(pid, timeout=5):
    """Original Win32-based foreground method (used as fallback in v0.2)."""
    
    logger.debug(f"ensure_window_foreground called for PID {pid} with timeout={timeout}s")
    
    # Callback to find windows for PID
    def callback(hwnd, windows):
        try:
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid:
                # Basic visibility check, can be refined based on window style
                if win32gui.IsWindowVisible(hwnd):
                    # Check if it has a title (usually main windows do)
                    title = win32gui.GetWindowText(hwnd)
                    if title:
                        windows.append((hwnd, title))
        except:
            pass
        return True

    start_time = time.time()
    attempt = 0
    while time.time() - start_time < timeout:
        attempt += 1
        windows = []
        try:
            win32gui.EnumWindows(callback, windows)
        except Exception as e:
            logger.warning(f"Window enumeration failed: {e}")
            
        if windows:
            # Use first window with a title found
            target_hwnd, window_title = windows[0]
            logger.debug(f"Attempt {attempt}: Found {len(windows)} window(s) for PID {pid}. Target: HWND={target_hwnd}, Title='{window_title}'")
            
            # Helper to try forcing foreground
            current_tid = win32api.GetCurrentThreadId()
            target_tid, _ = win32process.GetWindowThreadProcessId(target_hwnd)
            
            try:
                # 0. "Alt" key trick to bypass foreground lock
                # Pressing Alt (VK_MENU = 0x12) tells Windows user is active
                # This is a known workaround for "Access is denied" on SetForegroundWindow
                logger.debug(f"Sending Alt key trick to enable foreground switch")
                alt_input = INPUT()
                alt_input.type = INPUT_KEYBOARD
                alt_input.union.ki.wVk = 0x12 # VK_MENU (Alt)
                alt_input.union.ki.wScan = 0
                alt_input.union.ki.dwFlags = 0
                alt_input.union.ki.time = 0
                alt_input.union.ki.dwExtraInfo = ctypes.cast(ctypes.pointer(wintypes.ULONG(0)), ctypes.POINTER(wintypes.ULONG))
                
                ctypes.windll.user32.SendInput(1, ctypes.byref(alt_input), ctypes.sizeof(INPUT))
                
                # Release Alt
                alt_input.union.ki.dwFlags = KEYEVENTF_KEYUP
                ctypes.windll.user32.SendInput(1, ctypes.byref(alt_input), ctypes.sizeof(INPUT))
                
                # 1. Allow this process to set foreground (magic constant ASFW_ANY = -1)
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
                
                # 2. Attach input processing mechanism to target thread
                attached = False
                if current_tid != target_tid:
                    logger.debug(f"Attaching thread {current_tid} to target thread {target_tid}")
                    attached = win32process.AttachThreadInput(current_tid, target_tid, True)
                
                # 3. Restore and Show
                if win32gui.IsIconic(target_hwnd):
                    logger.debug(f"Window is minimized, restoring...")
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                else:
                    win32gui.ShowWindow(target_hwnd, win32con.SW_SHOW)
                
                # 4. Bring to front
                logger.debug(f"Calling BringWindowToTop and SetForegroundWindow for HWND {target_hwnd}")
                win32gui.BringWindowToTop(target_hwnd)
                win32gui.SetForegroundWindow(target_hwnd)
                
                # 5. Detach
                if attached:
                    logger.debug(f"Detaching thread input")
                    win32process.AttachThreadInput(current_tid, target_tid, False)
                
                # Verify
                foreground_hwnd = win32gui.GetForegroundWindow()
                if foreground_hwnd == target_hwnd:
                    logger.info(f"Successfully forced window {target_hwnd} to foreground.")
                    return True
                else:
                    logger.debug(f"Window {target_hwnd} brought to top but GetForegroundWindow is {foreground_hwnd}")
                    # sometimes main window is wrapper, but child took focus. Accept if PID matches?
                    _, fg_pid = win32process.GetWindowThreadProcessId(foreground_hwnd)
                    if fg_pid == pid:
                        logger.info(f"Foreground window HWND differs but PID matches ({pid}). Accepting.")
                        return True

            except Exception as e:
                logger.warning(f"Failed to force foreground: {e}")
                # Ensure detach happens even on error
                try:
                    if 'attached' in locals() and attached:
                        win32process.AttachThreadInput(current_tid, target_tid, False)
                except:
                    pass
        else:
            logger.debug(f"Attempt {attempt}: No visible windows found for PID {pid}")

        time.sleep(0.5)
    
    logger.debug(f"ensure_window_foreground timed out after {timeout}s for PID {pid}")
    return False

# ============================================================================
# v0.2: Launch Cancellation Support
# ============================================================================

@app.route('/cancel_launch', methods=['POST'])
def cancel_launch():
    """
    v0.2 NEW: Cancel an ongoing launch operation.
    Call this when the user presses Stop in the GUI.
    """
    global launch_cancel_flag
    logger.info("[v0.2] Received cancel_launch request - setting cancel flag")
    launch_cancel_flag.set()
    return jsonify({"status": "success", "message": "Launch cancellation requested"})

# ============================================================================

@app.route('/launch', methods=['POST'])
def launch_game():
    """Launch a game with process tracking. Supports both exe paths and Steam app IDs."""
    global game_process, current_game_process_name, launch_cancel_flag

    # v0.2: Clear cancel flag at start of new launch
    launch_cancel_flag.clear()
    
    try:
        data = request.json
        game_path = data.get('path', '')
        process_id = data.get('process_id', '')

        # Validate game_path is provided (before conversion)
        if not game_path:
            logger.error("No game path provided")
            return jsonify({"status": "error", "error": "Game path is required"}), 400

        # Convert game_path to string
        game_path = str(game_path)
        is_steam_id = False
        
        # Check if it's a numeric Steam App ID
        if game_path.isdigit():
            is_steam_id = True
            logger.info(f"Detected Steam App ID: {game_path}")
        elif game_path.startswith('steam://'):
            # Legacy handling, try to extract ID
            match = re.search(r'run/(\d+)', game_path) or re.search(r'rungameid/(\d+)', game_path)
            if match:
                game_path = match.group(1)
                is_steam_id = True
                logger.info(f"Extracted Steam App ID from URL: {game_path}")

        # Resolve Steam App ID to Executable
        steam_app_id = None  # Track the original app ID for steam:// launch
        if is_steam_id:
            steam_app_id = game_path  # Store original Steam App ID
            logger.info(f"Resolving Steam App ID: {game_path}")
            resolved_path, error = resolve_steam_app_path(game_path, process_id)
            if resolved_path:
                logger.info(f"Resolved Steam ID {game_path} to: {resolved_path}")
                game_path = resolved_path
                # If process_id wasn't provided, try to guess it from the exe name
                if not process_id:
                     new_process_name = os.path.splitext(os.path.basename(resolved_path))[0]
                else:
                     new_process_name = process_id
            else:
                 logger.error(f"Failed to resolve Steam ID: {error}")
                 return jsonify({"status": "error", "error": f"Failed to resolve Steam ID: {error}"}), 404
        else:
             if not os.path.exists(game_path):
                 logger.error(f"Game path not found: {game_path}")
                 return jsonify({"status": "error", "error": "Game executable not found"}), 404
             # For direct EXE, use process_id if provided, otherwise extract from path
             new_process_name = process_id if process_id else os.path.splitext(os.path.basename(game_path))[0]
        
        with game_lock:
            # Terminate existing game if running (using GLOBAL current_game_process_name)
            if current_game_process_name:
                logger.info(f"Terminating existing game: {current_game_process_name}")
                terminate_process_by_name(current_game_process_name)
                # Also try to terminate previous game_process handle
                if game_process and game_process.poll() is None:
                     try:
                        game_process.terminate()
                        game_process.wait(timeout=2)
                     except:
                        pass
                current_game_process_name = None

            # Set the new global process name
            current_game_process_name = new_process_name

            # Launch game - Use steam:// protocol for Steam games to ensure Steam starts
            if steam_app_id:
                # Launch via Steam protocol - this automatically starts Steam if not running
                steam_url = f"steam://rungameid/{steam_app_id}"
                logger.info(f"Launching game via Steam protocol: {steam_url}")
                os.startfile(steam_url)
                game_process = None  # No subprocess handle for steam:// launch
            else:
                # Direct exe launch for non-Steam games
                logger.info(f"Launching game directly: {game_path}")
                game_dir = os.path.dirname(game_path)
                try:
                    game_process = subprocess.Popen(game_path, cwd=game_dir)
                except Exception as e:
                    logger.warning(f"Failed to launch with cwd, trying direct: {e}")
                    game_process = subprocess.Popen(game_path)
                
            # Log launch status
            if game_process:
                logger.info(f"Subprocess started with PID: {game_process.pid}")
            else:
                logger.info("Game launched via Steam protocol (no direct PID)")

            time.sleep(3) # Initial wait for process spawn

            if game_process:
                subprocess_status = "running" if game_process.poll() is None else "exited"
                logger.info(f"Subprocess status after 3 seconds: {subprocess_status}")
            
            # If subprocess exited quickly (launcher wrapper), we need to find the actual game process
            # Give the actual game process time to start and show window
            max_wait_time = 60  # Increased wait time to ensure window logic has time
            wait_interval = 1
            actual_process = None
            foreground_confirmed = False
            
            logger.info(f"Waiting up to {max_wait_time}s for process '{current_game_process_name}' to appear...")

            # Phase 1: Detect Process (v0.2 OPTIMIZED: Uses Event.wait for efficient blocking)
            start_wait = time.time()
            cancelled = False
            while time.time() - start_wait < max_wait_time:
                # Check for process FIRST (before waiting)
                actual_process = find_process_by_name(current_game_process_name)
                if actual_process:
                    logger.info(f"Process found: {actual_process.name()} (PID: {actual_process.pid})")
                    break
                
                # v0.2 OPTIMIZED: Use Event.wait() instead of sleep
                # This blocks the thread efficiently (near-zero CPU) while also 
                # checking for cancellation. Returns True if flag was set.
                if launch_cancel_flag.wait(timeout=3):  # Poll every 3s instead of 1s
                    logger.info("[v0.2] Launch cancelled during process detection")
                    cancelled = True
                    break
            
            # v0.2: Return immediately if cancelled
            if cancelled:
                return jsonify({"status": "cancelled", "message": "Launch cancelled by user"})
            
            if actual_process:
                # ============================================================
                # v0.2 IMPROVED: Use pywinauto for reliable window detection
                # ============================================================
                
                # Phase 2a: Wait for window to be VISIBLE and READY using pywinauto
                # This is the KEY improvement - waits until app is fully loaded
                logger.info("[v0.2] Starting enhanced foreground detection...")
                
                window_ready, hwnd, window_title = wait_for_window_ready_pywinauto(
                    actual_process.pid,
                    process_name=current_game_process_name,
                    visible_timeout=120,  # Wait up to 120s for window visible (slow games like RDR2)
                    ready_timeout=30     # Wait up to 30s for window ready/idle
                )
                
                if window_ready:
                    logger.info(f"[v0.2] Window is ready, bringing to foreground...")
                    # Use v0.2 foreground method (tries pywinauto first, then Win32)
                    foreground_confirmed = ensure_window_foreground_v2(actual_process.pid, timeout=5)
                else:
                    logger.warning("[v0.2] pywinauto window detection failed, using legacy method")
                    # Fallback to original method if pywinauto failed
                    foreground_confirmed = ensure_window_foreground(actual_process.pid, timeout=5)
                
                # Retry with longer waits for slow-loading games (if still not confirmed)
                if not foreground_confirmed:
                    max_retries = 5
                    retry_interval = 10  # seconds between retries
                    for attempt in range(1, max_retries + 1):
                        logger.warning(f"Foreground attempt failed. Retry {attempt}/{max_retries} in {retry_interval}s...")
                        
                        # v0.2 OPTIMIZED: Use Event.wait() for efficient blocking
                        # Blocks thread (near-zero CPU) while waiting, automatically wakes if cancelled
                        if launch_cancel_flag.wait(timeout=retry_interval):
                            logger.info("[v0.2] Launch cancelled during retry wait")
                            return jsonify({"status": "cancelled", "message": "Launch cancelled by user"})
                        
                        # Re-check if process still exists (may have new PID after launcher)
                        actual_process = find_process_by_name(current_game_process_name)
                        if actual_process:
                            logger.info(f"Retry {attempt}: Process found: {actual_process.name()} (PID: {actual_process.pid})")
                            
                            # v0.2: Try pywinauto wait first on retry
                            window_ready, hwnd, _ = wait_for_window_ready_pywinauto(
                                actual_process.pid,
                                visible_timeout=15,  # Shorter timeout on retry
                                ready_timeout=10
                            )
                            
                            # Then try to bring to foreground
                            foreground_confirmed = ensure_window_foreground_v2(actual_process.pid, timeout=5)
                            if foreground_confirmed:
                                logger.info(f"Retry {attempt}: Successfully brought to foreground!")
                                break
                        else:
                            logger.warning(f"Retry {attempt}: Process '{current_game_process_name}' no longer found")
                
                response_data = {
                    "status": "success" if foreground_confirmed else "warning",
                    "subprocess_pid": game_process.pid if game_process else None,
                    "subprocess_status": subprocess_status if game_process else "steam_protocol",
                    "resolved_path": game_path if is_steam_id else None,
                    "launch_method": "steam" if is_steam_id else "direct_exe",
                    "game_process_pid": actual_process.pid,
                    "game_process_name": actual_process.name(),
                    "game_process_status": actual_process.status(),
                    "foreground_confirmed": foreground_confirmed,
                    "pywinauto_available": PYWINAUTO_AVAILABLE,  # v0.2: Report if enhanced detection was used
                    "window_ready_detected": window_ready if 'window_ready' in dir() else False
                }
                
                if foreground_confirmed:
                     logger.info(f"[OK] Launch Complete: {actual_process.name()} is running and in foreground.")
                else:
                     logger.warning(f"[WARN] Launch Warning: Process {actual_process.pid} exists but could not confirm foreground status.")
                     response_data["warning"] = "Process launched but window not in foreground (timeout)"

            else:
                logger.warning(f"Game process '{current_game_process_name}' not found within {max_wait_time} seconds")
                response_data = {
                    "status": "warning",
                    "warning": f"Game process '{current_game_process_name}' not detected, but launch command executed",
                    "subprocess_pid": game_process.pid if game_process else None,
                    "subprocess_status": subprocess_status if game_process else "steam_protocol",
                    "resolved_path": game_path if is_steam_id else None,
                    "launch_method": "steam" if is_steam_id else "direct_exe"
                }
        
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error launching game: {str(e)}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/action', methods=['POST'])
def perform_action():
    """Enhanced action handler using SendInput."""
    try:
        data = request.json
        action_type = data.get('type', '').lower()

        logger.info(f"Executing action: {action_type}")

        if action_type == 'click':
            x = data.get('x', 0)
            y = data.get('y', 0)
            button = data.get('button', 'left').lower()
            move_duration = data.get('move_duration', 0.3)
            click_delay = data.get('click_delay', 0.1)

            success = input_controller.click_mouse(x, y, button, move_duration, click_delay)

            if success:
                return jsonify({
                    "status": "success",
                    "action": "click",
                    "coordinates": [x, y],
                    "button": button
                })
            else:
                return jsonify({"status": "error", "error": "Click failed"}), 500

        elif action_type in ['key', 'keypress']:
            key = data.get('key', '')
            success = input_controller.press_key(key)

            if success:
                return jsonify({"status": "success", "action": "keypress", "key": key})
            else:
                return jsonify({"status": "error", "error": "Key press failed"}), 500

        elif action_type in ['text', 'type', 'input']:
            text = data.get('text', '')
            char_delay = data.get('char_delay', 0.05)
            success = input_controller.type_text(text, char_delay)

            if success:
                return jsonify({"status": "success", "action": "text_input", "text_length": len(text)})
            else:
                return jsonify({"status": "error", "error": "Text input failed"}), 500

        elif action_type == 'scroll':
            x = data.get('x', 0)
            y = data.get('y', 0)
            direction = data.get('direction', 'up').lower()
            clicks = data.get('clicks', 3)

            success = input_controller.scroll(x, y, clicks, direction)

            if success:
                return jsonify({
                    "status": "success",
                    "action": "scroll",
                    "direction": direction,
                    "clicks": clicks
                })
            else:
                return jsonify({"status": "error", "error": "Scroll failed"}), 500

        elif action_type == 'hotkey':
            keys = data.get('keys', [])
            if not keys:
                return jsonify({"status": "error", "error": "No keys provided"}), 400

            success = input_controller.press_hotkey(keys)

            if success:
                return jsonify({
                    "status": "success",
                    "action": "hotkey",
                    "keys": keys
                })
            else:
                return jsonify({"status": "error", "error": "Hotkey failed"}), 500

        elif action_type == 'hold_key':
            # Hold a key for specified duration
            key = data.get('key', '')
            duration = data.get('duration', 1.0)
            
            if not key:
                return jsonify({"status": "error", "error": "No key provided"}), 400
            
            success = input_controller.hold_key(key, duration)
            
            if success:
                return jsonify({
                    "status": "success",
                    "action": "hold_key",
                    "key": key,
                    "duration": duration
                })
            else:
                return jsonify({"status": "error", "error": "Hold key failed"}), 500

        elif action_type == 'hold_click':
            # Hold mouse button at position for specified duration
            x = data.get('x', 0)
            y = data.get('y', 0)
            button = data.get('button', 'left').lower()
            duration = data.get('duration', 1.0)
            move_duration = data.get('move_duration', 0.3)
            
            success = input_controller.hold_click(x, y, button, duration, move_duration)
            
            if success:
                return jsonify({
                    "status": "success",
                    "action": "hold_click",
                    "coordinates": [x, y],
                    "button": button,
                    "duration": duration
                })
            else:
                return jsonify({"status": "error", "error": "Hold click failed"}), 500

        elif action_type == 'double_click':
            x = data.get('x', 0)
            y = data.get('y', 0)
            button = data.get('button', 'left').lower()
            move_duration = data.get('move_duration', 0.3)

            success = input_controller.double_click(x, y, button, move_duration)

            if success:
                return jsonify({
                    "status": "success",
                    "action": "double_click",
                    "coordinates": [x, y],
                    "button": button
                })
            else:
                return jsonify({"status": "error", "error": "Double-click failed"}), 500

        elif action_type == 'drag':
            x1 = data.get('x1', 0)
            y1 = data.get('y1', 0)
            x2 = data.get('x2', 0)
            y2 = data.get('y2', 0)
            button = data.get('button', 'left').lower()
            duration = data.get('duration', 1.0)

            success = input_controller.drag(x1, y1, x2, y2, button, duration)

            if success:
                return jsonify({
                    "status": "success",
                    "action": "drag",
                    "start": [x1, y1],
                    "end": [x2, y2]
                })
            else:
                return jsonify({"status": "error", "error": "Drag failed"}), 500

        elif action_type == 'wait':
            duration = data.get('duration', 1)
            logger.info(f"Waiting for {duration} seconds")
            time.sleep(duration)
            return jsonify({"status": "success", "action": "wait", "duration": duration})

        elif action_type == 'terminate_game':
            with game_lock:
                terminated = False

                if current_game_process_name:
                    if terminate_process_by_name(current_game_process_name):
                        terminated = True

                if game_process and game_process.poll() is None:
                    game_process.terminate()
                    try:
                        game_process.wait(timeout=5)
                        terminated = True
                    except subprocess.TimeoutExpired:
                        game_process.kill()
                        terminated = True

                message = "Game terminated" if terminated else "No game running"
                return jsonify({
                    "status": "success",
                    "action": "terminate_game",
                    "message": message
                })

        else:
            return jsonify({"status": "error", "error": f"Unknown action: {action_type}"}), 400

    except Exception as e:
        logger.error(f"Error performing action: {str(e)}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    try:
        health_status = {
            "service": "running",
            "version": "3.1-optimized",
            "input_method": "SendInput + win32api fallback",
            "admin_privileges": is_admin(),
            "screen_resolution": f"{input_controller.screen_width}x{input_controller.screen_height}"
        }

        if current_game_process_name:
            game_proc = find_process_by_name(current_game_process_name)
            health_status["game_process"] = "running" if game_proc else "not_found"
        else:
            health_status["game_process"] = "none"

        return jsonify({"status": "success", "health": health_status})

    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='KATANA SUT Service v0.2')
    parser.add_argument('--port', type=int, default=8080, help='Port to run on')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()

    # KATANA ASCII Art Banner
    print("")
    print("=" * 70)
    print("""
    ██╗  ██╗ █████╗ ████████╗ █████╗ ███╗   ██╗ █████╗ 
    ██║ ██╔╝██╔══██╗╚══██╔══╝██╔══██╗████╗  ██║██╔══██╗
    █████╔╝ ███████║   ██║   ███████║██╔██╗ ██║███████║
    ██╔═██╗ ██╔══██║   ██║   ██╔══██║██║╚██╗██║██╔══██║
    ██║  ██╗██║  ██║   ██║   ██║  ██║██║ ╚████║██║  ██║
    ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝
                                                   
                 S U T   S E R V I C E   v 0 . 2
    """)
    print("=" * 70)
    print("    Author: SATYAJIT BHUYAN | satyajit.bhuyan@intel.com")
    print("    Date:   14th December 2025")
    print("=" * 70)
    
    logger.info("KATANA SUT Service v0.2 - Enhanced Window Detection")
    logger.info("=" * 70)
    logger.info(f"Host: {args.host}:{args.port}")
    logger.info(f"Admin: {'YES' if is_admin() else 'NO (run as admin for best results)'}")
    logger.info(f"Screen: {input_controller.screen_width}x{input_controller.screen_height}")
    logger.info(f"pywinauto: {'ENABLED' if PYWINAUTO_AVAILABLE else 'DISABLED (install for enhanced detection)'}")
    logger.info("")
    
    logger.info("[v0.2 ENHANCEMENTS]")
    logger.info("  * pywinauto wait('visible')  - waits for window to appear")
    logger.info("  * pywinauto wait('ready')    - waits for app fully loaded")
    logger.info("  * pywinauto set_focus()      - reliable foreground switch")
    logger.info("  * Exact process matching     - finds RDR2.exe not PlayRDR2.exe")
    logger.info("  * 120s visible timeout       - for slow games (RDR2, etc.)")
    logger.info("  * 5 retries x 10s intervals  - robust retry mechanism")
    logger.info("  * Win32 fallback             - works without pywinauto")
    logger.info("")
    
    logger.info("[v0.2 OPTIMIZATIONS]")
    logger.info("  * Event.wait() blocking      - near-zero CPU during waits")
    logger.info("  * 3s process poll interval   - reduced from 1s")
    logger.info("  * 2s pywinauto poll interval - reduced polling overhead")
    logger.info("  * /cancel_launch endpoint    - instant stop support")
    logger.info("")
    
    logger.info("[SUPPORTED ACTIONS]")
    logger.info("  + click        - left/right/middle mouse clicks")
    logger.info("  + double_click - double-click at position")
    logger.info("  + hold_click   - click and hold for duration")
    logger.info("  + drag         - drag from point A to B")
    logger.info("  + scroll       - mouse wheel scrolling")
    logger.info("  + key          - single key press")
    logger.info("  + hold_key     - press and hold key for duration")
    logger.info("  + hotkey       - key combinations (Ctrl+S, etc.)")
    logger.info("  + text         - type text strings")
    logger.info("  + launch       - launch games with foreground detection")
    logger.info("  + kill_process - terminate processes by name")
    logger.info("  + login_steam  - Steam auto-login")
    logger.info("=" * 70)

    if not is_admin():
        logger.warning("WARNING: Not running with administrator privileges!")
        logger.warning("Some games may block input. Run as administrator for best results.")
        print("")

    app.run(host=args.host, port=args.port, debug=args.debug)
