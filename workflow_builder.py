"""
Interactive Workflow Builder GUI for Game Automation
Build, test, and export game automation workflows visually

Detailed Changes Summary:
    New Classes Added:
        ConfigManager - Handles persistent configuration storage
        RecentFilesDialog - UI for selecting from recent workflow files
    New Features:
        Configuration Management:
        Persistent storage of connection settings (SUT, Omniparser, Gemma IPs/ports)
        Default values for game metadata fields
        Window geometry and state persistence
        Vision model selection persistence
    Recent Files System:
        Track up to 10 recently opened workflow files
        Quick access via Ctrl+R or File menu
        Automatic cleanup of non-existent files
        Clear recent files option
    Keyboard Shortcuts:
        Ctrl+N: New workflow
        Ctrl+O: Open workflow
        Ctrl+R: Recent files
        Ctrl+S: Save workflow
        Ctrl+Shift+S: Save as
    Enhanced File Operations:
        Save/Save As distinction
        Current file tracking
        Window title updates with filename
        Configuration import/export
    Improved UX:
        Window state restoration on startup
        Proper application shutdown handling
        Persistent connection settings
        Default metadata values
    Technical Improvements:
        Better separation of concerns with ConfigManager
        Robust configuration merging for backward compatibility
        Proper error handling for configuration operations
        Clean window close event handling
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import yaml
from PIL import Image, ImageTk, ImageDraw, ImageFont
import os
import sys
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

# Import existing modules
from modules.network import NetworkManager
from modules.screenshot import ScreenshotManager
from modules.omniparser_client import OmniparserClient
from modules.gemma_client import GemmaClient, BoundingBox

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration and recent files."""
    
    def __init__(self):
        self.config_dir = "config"
        self.config_file = os.path.join(self.config_dir, "workflow_builder_config.json")
        self.max_recent_files = 10
        self.config = self.load_config()
    
    def ensure_config_dir(self):
        """Ensure config directory exists."""
        os.makedirs(self.config_dir, exist_ok=True)
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file."""
        self.ensure_config_dir()
        
        default_config = {
            "connections": {
                "sut": {
                    "ip": "192.168.50.217",
                    "port": "8080"
                },
                "omniparser": {
                    "ip": "192.168.50.241",
                    "port": "8000"
                },
                "gemma": {
                    "ip": "192.168.50.241",
                    "port": "1234"
                },
                "vision_model": "omniparser"
            },
            "recent_workflows": [],
            "window": {
                "geometry": "1600x900",
                "maximized": False
            },
            "defaults": {
                "game_name": "My Game",
                "version": "1.0",
                "benchmark_duration": "120",
                "startup_wait": "30",
                "resolution": "1920x1080",
                "preset": "High",
                "graphics_api": "DirectX 11"
            }
        }
        
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                # Merge with defaults to handle new keys
                self._merge_config(default_config, loaded_config)
                return default_config
            else:
                return default_config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return default_config
    
    def _merge_config(self, default: Dict, loaded: Dict):
        """Recursively merge loaded config into default config."""
        for key, value in loaded.items():
            if key in default:
                if isinstance(value, dict) and isinstance(default[key], dict):
                    self._merge_config(default[key], value)
                else:
                    default[key] = value
    
    def save_config(self):
        """Save configuration to file."""
        try:
            self.ensure_config_dir()
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
    
    def add_recent_workflow(self, filepath: str):
        """Add workflow to recent files list."""
        filepath = os.path.abspath(filepath)
        
        # Remove if already exists
        if filepath in self.config["recent_workflows"]:
            self.config["recent_workflows"].remove(filepath)
        
        # Add to beginning
        self.config["recent_workflows"].insert(0, filepath)
        
        # Limit to max recent files
        self.config["recent_workflows"] = self.config["recent_workflows"][:self.max_recent_files]
        
        # Remove non-existent files
        self.config["recent_workflows"] = [f for f in self.config["recent_workflows"] if os.path.exists(f)]
        
        self.save_config()
    
    def get_recent_workflows(self) -> List[str]:
        """Get list of recent workflow files."""
        # Filter out non-existent files
        existing_files = [f for f in self.config["recent_workflows"] if os.path.exists(f)]
        if len(existing_files) != len(self.config["recent_workflows"]):
            self.config["recent_workflows"] = existing_files
            self.save_config()
        return existing_files
    
    def update_connection_config(self, connection_type: str, ip: str, port: str):
        """Update connection configuration."""
        if connection_type not in self.config["connections"]:
            self.config["connections"][connection_type] = {}
        
        self.config["connections"][connection_type]["ip"] = ip
        self.config["connections"][connection_type]["port"] = port
        self.save_config()
    
    def update_window_config(self, geometry: str, maximized: bool = False):
        """Update window configuration."""
        self.config["window"]["geometry"] = geometry
        self.config["window"]["maximized"] = maximized
        self.save_config()
    
    def update_vision_model(self, model: str):
        """Update selected vision model."""
        self.config["connections"]["vision_model"] = model
        self.save_config()


class WorkflowStep:
    """Represents a single workflow step."""

    def __init__(self, step_number: int):
        self.step_number = step_number
        self.description = ""
        self.action_type = "find_and_click"  # or "action"
        self.element_type = "icon"
        self.text = ""
        self.text_match = "contains"
        self.action_config = {}
        self.verify_elements = []
        self.expected_delay = 2
        self.timeout = 20
        self.optional = False
        self.selected_bbox = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert step to YAML-compatible dictionary."""
        step_dict = {
            "description": self.description
        }

        # Handle find_and_click with new format
        if self.action_type == "find_and_click":
            step_dict["find"] = {
                "type": self.element_type,
                "text": self.text,
                "text_match": self.text_match
            }
            # Get button and move_duration from step attributes if available
            button = getattr(self, 'button', 'left')
            move_duration = getattr(self, 'move_duration', 0.3)
            step_dict["action"] = {
                "type": "click",
                "button": button,
                "move_duration": move_duration,
                "click_delay": 0.2
            }

        # Handle actions that need find block
        elif self.action_type in ["right_click", "double_click", "middle_click", "text", "drag", "key", "hotkey"]:
            step_dict["find"] = {
                "type": self.element_type,
                "text": self.text,
                "text_match": self.text_match
            }
            step_dict["action"] = self.action_config

        # Handle actions without find block
        elif self.action_config:
            step_dict["action"] = self.action_config

        # Add verification elements
        if self.verify_elements:
            step_dict["verify_success"] = self.verify_elements

        # Add optional flag if set
        if self.optional:
            step_dict["optional"] = True

        step_dict["expected_delay"] = self.expected_delay
        step_dict["timeout"] = self.timeout

        return step_dict


class InteractiveCanvas(tk.Canvas):
    """Canvas with interactive bounding boxes."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.image = None
        self.photo = None
        self.bboxes = []
        self.selected_bbox = None
        self.bbox_items = {}
        self.callback = None

        self.bind("<Button-1>", self.on_click)
        self.bind("<Motion>", self.on_hover)

    def load_image(self, image_path: str, bboxes: List[BoundingBox]):
        """Load image and draw bounding boxes."""
        try:
            self.image = Image.open(image_path)
            self.bboxes = bboxes
            self.selected_bbox = None
            self.draw_bboxes()
        except Exception as e:
            logger.error(f"Failed to load image: {e}")

    def draw_bboxes(self):
        """Draw bounding boxes on image."""
        if not self.image:
            return

        # Create a copy for drawing
        img_copy = self.image.copy()
        draw = ImageDraw.Draw(img_copy, 'RGBA')

        # Try to load a font
        try:
            font = ImageFont.truetype("arial.ttf", 12)
        except:
            font = ImageFont.load_default()

        # Draw each bounding box
        self.bbox_items = {}
        for i, bbox in enumerate(self.bboxes):
            x1, y1 = bbox.x, bbox.y
            x2, y2 = bbox.x + bbox.width, bbox.y + bbox.height

            # Color based on type
            if bbox.element_type == "icon":
                color = (0, 255, 0, 100)  # Green for icons
                border_color = (0, 200, 0, 255)
            else:
                color = (0, 0, 255, 100)  # Blue for text
                border_color = (0, 0, 200, 255)

            # Highlight selected
            if self.selected_bbox == i:
                color = (255, 255, 0, 150)  # Yellow
                border_color = (255, 200, 0, 255)
                border_width = 3
            else:
                border_width = 2

            # Draw rectangle
            draw.rectangle([x1, y1, x2, y2], fill=color, outline=border_color, width=border_width)

            # Draw label
            text = bbox.element_text[:20] if bbox.element_text else bbox.element_type
            draw.text((x1 + 2, y1 + 2), text, fill=(255, 255, 255, 255), font=font)

            # Store bbox coordinates (at original size)
            self.bbox_items[i] = (x1, y1, x2, y2)

        # NO RESIZING - Use original image size for maximum accuracy
        # Convert to PhotoImage
        self.photo = ImageTk.PhotoImage(img_copy)

        # Update canvas - set to original image size
        self.delete("all")
        self.config(width=img_copy.width, height=img_copy.height,
                   scrollregion=(0, 0, img_copy.width, img_copy.height))
        self.create_image(0, 0, anchor=tk.NW, image=self.photo)

    def on_click(self, event):
        """Handle click on canvas."""
        if not self.bboxes:
            return

        # NO SCALING - Direct coordinates (1:1 with original image)
        click_x = event.x + self.canvasx(0)
        click_y = event.y + self.canvasy(0)

        for i, (x1, y1, x2, y2) in self.bbox_items.items():
            if x1 <= click_x <= x2 and y1 <= click_y <= y2:
                self.selected_bbox = i
                self.draw_bboxes()
                if self.callback:
                    self.callback(self.bboxes[i])
                break

    def on_hover(self, event):
        """Show cursor change on hover."""
        if not self.bboxes:
            return

        # NO SCALING - Direct coordinates (1:1 with original image)
        hover_x = event.x + self.canvasx(0)
        hover_y = event.y + self.canvasy(0)

        for i, (x1, y1, x2, y2) in self.bbox_items.items():
            if x1 <= hover_x <= x2 and y1 <= hover_y <= y2:
                self.config(cursor="hand2")
                return

        self.config(cursor="")


class ActionDefinitionDialog(tk.Toplevel):
    """Enhanced dialog for defining all action types with verification support."""

    def __init__(self, parent, bbox: Optional[BoundingBox] = None):
        super().__init__(parent)
        self.title("Define Action")
        self.geometry("640x1050")
        self.result = None
        self.bbox = bbox
        self.verify_elements = []  # List of elements to verify

        self.create_widgets()
        self.center_window()

    def create_widgets(self):
        """Create clear dialog with organized radio button action selection."""
        # Create scrollable frame
        canvas = tk.Canvas(self)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # === DESCRIPTION ===
        desc_frame = ttk.LabelFrame(scrollable_frame, text="Step Description", padding=10)
        desc_frame.pack(fill=tk.X, padx=10, pady=5)

        self.desc_var = tk.StringVar()
        ttk.Entry(desc_frame, textvariable=self.desc_var, width=60, font=('TkDefaultFont', 10)).pack(fill=tk.X)
        ttk.Label(desc_frame, text="What does this step do? (e.g., 'Click PLAY button', 'Enter username')",
                 font=('TkDefaultFont', 8), foreground="gray").pack(anchor=tk.W, pady=(2,0))

        # === ACTION TYPE SELECTION (ORGANIZED RADIO BUTTONS) ===
        action_frame = ttk.LabelFrame(scrollable_frame, text="Action Type - Select what to do", padding=10)
        action_frame.pack(fill=tk.X, padx=10, pady=5)

        self.action_var = tk.StringVar(value="find_and_click")

        # Organized in columns for better readability
        actions_grid = [
            # Column 1: Click actions
            [
                ("🖱️ Click Actions", None, "header"),
                ("Find and Click", "find_and_click", "Find element and click it"),
                ("Right Click", "right_click", "Right-click on element"),
                ("Double Click", "double_click", "Double-click on element"),
                ("Middle Click", "middle_click", "Middle mouse button click"),
                ("Drag and Drop", "drag", "Drag element to location")
            ],
            # Column 2: Keyboard actions
            [
                ("⌨️ Keyboard Actions", None, "header"),
                ("Press Key", "key", "Press single key (Enter, Esc, etc.)"),
                ("Key Combo", "hotkey", "Press keys together (Ctrl+S)"),
                ("Type Text", "text", "Type text into field")
            ],
            # Column 3: Other actions
            [
                ("🖱️ Other Actions", None, "header"),
                ("Scroll", "scroll", "Scroll up or down"),
                ("Wait", "wait", "Wait for specified time")
            ]
        ]

        # Create 3 columns
        for col_idx, column in enumerate(actions_grid):
            col_frame = ttk.Frame(action_frame)
            col_frame.grid(row=0, column=col_idx, sticky=tk.N, padx=15, pady=5)

            for row_idx, item in enumerate(column):
                if item[2] == "header":
                    # Header label
                    ttk.Label(col_frame, text=item[0], font=('TkDefaultFont', 9, 'bold')).pack(anchor=tk.W, pady=(5,2))
                else:
                    # Radio button with description
                    rb_frame = ttk.Frame(col_frame)
                    rb_frame.pack(anchor=tk.W, fill=tk.X, pady=1)

                    rb = ttk.Radiobutton(rb_frame, text=item[0], variable=self.action_var,
                                        value=item[1], command=self.on_action_change)
                    rb.pack(anchor=tk.W)

                    # Description label
                    ttk.Label(rb_frame, text=f"  {item[2]}",
                             font=('TkDefaultFont', 7), foreground="gray").pack(anchor=tk.W, padx=(20,0))

        # === FIND ELEMENT (for actions that need it) ===
        self.element_frame = ttk.LabelFrame(scrollable_frame, text="Find Element - Which element to interact with?", padding=10)

        ttk.Label(self.element_frame, text="Element Type:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.type_var = tk.StringVar(value=self.bbox.element_type if self.bbox else "icon")
        type_combo = ttk.Combobox(self.element_frame, textvariable=self.type_var,
                    values=["icon", "text", "any"], state="readonly", width=15)
        type_combo.grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.element_frame, text="icon = button/clickable, text = label, any = both",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=5)

        ttk.Label(self.element_frame, text="Text Content:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.text_var = tk.StringVar(value=self.bbox.element_text if self.bbox else "")
        ttk.Entry(self.element_frame, textvariable=self.text_var, width=35, font=('TkDefaultFont', 9)).grid(
            row=1, column=1, columnspan=2, sticky=tk.W, pady=5)
        ttk.Label(self.element_frame, text="The text visible on the element (e.g., 'PLAY', 'Submit', 'OK')",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=2, column=1, columnspan=2, sticky=tk.W)

        ttk.Label(self.element_frame, text="Match Method:").grid(row=3, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.match_var = tk.StringVar(value="contains")
        match_combo = ttk.Combobox(self.element_frame, textvariable=self.match_var,
                    values=["contains", "exact", "startswith", "endswith"],
                    state="readonly", width=15)
        match_combo.grid(row=3, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.element_frame, text="'contains' is most flexible",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=3, column=2, sticky=tk.W, padx=5)

        # === ACTION-SPECIFIC SETTINGS (shown based on action type) ===

        # Click options
        self.click_frame = ttk.LabelFrame(scrollable_frame, text="Click Options", padding=10)

        ttk.Label(self.click_frame, text="Mouse Button:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.button_var = tk.StringVar(value="left")
        ttk.Combobox(self.click_frame, textvariable=self.button_var,
                    values=["left", "right", "middle"], state="readonly", width=12).grid(row=0, column=1, sticky=tk.W, pady=5)

        ttk.Label(self.click_frame, text="Move Duration:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.move_duration_var = tk.StringVar(value="0.3")
        ttk.Entry(self.click_frame, textvariable=self.move_duration_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.click_frame, text="seconds - Time to move mouse to element",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)

        # Key press options
        self.key_frame = ttk.LabelFrame(scrollable_frame, text="Key Press - Which key?", padding=10)
        ttk.Label(self.key_frame, text="Key Name:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.key_var = tk.StringVar(value="enter")
        ttk.Entry(self.key_frame, textvariable=self.key_var, width=25).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.key_frame, text="Common: enter, escape, tab, space, f1-f12, up, down, left, right",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Hotkey options
        self.hotkey_frame = ttk.LabelFrame(scrollable_frame, text="Key Combination - Which keys together?", padding=10)
        ttk.Label(self.hotkey_frame, text="Keys (comma-separated):").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.hotkey_var = tk.StringVar(value="ctrl, s")
        ttk.Entry(self.hotkey_frame, textvariable=self.hotkey_var, width=25).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.hotkey_frame, text="Examples: ctrl, s  or  ctrl, shift, esc  or  alt, f4",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Text input options
        self.text_frame = ttk.LabelFrame(scrollable_frame, text="Type Text - What to type?", padding=10)
        ttk.Label(self.text_frame, text="Text to Type:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.type_text_var = tk.StringVar()
        ttk.Entry(self.text_frame, textvariable=self.type_text_var, width=40).grid(row=0, column=1, sticky=tk.W, pady=5)
        self.clear_first_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.text_frame, text="Clear field first (select all with Ctrl+A before typing)",
                       variable=self.clear_first_var).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=2, padx=(5,0))

        # Drag options
        self.drag_frame = ttk.LabelFrame(scrollable_frame, text="Drag and Drop - Where to drag?", padding=10)
        ttk.Label(self.drag_frame, text="Drop at X:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.dest_x_var = tk.StringVar(value="0")
        ttk.Entry(self.drag_frame, textvariable=self.dest_x_var, width=10).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.drag_frame, text="Drop at Y:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.dest_y_var = tk.StringVar(value="0")
        ttk.Entry(self.drag_frame, textvariable=self.dest_y_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.drag_frame, text="Pixel coordinates where to drop the element",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Scroll options
        self.scroll_frame = ttk.LabelFrame(scrollable_frame, text="Scroll Settings", padding=10)
        ttk.Label(self.scroll_frame, text="Direction:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.scroll_dir_var = tk.StringVar(value="down")
        ttk.Combobox(self.scroll_frame, textvariable=self.scroll_dir_var,
                    values=["up", "down"], state="readonly", width=12).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.scroll_frame, text="Scroll Amount:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.scroll_clicks_var = tk.StringVar(value="3")
        ttk.Entry(self.scroll_frame, textvariable=self.scroll_clicks_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.scroll_frame, text="Number of scroll clicks (higher = scroll more)",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Wait options
        self.wait_frame = ttk.LabelFrame(scrollable_frame, text="Wait Duration", padding=10)
        ttk.Label(self.wait_frame, text="Wait Time (seconds):").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.duration_var = tk.StringVar(value="2")
        ttk.Entry(self.wait_frame, textvariable=self.duration_var, width=10).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.wait_frame, text="How many seconds to pause",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # === VERIFY SUCCESS ===
        self.verify_frame = ttk.LabelFrame(scrollable_frame, text="Verify Success (Optional) - Check if action worked", padding=10)
        self.verify_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(self.verify_frame, text="Add elements that should appear after this action completes:",
                 font=('TkDefaultFont', 8), foreground="gray").pack(anchor=tk.W, pady=2)

        verify_list_frame = ttk.Frame(self.verify_frame)
        verify_list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.verify_listbox = tk.Listbox(verify_list_frame, height=3)
        self.verify_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        verify_scroll = ttk.Scrollbar(verify_list_frame, orient=tk.VERTICAL, command=self.verify_listbox.yview)
        verify_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.verify_listbox.config(yscrollcommand=verify_scroll.set)

        verify_btn_frame = ttk.Frame(self.verify_frame)
        verify_btn_frame.pack(fill=tk.X, pady=2)

        ttk.Button(verify_btn_frame, text="+ Add Element", command=self.add_verify_element).pack(side=tk.LEFT, padx=2)
        ttk.Button(verify_btn_frame, text="- Remove", command=self.remove_verify_element).pack(side=tk.LEFT, padx=2)

        # === TIMING ===
        timing_frame = ttk.LabelFrame(scrollable_frame, text="Timing Settings", padding=10)
        timing_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(timing_frame, text="Expected Delay:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.delay_var = tk.StringVar(value="2")
        ttk.Entry(timing_frame, textvariable=self.delay_var, width=10).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(timing_frame, text="seconds - Wait after completing this step before next step",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=5)

        ttk.Label(timing_frame, text="Timeout:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.timeout_var = tk.StringVar(value="20")
        ttk.Entry(timing_frame, textvariable=self.timeout_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(timing_frame, text="seconds - Maximum time to wait for element to appear",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)

        ttk.Label(timing_frame, text="Optional Step:").grid(row=2, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.optional_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(timing_frame, text="Continue workflow even if this step fails",
                       variable=self.optional_var).grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=5)
        ttk.Label(timing_frame, text="If checked, failures in this step won't stop the workflow",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=5)

        # === BUTTONS ===
        btn_frame = ttk.Frame(scrollable_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text="OK", command=self.on_ok, width=12).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.on_cancel, width=12).pack(side=tk.RIGHT)

        # Initialize - show initial action settings
        self.on_action_change()

    def add_verify_element(self):
        """Add element to verify list."""
        dialog = tk.Toplevel(self)
        dialog.title("Add Verification Element")
        dialog.geometry("400x200")

        ttk.Label(dialog, text="Element Type:").grid(row=0, column=0, sticky=tk.W, padx=10, pady=5)
        type_var = tk.StringVar(value="icon")
        ttk.Combobox(dialog, textvariable=type_var, values=["icon", "text", "any"],
                    state="readonly", width=20).grid(row=0, column=1, sticky=tk.W, padx=10, pady=5)

        ttk.Label(dialog, text="Text:").grid(row=1, column=0, sticky=tk.W, padx=10, pady=5)
        text_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=text_var, width=30).grid(row=1, column=1, sticky=tk.W, padx=10, pady=5)

        ttk.Label(dialog, text="Text Match:").grid(row=2, column=0, sticky=tk.W, padx=10, pady=5)
        match_var = tk.StringVar(value="contains")
        ttk.Combobox(dialog, textvariable=match_var, values=["contains", "exact", "startswith", "endswith"],
                    state="readonly", width=20).grid(row=2, column=1, sticky=tk.W, padx=10, pady=5)

        def add_element():
            element = {
                "type": type_var.get(),
                "text": text_var.get(),
                "text_match": match_var.get()
            }
            self.verify_elements.append(element)
            self.verify_listbox.insert(tk.END, f"{element['type']}: '{element['text']}' ({element['text_match']})")
            dialog.destroy()

        ttk.Button(dialog, text="Add", command=add_element).grid(row=3, column=0, columnspan=2, pady=10)

    def remove_verify_element(self):
        """Remove selected element from verify list."""
        selection = self.verify_listbox.curselection()
        if selection:
            idx = selection[0]
            self.verify_listbox.delete(idx)
            self.verify_elements.pop(idx)

    def on_action_change(self):
        """Show/hide frames based on action type."""
        action = self.action_var.get()

        # Hide all configuration frames
        self.element_frame.pack_forget()
        self.click_frame.pack_forget()
        self.key_frame.pack_forget()
        self.hotkey_frame.pack_forget()
        self.text_frame.pack_forget()
        self.drag_frame.pack_forget()
        self.scroll_frame.pack_forget()
        self.wait_frame.pack_forget()

        # Show relevant frames
        if action == "find_and_click":
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
            self.click_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action in ["right_click", "double_click", "middle_click"]:
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "key":
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
            self.key_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "hotkey":
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
            self.hotkey_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "text":
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
            self.text_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "drag":
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
            self.drag_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "scroll":
            self.scroll_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "wait":
            self.wait_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)

    def on_ok(self):
        """Build result and close."""
        try:
            self.result = {
                "action_type": self.action_var.get(),
                "description": self.desc_var.get(),
                "expected_delay": int(self.delay_var.get()) if self.delay_var.get() else 2,
                "timeout": int(self.timeout_var.get()) if self.timeout_var.get() else 20,
                "verify_elements": self.verify_elements,
                "optional": self.optional_var.get()
            }

            action = self.action_var.get()

            # Build action-specific config
            if action == "find_and_click":
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                self.result["button"] = self.button_var.get()
                self.result["move_duration"] = float(self.move_duration_var.get()) if self.move_duration_var.get() else 0.3

            elif action in ["right_click", "double_click", "middle_click"]:
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                self.result["action_config"] = {"type": action}

            elif action == "key":
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                self.result["action_config"] = {
                    "type": "key",
                    "key": self.key_var.get()
                }
            elif action == "hotkey":
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                keys = [k.strip() for k in self.hotkey_var.get().split(",")]
                self.result["action_config"] = {
                    "type": "hotkey",
                    "keys": keys
                }
            elif action == "text":
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                self.result["action_config"] = {
                    "type": "text",
                    "text": self.type_text_var.get(),
                    "clear_first": self.clear_first_var.get(),
                    "char_delay": 0.05
                }
            elif action == "drag":
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                self.result["action_config"] = {
                    "type": "drag",
                    "dest_x": int(self.dest_x_var.get()) if self.dest_x_var.get() else 0,
                    "dest_y": int(self.dest_y_var.get()) if self.dest_y_var.get() else 0,
                    "duration": 1.0,
                    "steps": 20
                }
            elif action == "scroll":
                self.result["action_config"] = {
                    "type": "scroll",
                    "direction": self.scroll_dir_var.get(),
                    "clicks": int(self.scroll_clicks_var.get()) if self.scroll_clicks_var.get() else 3
                }
            elif action == "wait":
                duration_value = self.duration_var.get().strip()
                try:
                    duration = int(duration_value) if duration_value else 2
                except ValueError:
                    duration = 2

                self.result["action_config"] = {
                    "type": "wait",
                    "duration": duration
                }

            self.destroy()

        except Exception as e:
            import traceback
            from tkinter import messagebox
            error_msg = f"Error saving step:\n{str(e)}\n\nDetails:\n{traceback.format_exc()}"
            messagebox.showerror("Error", error_msg)
            print(error_msg)

    def on_cancel(self):
        """Cancel dialog."""
        self.result = None
        self.destroy()

    def center_window(self):
        """Center window on screen."""
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')


class RecentFilesDialog(tk.Toplevel):
    """Dialog for selecting recent workflow files."""
    
    def __init__(self, parent, recent_files: List[str]):
        super().__init__(parent)
        self.title("Recent Workflows")
        self.geometry("600x400")
        self.result = None
        self.recent_files = recent_files
        
        self.create_widgets()
        self.center_window()
    
    def create_widgets(self):
        """Create dialog widgets."""
        # Header
        header_frame = ttk.Frame(self)
        header_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(header_frame, text="Recent Workflow Files", 
                 font=('TkDefaultFont', 12, 'bold')).pack(anchor=tk.W)
        ttk.Label(header_frame, text="Double-click to open a workflow file", 
                 font=('TkDefaultFont', 9), foreground="gray").pack(anchor=tk.W)
        
        # Files list
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Listbox with scrollbar
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.files_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                       font=("Consolas", 10))
        self.files_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.files_listbox.yview)
        
        # Populate list
        for filepath in self.recent_files:
            # Show filename and directory
            filename = os.path.basename(filepath)
            directory = os.path.dirname(filepath)
            display_text = f"{filename}\n    {directory}"
            self.files_listbox.insert(tk.END, display_text)
        
        # Bind double-click
        self.files_listbox.bind("<Double-Button-1>", self.on_double_click)
        
        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(btn_frame, text="Open", command=self.on_open).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.on_cancel).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Clear List", command=self.on_clear).pack(side=tk.LEFT)
        
        # Select first item if available
        if self.recent_files:
            self.files_listbox.selection_set(0)
    
    def on_double_click(self, event):
        """Handle double-click on file."""
        self.on_open()
    
    def on_open(self):
        """Open selected file."""
        selection = self.files_listbox.curselection()
        if selection:
            idx = selection[0]
            self.result = self.recent_files[idx]
            self.destroy()
    
    def on_clear(self):
        """Clear recent files list."""
        if messagebox.askyesno("Confirm", "Clear all recent files?"):
            self.result = "CLEAR"
            self.destroy()
    
    def on_cancel(self):
        """Cancel dialog."""
        self.result = None
        self.destroy()
    
    def center_window(self):
        """Center window on screen."""
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')


class WorkflowBuilderGUI:
    """Main Workflow Builder GUI Application."""

    def __init__(self, root):
        self.root = root
        self.root.title("Katana Workflow Builder")
        
        # Initialize configuration manager
        self.config_manager = ConfigManager()
        
        # Apply saved window configuration
        window_config = self.config_manager.config["window"]
        self.root.geometry(window_config["geometry"])
        if window_config["maximized"]:
            self.root.state('zoomed')  # Windows
        
        # State
        self.current_screenshot = None
        self.current_bboxes = []
        self.workflow_steps = []
        self.network = None
        self.screenshot_mgr = None
        self.vision_model = None

        # Load connection settings from config
        conn_config = self.config_manager.config["connections"]
        
        # SUT connection
        self.sut_ip = tk.StringVar(value=conn_config["sut"]["ip"])
        self.sut_port = tk.StringVar(value=conn_config["sut"]["port"])

        # Vision model connections
        self.omniparser_ip = tk.StringVar(value=conn_config["omniparser"]["ip"])
        self.omniparser_port = tk.StringVar(value=conn_config["omniparser"]["port"])
        self.gemma_ip = tk.StringVar(value=conn_config["gemma"]["ip"])
        self.gemma_port = tk.StringVar(value=conn_config["gemma"]["port"])

        # Store connection states for each model
        self.omniparser_connection = None
        self.gemma_connection = None

        # Load metadata defaults from config
        defaults = self.config_manager.config["defaults"]
        self.game_name = tk.StringVar(value=defaults["game_name"])
        self.game_path = tk.StringVar(value="")
        self.process_id = tk.StringVar(value="")
        self.process_name = tk.StringVar(value="")
        self.version = tk.StringVar(value=defaults["version"])
        self.benchmark_duration = tk.StringVar(value=defaults["benchmark_duration"])
        self.startup_wait = tk.StringVar(value=defaults["startup_wait"])
        self.resolution = tk.StringVar(value=defaults["resolution"])
        self.preset = tk.StringVar(value=defaults["preset"])
        self.benchmark_name = tk.StringVar(value="")
        self.engine = tk.StringVar(value="")
        self.graphics_api = tk.StringVar(value=defaults["graphics_api"])

        # Clipboard for copy/paste
        self.copied_step = None

        self.create_widgets()
        self.center_window()
        
        # Bind window close event to save configuration
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Handle window closing - save configuration."""
        try:
            # Save window state
            geometry = self.root.geometry()
            maximized = self.root.state() == 'zoomed'
            self.config_manager.update_window_config(geometry, maximized)
            
            # Save connection settings
            self.config_manager.update_connection_config("sut", self.sut_ip.get(), self.sut_port.get())
            self.config_manager.update_connection_config("omniparser", self.omniparser_ip.get(), self.omniparser_port.get())
            self.config_manager.update_connection_config("gemma", self.gemma_ip.get(), self.gemma_port.get())
            
            # Save vision model selection
            vision_model = getattr(self, 'vision_var', None)
            if vision_model:
                self.config_manager.update_vision_model(vision_model.get())
            
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
        
        self.root.destroy()

    def create_widgets(self):
        """Create main GUI widgets."""
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New Workflow", command=self.new_workflow, accelerator="Ctrl+N")
        file_menu.add_separator()
        file_menu.add_command(label="Open YAML...", command=self.load_yaml, accelerator="Ctrl+O")
        file_menu.add_command(label="Open Recent", command=self.show_recent_files, accelerator="Ctrl+R")
        file_menu.add_separator()
        file_menu.add_command(label="Save YAML", command=self.save_yaml, accelerator="Ctrl+S")
        file_menu.add_command(label="Save YAML As...", command=self.save_yaml_as, accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Load Configuration", command=self.load_configuration)
        file_menu.add_command(label="Save Configuration", command=self.save_configuration)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing, accelerator="Alt+F4")

        # Bind keyboard shortcuts
        self.root.bind('<Control-n>', lambda e: self.new_workflow())
        self.root.bind('<Control-o>', lambda e: self.load_yaml())
        self.root.bind('<Control-r>', lambda e: self.show_recent_files())
        self.root.bind('<Control-s>', lambda e: self.save_yaml())
        self.root.bind('<Control-S>', lambda e: self.save_yaml_as())

        # Main container
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # Left panel (Screenshot and controls)
        left_panel = ttk.Frame(main_paned)
        main_paned.add(left_panel, weight=3)

        # Control panel
        control_frame = ttk.LabelFrame(left_panel, text="Game Control", padding=10)
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        # SUT connection
        sut_frame = ttk.LabelFrame(control_frame, text="SUT Connection", padding=5)
        sut_frame.pack(fill=tk.X, pady=2)

        sut_conn_frame = ttk.Frame(sut_frame)
        sut_conn_frame.pack(fill=tk.X)

        ttk.Label(sut_conn_frame, text="IP:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(sut_conn_frame, textvariable=self.sut_ip, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Label(sut_conn_frame, text="Port:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(sut_conn_frame, textvariable=self.sut_port, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(sut_conn_frame, text="Connect", command=self.connect_sut).pack(side=tk.LEFT, padx=5)
        self.sut_status_label = ttk.Label(sut_conn_frame, text="Not Connected", foreground="red")
        self.sut_status_label.pack(side=tk.LEFT, padx=5)

        # Vision model connection
        vision_conn_frame = ttk.LabelFrame(control_frame, text="Vision Model Connection", padding=5)
        vision_conn_frame.pack(fill=tk.X, pady=2)

        # Vision model selection
        vision_select_frame = ttk.Frame(vision_conn_frame)
        vision_select_frame.pack(fill=tk.X, pady=2)

        ttk.Label(vision_select_frame, text="Model:").pack(side=tk.LEFT, padx=2)
        self.vision_var = tk.StringVar(value=self.config_manager.config["connections"]["vision_model"])
        ttk.Radiobutton(vision_select_frame, text="Omniparser", variable=self.vision_var,
                       value="omniparser", command=self.on_vision_model_change).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(vision_select_frame, text="Gemma", variable=self.vision_var,
                       value="gemma", command=self.on_vision_model_change).pack(side=tk.LEFT, padx=5)

        # Omniparser connection
        self.omni_frame = ttk.Frame(vision_conn_frame)
        self.omni_frame.pack(fill=tk.X, pady=2)

        ttk.Label(self.omni_frame, text="IP:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(self.omni_frame, textvariable=self.omniparser_ip, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.omni_frame, text="Port:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(self.omni_frame, textvariable=self.omniparser_port, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.omni_frame, text="Connect", command=self.connect_vision_model).pack(side=tk.LEFT, padx=5)
        self.vision_status_label = ttk.Label(self.omni_frame, text="Not Connected", foreground="red")
        self.vision_status_label.pack(side=tk.LEFT, padx=5)

        # Gemma connection (hidden by default)
        self.gemma_frame = ttk.Frame(vision_conn_frame)

        ttk.Label(self.gemma_frame, text="IP:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(self.gemma_frame, textvariable=self.gemma_ip, width=15).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.gemma_frame, text="Port:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(self.gemma_frame, textvariable=self.gemma_port, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.gemma_frame, text="Connect", command=self.connect_vision_model).pack(side=tk.LEFT, padx=5)

        # Create status label for gemma frame (same reference as omni_frame status)
        self.gemma_status_label = ttk.Label(self.gemma_frame, text="Not Connected", foreground="red")
        self.gemma_status_label.pack(side=tk.LEFT, padx=5)

        # Initialize vision model display
        self.on_vision_model_change()

        # Action buttons
        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Capture Screenshot",
                  command=self.capture_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Parse Screenshot",
                  command=self.parse_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Test Action",
                  command=self.test_action).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Open Screenshots Folder",
                  command=self.open_screenshots_folder).pack(side=tk.LEFT, padx=2)

        # Screenshot display
        screenshot_frame = ttk.LabelFrame(left_panel, text="Screenshot (Click on elements - Original Size)", padding=5)
        screenshot_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Scrollable canvas with scrollbars
        canvas_container = ttk.Frame(screenshot_frame)
        canvas_container.pack(fill=tk.BOTH, expand=True)

        # Horizontal scrollbar
        h_scrollbar = ttk.Scrollbar(canvas_container, orient=tk.HORIZONTAL)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Vertical scrollbar
        v_scrollbar = ttk.Scrollbar(canvas_container, orient=tk.VERTICAL)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Canvas with scrollbars
        self.canvas = InteractiveCanvas(canvas_container, bg="gray20",
                                       xscrollcommand=h_scrollbar.set,
                                       yscrollcommand=v_scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Configure scrollbars
        h_scrollbar.config(command=self.canvas.xview)
        v_scrollbar.config(command=self.canvas.yview)

        self.canvas.callback = self.on_element_selected

        # Right panel (Workflow steps and element details)
        right_panel = ttk.Frame(main_paned)
        main_paned.add(right_panel, weight=2)

        # Element details
        details_frame = ttk.LabelFrame(right_panel, text="Selected Element", padding=10)
        details_frame.pack(fill=tk.X, padx=5, pady=5)

        self.details_text = scrolledtext.ScrolledText(details_frame, height=8, width=40)
        self.details_text.pack(fill=tk.BOTH, expand=True)

        # Workflow steps
        steps_frame = ttk.LabelFrame(right_panel, text="Workflow Steps", padding=10)
        steps_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Step management buttons - Row 1
        step_btn_frame = ttk.Frame(steps_frame)
        step_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(step_btn_frame, text="+ Add Step",
                  command=self.add_step).pack(side=tk.LEFT, padx=2)
        ttk.Button(step_btn_frame, text="Edit Step",
                  command=self.edit_step).pack(side=tk.LEFT, padx=2)
        ttk.Button(step_btn_frame, text="Remove Step",
                  command=self.remove_step).pack(side=tk.LEFT, padx=2)

        # Row 2 - More actions
        step_btn_frame2 = ttk.Frame(steps_frame)
        step_btn_frame2.pack(fill=tk.X, pady=2)

        ttk.Button(step_btn_frame2, text="^ Move Up",
                  command=self.move_step_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(step_btn_frame2, text="v Move Down",
                  command=self.move_step_down).pack(side=tk.LEFT, padx=2)
        ttk.Button(step_btn_frame2, text="Copy Step",
                  command=self.copy_step).pack(side=tk.LEFT, padx=2)
        ttk.Button(step_btn_frame2, text="Paste Step",
                  command=self.paste_step).pack(side=tk.LEFT, padx=2)

        # Steps list
        list_frame = ttk.Frame(steps_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.steps_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                        font=("Consolas", 10))
        self.steps_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.steps_listbox.yview)

        # Metadata frame with scrollable canvas
        metadata_frame = ttk.LabelFrame(right_panel, text="Workflow Metadata", padding=10)
        metadata_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Create canvas and scrollbar for metadata
        meta_canvas = tk.Canvas(metadata_frame, height=250)
        meta_scrollbar = ttk.Scrollbar(metadata_frame, orient="vertical", command=meta_canvas.yview)
        meta_scrollable = ttk.Frame(meta_canvas)

        meta_scrollable.bind(
            "<Configure>",
            lambda e: meta_canvas.configure(scrollregion=meta_canvas.bbox("all"))
        )

        meta_canvas.create_window((0, 0), window=meta_scrollable, anchor="nw")
        meta_canvas.configure(yscrollcommand=meta_scrollbar.set)

        meta_canvas.pack(side="left", fill="both", expand=True)
        meta_scrollbar.pack(side="right", fill="y")

        # === BASIC INFO ===
        ttk.Label(meta_scrollable, text="Basic Information", font=('TkDefaultFont', 9, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0,5))

        ttk.Label(meta_scrollable, text="Game Name:").grid(row=1, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.game_name, width=35).grid(row=1, column=1, sticky=tk.W, pady=2)

        ttk.Label(meta_scrollable, text="Version:").grid(row=2, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.version, width=35).grid(row=2, column=1, sticky=tk.W, pady=2)

        ttk.Label(meta_scrollable, text="Engine:").grid(row=3, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.engine, width=35).grid(row=3, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="e.g., Unreal Engine 5, Source 2, Unity",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=3, column=2, sticky=tk.W, padx=5)

        # === EXECUTABLE INFO ===
        ttk.Label(meta_scrollable, text="Executable Information", font=('TkDefaultFont', 9, 'bold')).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=(10,5))

        ttk.Label(meta_scrollable, text="Game Path:").grid(row=5, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.game_path, width=35).grid(row=5, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="Full path to game executable",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=5, column=2, sticky=tk.W, padx=5)

        ttk.Label(meta_scrollable, text="Process ID:").grid(row=6, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.process_id, width=35).grid(row=6, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="Short name (e.g., cs2, sottr)",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=6, column=2, sticky=tk.W, padx=5)

        ttk.Label(meta_scrollable, text="Process Name:").grid(row=7, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.process_name, width=35).grid(row=7, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="e.g., cs2.exe, sottr.exe",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=7, column=2, sticky=tk.W, padx=5)

        # === BENCHMARK SETTINGS ===
        ttk.Label(meta_scrollable, text="Benchmark Settings", font=('TkDefaultFont', 9, 'bold')).grid(
            row=8, column=0, columnspan=2, sticky=tk.W, pady=(10,5))

        ttk.Label(meta_scrollable, text="Benchmark Name:").grid(row=9, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.benchmark_name, width=35).grid(row=9, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="e.g., Built-in Benchmark, Custom Map",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=9, column=2, sticky=tk.W, padx=5)

        ttk.Label(meta_scrollable, text="Duration (sec):").grid(row=10, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.benchmark_duration, width=35).grid(row=10, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="Total benchmark duration in seconds",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=10, column=2, sticky=tk.W, padx=5)

        ttk.Label(meta_scrollable, text="Startup Wait (sec):").grid(row=11, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.startup_wait, width=35).grid(row=11, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="Time to wait for game to start",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=11, column=2, sticky=tk.W, padx=5)

        # === GRAPHICS SETTINGS ===
        ttk.Label(meta_scrollable, text="Graphics Settings", font=('TkDefaultFont', 9, 'bold')).grid(
            row=12, column=0, columnspan=2, sticky=tk.W, pady=(10,5))

        ttk.Label(meta_scrollable, text="Resolution:").grid(row=13, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.resolution, width=35).grid(row=13, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="e.g., 1920x1080, 2560x1440",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=13, column=2, sticky=tk.W, padx=5)

        ttk.Label(meta_scrollable, text="Graphics Preset:").grid(row=14, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.preset, width=35).grid(row=14, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="e.g., Low, Medium, High, Ultra",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=14, column=2, sticky=tk.W, padx=5)

        ttk.Label(meta_scrollable, text="Graphics API:").grid(row=15, column=0, sticky=tk.W, pady=2, padx=(5,10))
        ttk.Entry(meta_scrollable, textvariable=self.graphics_api, width=35).grid(row=15, column=1, sticky=tk.W, pady=2)
        ttk.Label(meta_scrollable, text="e.g., DirectX 11, DirectX 12, Vulkan",
                 font=('TkDefaultFont', 7), foreground="gray").grid(row=15, column=2, sticky=tk.W, padx=5)

        # Status bar
        status_bar = ttk.Frame(self.root)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_text = tk.StringVar(value="Ready")
        ttk.Label(status_bar, textvariable=self.status_text, relief=tk.SUNKEN).pack(fill=tk.X)

    def on_vision_model_change(self):
        """Handle vision model selection change - preserves connection state."""
        if self.vision_var.get() == "omniparser":
            self.gemma_frame.pack_forget()
            self.omni_frame.pack(fill=tk.X, pady=2)

            # Restore omniparser connection and status if it exists
            if self.omniparser_connection:
                self.vision_model = self.omniparser_connection
                self.vision_status_label.config(text="Connected", foreground="green")
            else:
                self.vision_model = None
                self.vision_status_label.config(text="Not Connected", foreground="red")
        else:
            self.omni_frame.pack_forget()
            self.gemma_frame.pack(fill=tk.X, pady=2)

            # Restore gemma connection and status if it exists
            if self.gemma_connection:
                self.vision_model = self.gemma_connection
                self.gemma_status_label.config(text="Connected", foreground="green")
            else:
                self.vision_model = None
                self.gemma_status_label.config(text="Not Connected", foreground="red")

    def connect_sut(self):
        """Connect to SUT service."""
        try:
            ip = self.sut_ip.get()
            port = int(self.sut_port.get())

            self.network = NetworkManager(ip, port)
            self.screenshot_mgr = ScreenshotManager(self.network)

            self.sut_status_label.config(text="Connected", foreground="green")
            self.status_text.set(f"Connected to SUT at {ip}:{port}")
            messagebox.showinfo("Success", "Connected to SUT successfully!")

        except Exception as e:
            self.sut_status_label.config(text="Connection Failed", foreground="red")
            messagebox.showerror("Error", f"Failed to connect: {str(e)}")

    def connect_vision_model(self):
        """Connect to vision model service and save connection state."""
        try:
            if self.vision_var.get() == "omniparser":
                ip = self.omniparser_ip.get()
                port = self.omniparser_port.get()
                url = f"http://{ip}:{port}"
                self.vision_model = OmniparserClient(url)
                # Save connection for this model
                self.omniparser_connection = self.vision_model
                model_name = "Omniparser"
                status_label = self.vision_status_label
            else:
                ip = self.gemma_ip.get()
                port = self.gemma_port.get()
                url = f"http://{ip}:{port}"
                self.vision_model = GemmaClient(url)
                # Save connection for this model
                self.gemma_connection = self.vision_model
                model_name = "Gemma"
                status_label = self.gemma_status_label

            status_label.config(text="Connected", foreground="green")
            self.status_text.set(f"Connected to {model_name} at {url}")
            messagebox.showinfo("Success", f"Connected to {model_name} successfully!")

        except Exception as e:
            if self.vision_var.get() == "omniparser":
                self.vision_status_label.config(text="Connection Failed", foreground="red")
                # Clear saved connection on failure
                self.omniparser_connection = None
            else:
                self.gemma_status_label.config(text="Connection Failed", foreground="red")
                # Clear saved connection on failure
                self.gemma_connection = None
            messagebox.showerror("Error", f"Failed to connect to vision model: {str(e)}")

    def capture_screenshot(self):
        """Capture screenshot from SUT."""
        if not self.screenshot_mgr:
            messagebox.showwarning("Warning", "Please connect to SUT first!")
            return

        try:
            # Create temp directory
            os.makedirs("workflow_builder_temp", exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"workflow_builder_temp/screenshot_{timestamp}.png"

            self.screenshot_mgr.capture(screenshot_path)
            self.current_screenshot = screenshot_path

            self.status_text.set(f"Screenshot captured: {screenshot_path}")
            messagebox.showinfo("Success", "Screenshot captured! Click 'Parse Screenshot' to analyze.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to capture screenshot: {str(e)}")

    def parse_screenshot(self):
        """Parse screenshot with vision model."""
        if not self.current_screenshot:
            messagebox.showwarning("Warning", "Please capture a screenshot first!")
            return

        if not self.vision_model:
            messagebox.showwarning("Warning", "Please connect to Vision Model first!")
            return

        try:
            self.status_text.set("Parsing screenshot...")
            self.root.update()

            # Parse with vision model
            annotation_path = self.current_screenshot.replace(".png", "_annotated.png")
            self.current_bboxes = self.vision_model.detect_ui_elements(
                self.current_screenshot, annotation_path
            )

            # Display on canvas
            self.canvas.load_image(self.current_screenshot, self.current_bboxes)

            self.status_text.set(f"Found {len(self.current_bboxes)} UI elements")
            messagebox.showinfo("Success", f"Found {len(self.current_bboxes)} UI elements!\nClick on elements to select them.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse screenshot: {str(e)}")
            self.status_text.set("Parse failed")

    def on_element_selected(self, bbox: BoundingBox):
        """Handle element selection."""
        # Display element details
        details = f"Type: {bbox.element_type}\n"
        details += f"Text: {bbox.element_text}\n"
        details += f"Position: ({bbox.x}, {bbox.y})\n"
        details += f"Size: {bbox.width} x {bbox.height}\n"
        details += f"Confidence: {bbox.confidence:.2f}\n"

        self.details_text.delete(1.0, tk.END)
        self.details_text.insert(1.0, details)

        self.status_text.set(f"Selected: {bbox.element_type} '{bbox.element_text}'")

    def add_step(self):
        """Add new workflow step with support for all action types."""
        selected_bbox = None
        if self.canvas.selected_bbox is not None:
            selected_bbox = self.current_bboxes[self.canvas.selected_bbox]

        dialog = ActionDefinitionDialog(self.root, selected_bbox)
        self.root.wait_window(dialog)

        if dialog.result:
            step = WorkflowStep(len(self.workflow_steps) + 1)
            step.description = dialog.result["description"]
            step.action_type = dialog.result["action_type"]
            step.expected_delay = dialog.result["expected_delay"]
            step.timeout = dialog.result["timeout"]
            step.verify_elements = dialog.result.get("verify_elements", [])
            step.optional = dialog.result.get("optional", False)

            # Handle find_and_click
            if step.action_type == "find_and_click":
                step.element_type = dialog.result["element_type"]
                step.text = dialog.result["text"]
                step.text_match = dialog.result["text_match"]
                step.button = dialog.result.get("button", "left")
                step.move_duration = dialog.result.get("move_duration", 0.3)

            # Handle actions that need find block
            elif step.action_type in ["right_click", "double_click", "middle_click", "text", "drag", "key", "hotkey"]:
                step.element_type = dialog.result["element_type"]
                step.text = dialog.result["text"]
                step.text_match = dialog.result["text_match"]
                step.action_config = dialog.result["action_config"]

            # Handle actions without find block
            elif "action_config" in dialog.result:
                step.action_config = dialog.result["action_config"]

            step.selected_bbox = selected_bbox

            self.workflow_steps.append(step)
            self.refresh_steps_list()
            self.status_text.set(f"Added step {step.step_number}")

    def edit_step(self):
        """Edit selected step with support for all action types."""
        selection = self.steps_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a step to edit!")
            return

        idx = selection[0]
        step = self.workflow_steps[idx]

        dialog = ActionDefinitionDialog(self.root, step.selected_bbox)

        # Pre-fill dialog
        dialog.action_var.set(step.action_type)
        dialog.desc_var.set(step.description)
        dialog.delay_var.set(str(step.expected_delay))
        dialog.timeout_var.set(str(step.timeout))
        dialog.optional_var.set(getattr(step, 'optional', False))

        # Pre-fill verification elements
        if hasattr(step, 'verify_elements') and step.verify_elements:
            dialog.verify_elements = step.verify_elements.copy()
            for elem in step.verify_elements:
                dialog.verify_listbox.insert(tk.END, f"{elem['type']}: '{elem['text']}' ({elem['text_match']})")

        # Pre-fill action-specific fields
        if step.action_type == "find_and_click":
            dialog.type_var.set(step.element_type)
            dialog.text_var.set(step.text)
            dialog.match_var.set(step.text_match)
            if hasattr(step, 'button'):
                dialog.button_var.set(step.button)
            if hasattr(step, 'move_duration'):
                dialog.move_duration_var.set(str(step.move_duration))

        elif step.action_type in ["right_click", "double_click", "middle_click"]:
            dialog.type_var.set(step.element_type)
            dialog.text_var.set(step.text)
            dialog.match_var.set(step.text_match)

        elif step.action_type == "text":
            dialog.type_var.set(step.element_type)
            dialog.text_var.set(step.text)
            dialog.match_var.set(step.text_match)
            if hasattr(step, 'action_config') and step.action_config:
                dialog.type_text_var.set(step.action_config.get('text', ''))
                dialog.clear_first_var.set(step.action_config.get('clear_first', False))

        elif step.action_type == "drag":
            dialog.type_var.set(step.element_type)
            dialog.text_var.set(step.text)
            dialog.match_var.set(step.text_match)
            if hasattr(step, 'action_config') and step.action_config:
                dialog.dest_x_var.set(str(step.action_config.get('dest_x', 0)))
                dialog.dest_y_var.set(str(step.action_config.get('dest_y', 0)))

        elif step.action_type == "key":
            dialog.type_var.set(step.element_type)
            dialog.text_var.set(step.text)
            dialog.match_var.set(step.text_match)
            if hasattr(step, 'action_config') and step.action_config:
                dialog.key_var.set(step.action_config.get('key', 'enter'))

        elif step.action_type == "hotkey":
            dialog.type_var.set(step.element_type)
            dialog.text_var.set(step.text)
            dialog.match_var.set(step.text_match)
            if hasattr(step, 'action_config') and step.action_config:
                keys = step.action_config.get('keys', [])
                dialog.hotkey_var.set(', '.join(keys))

        elif step.action_type == "scroll":
            if hasattr(step, 'action_config') and step.action_config:
                dialog.scroll_dir_var.set(step.action_config.get('direction', 'down'))
                dialog.scroll_clicks_var.set(str(step.action_config.get('clicks', 3)))

        elif step.action_type == "wait":
            if hasattr(step, 'action_config') and step.action_config:
                dialog.duration_var.set(str(step.action_config.get('duration', 2)))

        dialog.on_action_change()

        self.root.wait_window(dialog)

        if dialog.result:
            step.description = dialog.result["description"]
            step.action_type = dialog.result["action_type"]
            step.expected_delay = dialog.result["expected_delay"]
            step.timeout = dialog.result["timeout"]
            step.verify_elements = dialog.result.get("verify_elements", [])
            step.optional = dialog.result.get("optional", False)

            # Handle find_and_click
            if step.action_type == "find_and_click":
                step.element_type = dialog.result["element_type"]
                step.text = dialog.result["text"]
                step.text_match = dialog.result["text_match"]
                step.button = dialog.result.get("button", "left")
                step.move_duration = dialog.result.get("move_duration", 0.3)

            # Handle actions that need find block
            elif step.action_type in ["right_click", "double_click", "middle_click", "text", "drag", "key", "hotkey"]:
                step.element_type = dialog.result["element_type"]
                step.text = dialog.result["text"]
                step.text_match = dialog.result["text_match"]
                step.action_config = dialog.result["action_config"]

            # Handle actions without find block
            elif "action_config" in dialog.result:
                step.action_config = dialog.result["action_config"]

            self.refresh_steps_list()
            self.status_text.set(f"Updated step {step.step_number}")

    def remove_step(self):
        """Remove selected step."""
        selection = self.steps_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a step to remove!")
            return

        idx = selection[0]
        self.workflow_steps.pop(idx)

        # Renumber steps
        for i, step in enumerate(self.workflow_steps):
            step.step_number = i + 1

        self.refresh_steps_list()
        self.status_text.set("Step removed")

    def move_step_up(self):
        """Move step up in list."""
        selection = self.steps_listbox.curselection()
        if not selection or selection[0] == 0:
            return

        idx = selection[0]
        self.workflow_steps[idx], self.workflow_steps[idx-1] = \
            self.workflow_steps[idx-1], self.workflow_steps[idx]

        # Renumber
        for i, step in enumerate(self.workflow_steps):
            step.step_number = i + 1

        self.refresh_steps_list()
        self.steps_listbox.selection_set(idx - 1)

    def move_step_down(self):
        """Move step down in list."""
        selection = self.steps_listbox.curselection()
        if not selection or selection[0] == len(self.workflow_steps) - 1:
            return

        idx = selection[0]
        self.workflow_steps[idx], self.workflow_steps[idx+1] = \
            self.workflow_steps[idx+1], self.workflow_steps[idx]

        # Renumber
        for i, step in enumerate(self.workflow_steps):
            step.step_number = i + 1

        self.refresh_steps_list()
        self.steps_listbox.selection_set(idx + 1)

    def copy_step(self):
        """Copy selected step to clipboard."""
        selection = self.steps_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a step to copy!")
            return

        idx = selection[0]
        step = self.workflow_steps[idx]

        # Deep copy the step
        import copy
        self.copied_step = copy.deepcopy(step)
        self.status_text.set(f"Copied step: {step.description}")

    def paste_step(self):
        """Paste copied step."""
        if not self.copied_step:
            messagebox.showwarning("Warning", "No step copied! Please copy a step first.")
            return

        import copy
        # Deep copy to avoid reference issues
        new_step = copy.deepcopy(self.copied_step)

        # Get the position to insert (after current selection, or at end)
        selection = self.steps_listbox.curselection()
        if selection:
            insert_idx = selection[0] + 1
        else:
            insert_idx = len(self.workflow_steps)

        # Insert the new step
        self.workflow_steps.insert(insert_idx, new_step)

        # Renumber all steps
        for i, step in enumerate(self.workflow_steps):
            step.step_number = i + 1

        self.refresh_steps_list()
        self.steps_listbox.selection_set(insert_idx)
        self.status_text.set(f"Pasted step: {new_step.description}")

    def refresh_steps_list(self):
        """Refresh steps listbox."""
        self.steps_listbox.delete(0, tk.END)
        for step in self.workflow_steps:
            optional_tag = " [Optional]" if getattr(step, 'optional', False) else ""
            display_text = f"{step.step_number}. {step.description or '[No description]'}{optional_tag}"
            self.steps_listbox.insert(tk.END, display_text)

    def test_action(self):
        """Test selected action on SUT."""
        selection = self.steps_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a step to test!")
            return

        if not self.network:
            messagebox.showwarning("Warning", "Please connect to SUT first!")
            return

        idx = selection[0]
        step = self.workflow_steps[idx]

        try:
            # Handle find_and_click actions
            if step.action_type == "find_and_click":
                if step.selected_bbox:
                    # Use bbox coordinates if available
                    bbox = step.selected_bbox
                    x = bbox.x + bbox.width // 2
                    y = bbox.y + bbox.height // 2
                else:
                    # Find element using vision model (for loaded YAML)
                    if not self.vision_model or not self.current_screenshot:
                        messagebox.showwarning("Warning", "Please capture and parse a screenshot first to test this action!")
                        return

                    # Find the element
                    bboxes = self.vision_model.detect_ui_elements(self.current_screenshot)

                    # Find matching bbox
                    found_bbox = None
                    for bbox in bboxes:
                        if step.element_type in ["any", bbox.element_type]:
                            if hasattr(step, 'text_match') and step.text_match == "contains":
                                if step.text.lower() in bbox.element_text.lower():
                                    found_bbox = bbox
                                    break
                            elif hasattr(step, 'text_match') and step.text_match == "exact":
                                if step.text.lower() == bbox.element_text.lower():
                                    found_bbox = bbox
                                    break
                            elif step.text.lower() in bbox.element_text.lower():
                                found_bbox = bbox
                                break

                    if not found_bbox:
                        messagebox.showerror("Error", f"Could not find element with text '{step.text}'")
                        return

                    x = found_bbox.x + found_bbox.width // 2
                    y = found_bbox.y + found_bbox.height // 2

                action = {
                    "type": "click",
                    "x": x,
                    "y": y,
                    "button": getattr(step, 'button', 'left')
                }

                self.network.send_action(action)
                self.status_text.set(f"Tested step {step.step_number}: Click at ({x}, {y})")
                messagebox.showinfo("Success", f"Action executed on SUT at ({x}, {y})!")

            # Handle other actions with action_config
            elif step.action_config:
                self.network.send_action(step.action_config)
                self.status_text.set(f"Tested step {step.step_number}")
                messagebox.showinfo("Success", "Action executed on SUT!")

            else:
                messagebox.showwarning("Warning", "This step has no executable action configured!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to test action: {str(e)}")

    def open_screenshots_folder(self):
        """Open the screenshots folder in file explorer."""
        import os
        import subprocess

        # Get current working directory (where screenshots are saved)
        screenshots_dir = os.getcwd()

        try:
            # Open folder in Windows Explorer
            subprocess.Popen(f'explorer "{screenshots_dir}\\workflow_builder_temp"')
            self.status_text.set(f"Opened folder: {screenshots_dir}\\workflow_builder_temp")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open folder: {str(e)}")

    def show_recent_files(self):
        """Show recent files dialog."""
        recent_files = self.config_manager.get_recent_workflows()
        
        if not recent_files:
            messagebox.showinfo("No Recent Files", "No recent workflow files found.")
            return
        
        dialog = RecentFilesDialog(self.root, recent_files)
        self.root.wait_window(dialog)
        
        if dialog.result == "CLEAR":
            # Clear recent files
            self.config_manager.config["recent_workflows"] = []
            self.config_manager.save_config()
            self.status_text.set("Recent files cleared")
        elif dialog.result:
            # Load selected file
            self.load_yaml_file(dialog.result)

    def save_yaml(self):
        """Save workflow to current file or prompt for new file."""
        if hasattr(self, 'current_file') and self.current_file:
            self.save_yaml_file(self.current_file)
        else:
            self.save_yaml_as()

    def save_yaml_as(self):
        """Save workflow to new YAML file."""
        if not self.workflow_steps:
            messagebox.showwarning("Warning", "No steps to save!")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")],
            initialdir="config/games"
        )

        if filename:
            self.save_yaml_file(filename)

    def save_yaml_file(self, filename: str):
        """Save workflow to specified YAML file."""
        try:
            # Build YAML structure
            metadata = {
                "game_name": self.game_name.get(),
                "version": self.version.get(),
                "created_with": "Katana Workflow Builder",
                "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # Add optional fields if they have values
            if self.game_path.get():
                metadata["path"] = self.game_path.get()
            if self.process_id.get():
                metadata["process_id"] = self.process_id.get()
            if self.process_name.get():
                metadata["process_name"] = self.process_name.get()
            if self.benchmark_name.get():
                metadata["benchmark_name"] = self.benchmark_name.get()
            if self.benchmark_duration.get():
                try:
                    metadata["benchmark_duration"] = int(self.benchmark_duration.get())
                except ValueError:
                    pass
            if self.startup_wait.get():
                try:
                    metadata["startup_wait"] = int(self.startup_wait.get())
                except ValueError:
                    pass
            if self.resolution.get():
                metadata["resolution"] = self.resolution.get()
            if self.preset.get():
                metadata["preset"] = self.preset.get()
            if self.engine.get():
                metadata["engine"] = self.engine.get()
            if self.graphics_api.get():
                metadata["graphics_api"] = self.graphics_api.get()

            yaml_data = {
                "metadata": metadata,
                "steps": {}
            }

            for step in self.workflow_steps:
                yaml_data["steps"][step.step_number] = step.to_dict()

            # Add fallback
            yaml_data["fallbacks"] = {
                "general": {
                    "action": "key",
                    "key": "escape",
                    "expected_delay": 1
                }
            }

            # Save
            with open(filename, 'w') as f:
                yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

            # Update current file and recent files
            self.current_file = filename
            self.config_manager.add_recent_workflow(filename)
            
            # Update window title
            self.root.title(f"Katana Workflow Builder - {os.path.basename(filename)}")

            self.status_text.set(f"Saved workflow to {filename}")
            messagebox.showinfo("Success", f"Workflow saved to:\n{filename}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save YAML: {str(e)}")

    def load_yaml(self):
        """Load workflow from YAML file."""
        filename = filedialog.askopenfilename(
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")],
            initialdir="config/games"
        )

        if filename:
            self.load_yaml_file(filename)

    def load_yaml_file(self, filename: str):
        """Load workflow from specified YAML file."""
        try:
            with open(filename, 'r') as f:
                yaml_data = yaml.safe_load(f)

            # Load metadata
            metadata = yaml_data.get("metadata", {})
            self.game_name.set(metadata.get("game_name", "Loaded Game"))
            self.version.set(metadata.get("version", "1.0"))
            self.game_path.set(metadata.get("path", ""))
            self.process_id.set(metadata.get("process_id", ""))
            self.process_name.set(metadata.get("process_name", ""))
            self.benchmark_name.set(metadata.get("benchmark_name", ""))
            self.benchmark_duration.set(str(metadata.get("benchmark_duration", 120)))
            self.startup_wait.set(str(metadata.get("startup_wait", 30)))
            self.resolution.set(metadata.get("resolution", "1920x1080"))
            self.preset.set(metadata.get("preset", "High"))
            self.engine.set(metadata.get("engine", ""))
            self.graphics_api.set(metadata.get("graphics_api", "DirectX 11"))

            # Load steps
            self.workflow_steps = []
            steps = yaml_data.get("steps", {})

            for step_num, step_data in steps.items():
                step = WorkflowStep(int(step_num))
                step.description = step_data.get("description", "")
                step.expected_delay = step_data.get("expected_delay", 2)
                step.timeout = step_data.get("timeout", 20)
                step.optional = step_data.get("optional", False)

                # Support new format: find + action blocks
                if "find" in step_data and "action" in step_data:
                    find_block = step_data["find"]
                    action_block = step_data["action"]

                    # If action is click, treat as find_and_click
                    if action_block.get("type") == "click":
                        step.action_type = "find_and_click"
                        step.element_type = find_block.get("type", "icon")
                        step.text = find_block.get("text", "")
                        step.text_match = find_block.get("text_match", "contains")
                    else:
                        step.action_config = action_block
                        if isinstance(step.action_config, dict):
                            step.action_type = step.action_config.get("type", "custom")
                # Support old format: find_and_click (backward compatibility)
                elif "find_and_click" in step_data:
                    step.action_type = "find_and_click"
                    fac = step_data["find_and_click"]
                    step.element_type = fac.get("type", "icon")
                    step.text = fac.get("text", "")
                    step.text_match = fac.get("text_match", "contains")
                # Only action block (e.g., wait, key press)
                elif "action" in step_data:
                    step.action_config = step_data["action"]
                    if isinstance(step.action_config, dict):
                        step.action_type = step.action_config.get("type", "custom")
                    else:
                        step.action_type = "wait"

                self.workflow_steps.append(step)

            # Update current file and recent files
            self.current_file = filename
            self.config_manager.add_recent_workflow(filename)
            
            # Update window title
            self.root.title(f"Katana Workflow Builder - {os.path.basename(filename)}")

            self.refresh_steps_list()
            self.status_text.set(f"Loaded {len(self.workflow_steps)} steps from {filename}")
            messagebox.showinfo("Success", f"Loaded workflow from:\n{filename}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load YAML: {str(e)}")

    def new_workflow(self):
        """Create new workflow."""
        if self.workflow_steps and not messagebox.askyesno("Confirm",
            "This will clear the current workflow. Continue?"):
            return

        self.workflow_steps = []
        self.refresh_steps_list()

        # Reset current file
        self.current_file = None
        self.root.title("Katana Workflow Builder")

        # Reset all metadata fields to defaults
        defaults = self.config_manager.config["defaults"]
        self.game_name.set(defaults["game_name"])
        self.version.set(defaults["version"])
        self.game_path.set("")
        self.process_id.set("")
        self.process_name.set("")
        self.benchmark_name.set("")
        self.benchmark_duration.set(defaults["benchmark_duration"])
        self.startup_wait.set(defaults["startup_wait"])
        self.resolution.set(defaults["resolution"])
        self.preset.set(defaults["preset"])
        self.engine.set("")
        self.graphics_api.set(defaults["graphics_api"])

        self.status_text.set("New workflow created")

    def save_configuration(self):
        """Save current application configuration."""
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir="config",
            title="Save Configuration"
        )

        if not filename:
            return

        try:
            config_data = {
                "connections": {
                    "sut": {
                        "ip": self.sut_ip.get(),
                        "port": self.sut_port.get()
                    },
                    "omniparser": {
                        "ip": self.omniparser_ip.get(),
                        "port": self.omniparser_port.get()
                    },
                    "gemma": {
                        "ip": self.gemma_ip.get(),
                        "port": self.gemma_port.get()
                    },
                    "vision_model": self.vision_var.get()
                },
                "defaults": {
                    "game_name": self.game_name.get(),
                    "version": self.version.get(),
                    "benchmark_duration": self.benchmark_duration.get(),
                    "startup_wait": self.startup_wait.get(),
                    "resolution": self.resolution.get(),
                    "preset": self.preset.get(),
                    "graphics_api": self.graphics_api.get()
                },
                "window": {
                    "geometry": self.root.geometry(),
                    "maximized": self.root.state() == 'zoomed'
                }
            }

            with open(filename, 'w') as f:
                json.dump(config_data, f, indent=2)

            self.status_text.set(f"Configuration saved to {filename}")
            messagebox.showinfo("Success", f"Configuration saved to:\n{filename}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save configuration: {str(e)}")

    def load_configuration(self):
        """Load application configuration."""
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir="config",
            title="Load Configuration"
        )

        if not filename:
            return

        try:
            with open(filename, 'r') as f:
                config_data = json.load(f)

            # Load connection settings
            connections = config_data.get("connections", {})
            
            sut_config = connections.get("sut", {})
            self.sut_ip.set(sut_config.get("ip", "192.168.50.217"))
            self.sut_port.set(sut_config.get("port", "8080"))

            omni_config = connections.get("omniparser", {})
            self.omniparser_ip.set(omni_config.get("ip", "192.168.50.241"))
            self.omniparser_port.set(omni_config.get("port", "8000"))

            gemma_config = connections.get("gemma", {})
            self.gemma_ip.set(gemma_config.get("ip", "192.168.50.241"))
            self.gemma_port.set(gemma_config.get("port", "1234"))

            # Load vision model selection
            vision_model = connections.get("vision_model", "omniparser")
            self.vision_var.set(vision_model)
            self.on_vision_model_change()

            # Load defaults
            defaults = config_data.get("defaults", {})
            self.game_name.set(defaults.get("game_name", "My Game"))
            self.version.set(defaults.get("version", "1.0"))
            self.benchmark_duration.set(defaults.get("benchmark_duration", "120"))
            self.startup_wait.set(defaults.get("startup_wait", "30"))
            self.resolution.set(defaults.get("resolution", "1920x1080"))
            self.preset.set(defaults.get("preset", "High"))
            self.graphics_api.set(defaults.get("graphics_api", "DirectX 11"))

            # Load window settings
            window_config = config_data.get("window", {})
            geometry = window_config.get("geometry", "1600x900")
            maximized = window_config.get("maximized", False)
            
            self.root.geometry(geometry)
            if maximized:
                self.root.state('zoomed')

            self.status_text.set(f"Configuration loaded from {filename}")
            messagebox.showinfo("Success", f"Configuration loaded from:\n{filename}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load configuration: {str(e)}")

    def center_window(self):
        """Center window on screen."""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')


def main():
    """Main entry point."""
    root = tk.Tk()
    app = WorkflowBuilderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
