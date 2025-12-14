"""
Interactive Workflow Builder GUI for Game Automation
Build, test, and export game automation workflows visually

Features:
- Game control and screenshot capture
- Interactive bounding box selection
- Visual workflow step management
- Action definition and testing
- YAML export for automation
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


class CollapsibleFrame(ttk.Frame):
    """A frame that can be collapsed/expanded with a toggle button."""
    
    def __init__(self, parent, title="", collapsed=False, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.collapsed = collapsed
        self.title = title
        
        # Header frame with toggle button
        self.header = ttk.Frame(self)
        self.header.pack(fill=tk.X)
        
        # Toggle button with arrow indicator
        self.toggle_btn = ttk.Button(
            self.header, 
            text=f"{'▶' if collapsed else '▼'} {title}",
            command=self.toggle,
            style="Toolbutton"
        )
        self.toggle_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Content frame
        self.content = ttk.Frame(self)
        if not collapsed:
            self.content.pack(fill=tk.BOTH, expand=True)
    
    def toggle(self):
        """Toggle collapsed state."""
        self.collapsed = not self.collapsed
        
        if self.collapsed:
            self.content.pack_forget()
            self.toggle_btn.config(text=f"▶ {self.title}")
        else:
            self.content.pack(fill=tk.BOTH, expand=True)
            self.toggle_btn.config(text=f"▼ {self.title}")
    
    def collapse(self):
        """Collapse the frame."""
        if not self.collapsed:
            self.toggle()
    
    def expand(self):
        """Expand the frame."""
        if self.collapsed:
            self.toggle()




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
    """Canvas with interactive bounding boxes and zoom support."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.image = None
        self.photo = None
        self.bboxes = []
        self.selected_bbox = None
        self.bbox_items = {}
        self.callback = None
        self.zoom_scale = 1.0  # 1.0 = 100%

        self.bind("<Button-1>", self.on_click)
        self.bind("<Motion>", self.on_hover)

    def set_zoom(self, scale: float):
        """Set zoom scale and redraw."""
        self.zoom_scale = scale
        if self.image:
            self.draw_bboxes()

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
        """Draw bounding boxes on image with zoom applied."""
        if not self.image:
            return

        # Apply zoom to create scaled image
        scale = self.zoom_scale
        new_width = int(self.image.width * scale)
        new_height = int(self.image.height * scale)
        
        if scale != 1.0:
            img_scaled = self.image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        else:
            img_scaled = self.image.copy()

        # Create a copy for drawing
        draw = ImageDraw.Draw(img_scaled, 'RGBA')

        # Try to load a font (scaled)
        try:
            font_size = max(8, int(12 * scale))
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            font = ImageFont.load_default()

        # Draw each bounding box (scaled)
        self.bbox_items = {}
        for i, bbox in enumerate(self.bboxes):
            # Scale coordinates
            x1, y1 = int(bbox.x * scale), int(bbox.y * scale)
            x2, y2 = int((bbox.x + bbox.width) * scale), int((bbox.y + bbox.height) * scale)

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

            # Store scaled bbox coordinates for click detection
            self.bbox_items[i] = (x1, y1, x2, y2)

        # Convert to PhotoImage
        self.photo = ImageTk.PhotoImage(img_scaled)

        # Update canvas size
        self.delete("all")
        self.config(width=new_width, height=new_height,
                   scrollregion=(0, 0, new_width, new_height))
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
        self.geometry("750x1100")
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
        ttk.Entry(desc_frame, textvariable=self.desc_var, width=60, font=('TkDefaultFont', 12)).pack(fill=tk.X)
        ttk.Label(desc_frame, text="What does this step do? (e.g., 'Click PLAY button', 'Enter username')",
                 font=('TkDefaultFont', 10), foreground="gray").pack(anchor=tk.W, pady=(2,0))

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
                ("Hold Key", "hold_key", "Press and HOLD key for duration"),
                ("Key Combo", "hotkey", "Press keys together (Ctrl+S)"),
                ("Type Text", "text", "Type text into field")
            ],
            # Column 3: Other actions
            [
                ("🖱️ Other Actions", None, "header"),
                ("Hold Click", "hold_click", "Click and HOLD for duration"),
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
                             font=('TkDefaultFont', 9), foreground="gray").pack(anchor=tk.W, padx=(20,0))

        # === FIND ELEMENT (for actions that need it) ===
        self.element_frame = ttk.LabelFrame(scrollable_frame, text="Find Element - Which element to interact with?", padding=10)

        ttk.Label(self.element_frame, text="Element Type:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.type_var = tk.StringVar(value=self.bbox.element_type if self.bbox else "icon")
        type_combo = ttk.Combobox(self.element_frame, textvariable=self.type_var,
                    values=["icon", "text", "any"], state="readonly", width=15)
        type_combo.grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.element_frame, text="icon = button/clickable, text = label, any = both",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=5)

        ttk.Label(self.element_frame, text="Text Content:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.text_var = tk.StringVar(value=self.bbox.element_text if self.bbox else "")
        ttk.Entry(self.element_frame, textvariable=self.text_var, width=35, font=('TkDefaultFont', 11)).grid(
            row=1, column=1, columnspan=2, sticky=tk.W, pady=5)
        ttk.Label(self.element_frame, text="The text visible on the element (e.g., 'PLAY', 'Submit', 'OK')",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=2, column=1, columnspan=2, sticky=tk.W)

        ttk.Label(self.element_frame, text="Match Method:").grid(row=3, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.match_var = tk.StringVar(value="contains")
        match_combo = ttk.Combobox(self.element_frame, textvariable=self.match_var,
                    values=["contains", "exact", "startswith", "endswith"],
                    state="readonly", width=15)
        match_combo.grid(row=3, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.element_frame, text="'contains' is most flexible",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=3, column=2, sticky=tk.W, padx=5)

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
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)

        # Key press options
        self.key_frame = ttk.LabelFrame(scrollable_frame, text="Key Press - Which key?", padding=10)
        ttk.Label(self.key_frame, text="Key Name:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.key_var = tk.StringVar(value="enter")
        ttk.Entry(self.key_frame, textvariable=self.key_var, width=25).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.key_frame, text="Common: enter, escape, tab, space, f1-f12, up, down, left, right",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Hotkey options
        self.hotkey_frame = ttk.LabelFrame(scrollable_frame, text="Key Combination - Which keys together?", padding=10)
        ttk.Label(self.hotkey_frame, text="Keys (comma-separated):").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.hotkey_var = tk.StringVar(value="ctrl, s")
        ttk.Entry(self.hotkey_frame, textvariable=self.hotkey_var, width=25).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.hotkey_frame, text="Examples: ctrl, s  or  ctrl, shift, esc  or  alt, f4",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

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
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

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
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Wait options
        self.wait_frame = ttk.LabelFrame(scrollable_frame, text="Wait Duration", padding=10)
        ttk.Label(self.wait_frame, text="Wait Time (seconds):").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.duration_var = tk.StringVar(value="2")
        ttk.Entry(self.wait_frame, textvariable=self.duration_var, width=10).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.wait_frame, text="How many seconds to pause",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Hold Key options
        self.hold_key_frame = ttk.LabelFrame(scrollable_frame, text="Hold Key - Press and hold for duration", padding=10)
        ttk.Label(self.hold_key_frame, text="Key Name:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.hold_key_var = tk.StringVar(value="enter")
        ttk.Entry(self.hold_key_frame, textvariable=self.hold_key_var, width=25).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.hold_key_frame, text="Hold Duration (seconds):").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.hold_key_duration_var = tk.StringVar(value="2")
        ttk.Entry(self.hold_key_frame, textvariable=self.hold_key_duration_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.hold_key_frame, text="Common: enter, escape, space, f1-f12. For benchmark buttons, try 2-5 seconds.",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # Hold Click options
        self.hold_click_frame = ttk.LabelFrame(scrollable_frame, text="Hold Click - Click and hold for duration", padding=10)
        ttk.Label(self.hold_click_frame, text="Mouse Button:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.hold_click_button_var = tk.StringVar(value="left")
        ttk.Combobox(self.hold_click_frame, textvariable=self.hold_click_button_var,
                    values=["left", "right", "middle"], state="readonly", width=12).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.hold_click_frame, text="Hold Duration (seconds):").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.hold_click_duration_var = tk.StringVar(value="2")
        ttk.Entry(self.hold_click_frame, textvariable=self.hold_click_duration_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(self.hold_click_frame, text="Click coordinates will use selected element or bbox center",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=(5,0))

        # === VERIFY SUCCESS ===
        self.verify_frame = ttk.LabelFrame(scrollable_frame, text="Verify Success (Optional) - Check if action worked", padding=10)
        self.verify_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(self.verify_frame, text="Add elements that should appear after this action completes:",
                 font=('TkDefaultFont', 10), foreground="gray").pack(anchor=tk.W, pady=2)

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
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=5)

        ttk.Label(timing_frame, text="Timeout:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.timeout_var = tk.StringVar(value="20")
        ttk.Entry(timing_frame, textvariable=self.timeout_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(timing_frame, text="seconds - Maximum time to wait for element to appear",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)

        ttk.Label(timing_frame, text="Optional Step:").grid(row=2, column=0, sticky=tk.W, pady=5, padx=(5,10))
        self.optional_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(timing_frame, text="Continue workflow even if this step fails",
                       variable=self.optional_var).grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=5)
        ttk.Label(timing_frame, text="If checked, failures in this step won't stop the workflow",
                 font=('TkDefaultFont', 9), foreground="gray").grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=5)

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
        self.hold_key_frame.pack_forget()
        self.hold_click_frame.pack_forget()

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
        elif action == "hold_key":
            self.hold_key_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
        elif action == "hold_click":
            self.element_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)
            self.hold_click_frame.pack(fill=tk.X, padx=10, pady=5, before=self.verify_frame)

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
            elif action == "hold_key":
                # Hold a key for specified duration
                key = self.hold_key_var.get().strip()
                duration_value = self.hold_key_duration_var.get().strip()
                try:
                    duration = float(duration_value) if duration_value else 2.0
                except ValueError:
                    duration = 2.0
                
                self.result["action_config"] = {
                    "type": "hold_key",
                    "key": key,
                    "duration": duration
                }
            elif action == "hold_click":
                # Hold click at element position for specified duration
                self.result["element_type"] = self.type_var.get()
                self.result["text"] = self.text_var.get()
                self.result["text_match"] = self.match_var.get()
                
                button = self.hold_click_button_var.get()
                duration_value = self.hold_click_duration_var.get().strip()
                try:
                    duration = float(duration_value) if duration_value else 2.0
                except ValueError:
                    duration = 2.0
                
                self.result["action_config"] = {
                    "type": "hold_click",
                    "button": button,
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


class WorkflowBuilderGUI:
    """Main Workflow Builder GUI Application."""

    def __init__(self, root):
        self.root = root
        self.root.title("Katana Workflow Builder")
        
        # Enable DPI awareness on Windows
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass
        
        # Dynamic window size based on screen (85% of screen)
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        win_width = int(screen_width * 0.85)
        win_height = int(screen_height * 0.85)
        self.root.geometry(f"{win_width}x{win_height}")
        
        # Store scale factor for font sizing
        self.scale_factor = max(1.0, screen_height / 1080)  # Relative to 1080p, minimum 1.0
        
        # Configure global fonts for better readability
        import tkinter.font as tkfont
        base_size = int(11 * self.scale_factor)  # Base font size 11pt
        self.fonts = {
            'default': ('Segoe UI', base_size),
            'bold': ('Segoe UI', base_size, 'bold'),
            'small': ('Segoe UI', int(base_size * 0.85)),
            'large': ('Segoe UI', int(base_size * 1.2), 'bold'),
            'mono': ('Consolas', base_size),
            'icon': ('Segoe UI Symbol', int(base_size * 1.3)),  # Larger icons
        }
        
        # Apply default font to all widgets
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Segoe UI", size=base_size)
        root.option_add("*Font", default_font)

        # State
        self.current_screenshot = None
        self.current_bboxes = []
        self.workflow_steps = []
        self.network = None
        self.screenshot_mgr = None
        self.vision_model = None

        # SUT connection
        self.sut_ip = tk.StringVar(value="192.168.50.217") #Razer laptop
        self.sut_port = tk.StringVar(value="8080")

        # Vision model connections
        self.omniparser_ip = tk.StringVar(value="localhost")
        self.omniparser_port = tk.StringVar(value="8000")
        self.gemma_ip = tk.StringVar(value="localhost")
        self.gemma_port = tk.StringVar(value="1234")

        # Store connection states for each model
        self.omniparser_connection = None
        self.gemma_connection = None

        # Metadata variables
        self.game_name = tk.StringVar(value="My Game")
        self.game_path = tk.StringVar(value="")
        self.process_id = tk.StringVar(value="")
        self.process_name = tk.StringVar(value="")
        self.version = tk.StringVar(value="1.0")
        self.benchmark_duration = tk.StringVar(value="120")
        self.startup_wait = tk.StringVar(value="30")
        self.resolution = tk.StringVar(value="1920x1080")
        self.preset = tk.StringVar(value="High")
        self.benchmark_name = tk.StringVar(value="")
        self.engine = tk.StringVar(value="")
        self.graphics_api = tk.StringVar(value="DirectX 11")

        # Clipboard for copy/paste
        self.copied_step = None

        # Zoom levels for screenshot (cycles through these)
        self.zoom_levels = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        self.zoom_index = 5  # Start at 100%

        # Screenshot history for ribbon (list of dicts with 'captured' and 'parsed' paths)
        self.screenshot_history = []
        self.history_thumbnails = []  # Keep references to avoid garbage collection

        # Flow control
        self.flow_running = False
        self.flow_stop_requested = False

        self.create_widgets()
        self.center_window()

    def create_widgets(self):
        """Create main GUI widgets."""
        # Enable resizing
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New Workflow", command=self.new_workflow)
        file_menu.add_command(label="Open YAML", command=self.load_yaml)
        file_menu.add_command(label="Save YAML", command=self.save_yaml)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        # Main container
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # LEFT PANEL - Screenshot and Controls (larger)
        left_panel = ttk.Frame(main_paned)
        main_paned.add(left_panel, weight=4)

        # COLLAPSIBLE Control Panel
        self.control_panel = CollapsibleFrame(left_panel, title="Model and SUT connections")
        self.control_panel.pack(fill=tk.X, padx=5, pady=2)
        control_frame = self.control_panel.content

        # SUT connection (compact single row)
        # SUT connection (compact single row)
        sut_frame = ttk.Frame(control_frame)
        sut_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(sut_frame, text="SUT:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(sut_frame, textvariable=self.sut_ip, width=14).pack(side=tk.LEFT, padx=2)
        ttk.Label(sut_frame, text=":").pack(side=tk.LEFT)
        ttk.Entry(sut_frame, textvariable=self.sut_port, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(sut_frame, text="Connect", command=self.connect_sut, width=8).pack(side=tk.LEFT, padx=3)
        self.sut_status_label = ttk.Label(sut_frame, text="●", foreground="red", font=('TkDefaultFont', 14))
        self.sut_status_label.pack(side=tk.LEFT, padx=2)

        # Vision model connection (compact)
        vision_frame = ttk.Frame(control_frame)
        vision_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(vision_frame, text="Vision:").pack(side=tk.LEFT, padx=2)
        self.vision_var = tk.StringVar(value="omniparser")
        ttk.Radiobutton(vision_frame, text="Omni", variable=self.vision_var,
                       value="omniparser", command=self.on_vision_model_change).pack(side=tk.LEFT)
        ttk.Radiobutton(vision_frame, text="Gemma", variable=self.vision_var,
                       value="gemma", command=self.on_vision_model_change).pack(side=tk.LEFT, padx=(0,5))

        # Omniparser connection entry
        self.omni_frame = ttk.Frame(vision_frame)
        self.omni_frame.pack(side=tk.LEFT)
        ttk.Entry(self.omni_frame, textvariable=self.omniparser_ip, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.omni_frame, text=":").pack(side=tk.LEFT)
        ttk.Entry(self.omni_frame, textvariable=self.omniparser_port, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.omni_frame, text="Connect", command=self.connect_vision_model, width=8).pack(side=tk.LEFT, padx=3)
        self.vision_status_label = ttk.Label(self.omni_frame, text="●", foreground="red", font=('TkDefaultFont', 14))
        self.vision_status_label.pack(side=tk.LEFT, padx=2)

        # Gemma connection (hidden by default)
        self.gemma_frame = ttk.Frame(vision_frame)
        ttk.Entry(self.gemma_frame, textvariable=self.gemma_ip, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.gemma_frame, text=":").pack(side=tk.LEFT)
        ttk.Entry(self.gemma_frame, textvariable=self.gemma_port, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(self.gemma_frame, text="Connect", command=self.connect_vision_model, width=8).pack(side=tk.LEFT, padx=3)
        self.gemma_status_label = ttk.Label(self.gemma_frame, text="●", foreground="red", font=('TkDefaultFont', 14))
        self.gemma_status_label.pack(side=tk.LEFT, padx=2)

        # Action buttons (compact)
        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(fill=tk.X, pady=3)

        ttk.Button(btn_frame, text="📷 Capture", command=self.capture_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🔍 Parse", command=self.parse_screenshot).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📁 Folder", command=self.open_screenshots_folder).pack(side=tk.LEFT, padx=2)
        
        # Zoom button
        self.zoom_btn_text = tk.StringVar(value="🔎 100%")
        self.zoom_btn = ttk.Button(btn_frame, textvariable=self.zoom_btn_text, command=self.cycle_zoom, width=8)
        self.zoom_btn.pack(side=tk.LEFT, padx=5)

        # Screenshot display
        self.screenshot_label_text = tk.StringVar(value="Screenshot (Click on elements) - Zoom: 100%")
        screenshot_frame = ttk.LabelFrame(left_panel, text="Screenshot (Click on elements)", padding=5)
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

        # RIGHT PANEL - Workflow steps and element details (narrower)
        right_panel = ttk.Frame(main_paned)
        main_paned.add(right_panel, weight=1)

        # COLLAPSIBLE Selected Element Panel
        self.details_panel = CollapsibleFrame(right_panel, title="Selected Element")
        self.details_panel.pack(fill=tk.X, padx=5, pady=2)
        
        self.details_text = scrolledtext.ScrolledText(self.details_panel.content, height=5, width=40)
        self.details_text.pack(fill=tk.BOTH, expand=True)

        # COLLAPSIBLE Workflow Steps Panel (smaller height)
        self.steps_panel = CollapsibleFrame(right_panel, title="Workflow Steps")
        self.steps_panel.pack(fill=tk.X, padx=5, pady=2)
        steps_content = self.steps_panel.content

        # Step management buttons (single row, compact)
        step_btn_frame = ttk.Frame(steps_content)
        step_btn_frame.pack(fill=tk.X, pady=2)

        ttk.Button(step_btn_frame, text="+Add", command=self.add_step, width=6).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_btn_frame, text="Edit", command=self.edit_step, width=5).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_btn_frame, text="Del", command=self.remove_step, width=4).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_btn_frame, text="▲", command=self.move_step_up, width=2).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_btn_frame, text="▼", command=self.move_step_down, width=2).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_btn_frame, text="Copy", command=self.copy_step, width=5).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_btn_frame, text="Paste", command=self.paste_step, width=5).pack(side=tk.LEFT, padx=1)
        
        # Test buttons with green styling
        tk.Button(step_btn_frame, text="▶ Test", command=self.test_action, width=7,
                  bg="#4CAF50", fg="white", font=('TkDefaultFont', 11, 'bold'),
                  relief=tk.RAISED, bd=2).pack(side=tk.LEFT, padx=3)
        self.flow_btn = tk.Button(step_btn_frame, text="▶▶ Flow", command=self.toggle_flow, width=8,
                  bg="#2E7D32", fg="white", font=('TkDefaultFont', 11, 'bold'),
                  relief=tk.RAISED, bd=2)
        self.flow_btn.pack(side=tk.LEFT, padx=3)

        # Steps list
        list_frame = ttk.Frame(steps_content)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.steps_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=("Consolas", 11), height=20)
        self.steps_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.steps_listbox.yview)

        # COLLAPSIBLE Metadata Panel (expanded, takes remaining space)
        self.metadata_panel = CollapsibleFrame(right_panel, title="Workflow Metadata", collapsed=False)
        self.metadata_panel.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        meta_content = self.metadata_panel.content

        # Create canvas and scrollbar for metadata
        meta_canvas = tk.Canvas(meta_content, height=200)
        meta_scrollbar = ttk.Scrollbar(meta_content, orient="vertical", command=meta_canvas.yview)
        meta_scrollable = ttk.Frame(meta_canvas)

        meta_scrollable.bind(
            "<Configure>",
            lambda e: meta_canvas.configure(scrollregion=meta_canvas.bbox("all"))
        )

        meta_canvas.create_window((0, 0), window=meta_scrollable, anchor="nw")
        meta_canvas.configure(yscrollcommand=meta_scrollbar.set)

        meta_canvas.pack(side="left", fill="both", expand=True)
        meta_scrollbar.pack(side="right", fill="y")

        # Compact metadata fields with Test/Kill button for Game Path
        row = 0
        
        # Regular fields before Game Path
        for label, var, hint in [
            ("Game Name:", self.game_name, ""),
            ("Version:", self.version, ""),
            ("Engine:", self.engine, "UE5, Source 2"),
        ]:
            ttk.Label(meta_scrollable, text=label).grid(row=row, column=0, sticky=tk.W, pady=1, padx=2)
            ttk.Entry(meta_scrollable, textvariable=var, width=25).grid(row=row, column=1, sticky=tk.W, pady=1)
            if hint:
                ttk.Label(meta_scrollable, text=hint, font=('TkDefaultFont', 9), foreground="gray").grid(
                    row=row, column=2, sticky=tk.W, padx=3)
            row += 1
        
        # Game Path with Test/Kill button
        ttk.Label(meta_scrollable, text="Game Path:").grid(row=row, column=0, sticky=tk.W, pady=1, padx=2)
        game_path_frame = ttk.Frame(meta_scrollable)
        game_path_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=1)
        ttk.Entry(game_path_frame, textvariable=self.game_path, width=20).pack(side=tk.LEFT)
        
        # Test/Kill button (starts as "▶ Test")
        self.game_running = False
        self.test_kill_btn = tk.Button(
            game_path_frame, text="▶ Launch Game", width=12, 
            command=self.toggle_game_test,
            bg="#4CAF50", fg="white", font=('TkDefaultFont', 10, 'bold')
        )
        self.test_kill_btn.pack(side=tk.LEFT, padx=3)
        row += 1
        
        # Process Name field (needed for kill)
        ttk.Label(meta_scrollable, text="Process Name:").grid(row=row, column=0, sticky=tk.W, pady=1, padx=2)
        ttk.Entry(meta_scrollable, textvariable=self.process_name, width=25).grid(row=row, column=1, sticky=tk.W, pady=1)
        ttk.Label(meta_scrollable, text="for Kill (e.g. cs2.exe)", font=('TkDefaultFont', 9), foreground="gray").grid(
            row=row, column=2, sticky=tk.W, padx=3)
        row += 1
        
        # Remaining fields
        for label, var, hint in [
            ("Process ID:", self.process_id, "cs2, sottr"),
            ("Benchmark:", self.benchmark_name, ""),
            ("Duration (s):", self.benchmark_duration, ""),
            ("Startup Wait:", self.startup_wait, ""),
            ("Resolution:", self.resolution, "1920x1080"),
            ("Preset:", self.preset, "High, Ultra"),
            ("Graphics API:", self.graphics_api, "DX11, DX12, Vulkan"),
        ]:
            ttk.Label(meta_scrollable, text=label).grid(row=row, column=0, sticky=tk.W, pady=1, padx=2)
            ttk.Entry(meta_scrollable, textvariable=var, width=25).grid(row=row, column=1, sticky=tk.W, pady=1)
            if hint:
                ttk.Label(meta_scrollable, text=hint, font=('TkDefaultFont', 9), foreground="gray").grid(
                    row=row, column=2, sticky=tk.W, padx=3)
            row += 1

        # Separator line above ribbon
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(side=tk.BOTTOM, fill=tk.X, pady=2)
        
        # Screenshot history ribbon (expanded by default)
        self.ribbon_panel = CollapsibleFrame(self.root, title="Screenshot History", collapsed=False)
        self.ribbon_panel.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=2, before=None)
        
        # Scrollable ribbon inside collapsible
        ribbon_canvas = tk.Canvas(self.ribbon_panel.content, height=60, bg="gray20")
        ribbon_h_scroll = ttk.Scrollbar(self.ribbon_panel.content, orient=tk.HORIZONTAL, command=ribbon_canvas.xview)
        self.ribbon_container = ttk.Frame(ribbon_canvas)
        
        self.ribbon_container.bind(
            "<Configure>",
            lambda e: ribbon_canvas.configure(scrollregion=ribbon_canvas.bbox("all"))
        )
        ribbon_canvas.create_window((0, 0), window=self.ribbon_container, anchor="nw")
        ribbon_canvas.configure(xscrollcommand=ribbon_h_scroll.set)
        
        ribbon_h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        ribbon_canvas.pack(side=tk.TOP, fill=tk.X)

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

            if self.omniparser_connection:
                self.vision_model = self.omniparser_connection
                self.vision_status_label.config(text="●", foreground="green")
            else:
                self.vision_model = None
                self.vision_status_label.config(text="●", foreground="red")
        else:
            self.omni_frame.pack_forget()
            self.gemma_frame.pack(fill=tk.X, pady=2)

            if self.gemma_connection:
                self.vision_model = self.gemma_connection
                self.gemma_status_label.config(text="●", foreground="green")
            else:
                self.vision_model = None
                self.gemma_status_label.config(text="●", foreground="red")

    def cycle_zoom(self):
        """Cycle through zoom levels: 50% -> 60% -> 70% -> 80% -> 90% -> 100% -> 50%..."""
        # Move to next zoom level (cycle back to start)
        self.zoom_index = (self.zoom_index + 1) % len(self.zoom_levels)
        zoom = self.zoom_levels[self.zoom_index]
        
        # Update button text
        zoom_percent = int(zoom * 100)
        self.zoom_btn_text.set(f"🔎 {zoom_percent}%")
        
        # Apply zoom to canvas
        self.canvas.set_zoom(zoom)
        
        self.status_text.set(f"Zoom: {zoom_percent}%")

    def refresh_ribbon(self):
        """Refresh the screenshot history ribbon with thumbnail pairs."""
        # Clear existing thumbnails
        for widget in self.ribbon_container.winfo_children():
            widget.destroy()
        self.history_thumbnails.clear()
        
        THUMB_SIZE = (60, 34)  # Small thumbnails (16:9 aspect)
        
        for i, entry in enumerate(self.screenshot_history):
            # Create pair frame
            pair_frame = ttk.Frame(self.ribbon_container)
            pair_frame.pack(side=tk.LEFT, padx=5, pady=2)
            
            # Captured thumbnail
            if entry['captured'] and os.path.exists(entry['captured']):
                try:
                    img = Image.open(entry['captured'])
                    img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.history_thumbnails.append(photo)
                    
                    lbl = ttk.Label(pair_frame, image=photo)
                    lbl.pack(side=tk.LEFT, padx=1)
                    lbl.bind("<Button-1>", lambda e, path=entry['captured']: self._load_from_ribbon(path))
                except Exception as ex:
                    logger.warning(f"Failed to load thumbnail: {ex}")
            
            # Parsed thumbnail (if available)
            if entry['parsed'] and os.path.exists(entry['parsed']):
                try:
                    img = Image.open(entry['parsed'])
                    img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.history_thumbnails.append(photo)
                    
                    lbl = ttk.Label(pair_frame, image=photo)
                    lbl.pack(side=tk.LEFT, padx=1)
                    lbl.bind("<Button-1>", lambda e, path=entry['parsed']: self._load_from_ribbon(path))
                except Exception as ex:
                    logger.warning(f"Failed to load parsed thumbnail: {ex}")
            
            # Separator between pairs
            if i < len(self.screenshot_history) - 1:
                ttk.Separator(self.ribbon_container, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

    def _load_from_ribbon(self, image_path):
        """Load an image from ribbon click."""
        try:
            self.canvas.load_image(image_path, self.current_bboxes if image_path == self.current_screenshot else [])
            self.status_text.set(f"Loaded: {os.path.basename(image_path)}")
        except Exception as e:
            logger.warning(f"Failed to load ribbon image: {e}")

    def toggle_game_test(self):
        """Toggle between launching game and killing it."""
        if not self.network:
            messagebox.showerror("Error", "Connect to SUT first!")
            return
        
        if not self.game_running:
            # LAUNCH mode
            game_path = self.game_path.get()
            if not game_path:
                messagebox.showerror("Error", "Enter a Game Path first!")
                return
            
            try:
                self.status_text.set("Launching game...")
                # Immediately show waiting state on button
                self.test_kill_btn.config(text="⏳ Waiting...", bg="#FFC107")  # Yellow/amber for waiting
                self.root.update()
                
                # Get startup wait from metadata (default 30 seconds for slow games)
                startup_wait = 30
                process_id = self.process_id.get()
                
                # Launch game via SUT
                response = self.network.session.post(
                    f"{self.network.base_url}/launch",
                    json={
                        "path": game_path, 
                        "process_id": process_id,
                        "startup_wait": startup_wait
                    },
                    timeout=120  # Increased timeout for slow launches
                )
                result = response.json()
                
                status = result.get("status", "unknown")
                
                if status == "success":
                    # Full success - process found and foreground confirmed
                    self.game_running = True
                    self.test_kill_btn.config(text="■ Kill Game", bg="#f44336")  # Red
                    process_name = process_id or os.path.basename(game_path)
                    pid = result.get("game_process_pid", "?")
                    self.status_text.set(f"Game running! PID: {pid}")
                    
                elif status == "warning":
                    # Partial success - game launched but process detection had issues
                    warning_msg = result.get("warning", "Process detection issue")
                    self.status_text.set(f"Launched (warning: {warning_msg[:40]}...)")
                    
                    # Still set as running - user can try to kill or wait for process
                    self.game_running = True
                    self.test_kill_btn.config(text="■ Kill Game", bg="#ff9800")  # Orange for warning
                    
                    # Try to detect process with retry if we have a process_id
                    if process_id:
                        self._retry_process_detection(process_id, max_retries=5, interval=3)
                    
                else:
                    # Real error
                    error_msg = result.get("error", "Unknown error")
                    self.status_text.set(f"Launch failed: {error_msg[:50]}")
                    messagebox.showerror("Launch Error", f"Failed to launch game:\n{error_msg}")
                    
            except Exception as e:
                error_str = str(e)
                # Check for timeout specifically
                if "timeout" in error_str.lower() or "timed out" in error_str.lower():
                    self.status_text.set("Launch timeout - game may still be starting")
                    # Offer to still mark as running
                    if messagebox.askyesno("Timeout", 
                        "Launch request timed out.\n\n"
                        "The game may still be starting.\n"
                        "Mark as running anyway?"):
                        self.game_running = True
                        self.test_kill_btn.config(text="■ Kill Game", bg="#ff9800")  # Orange
                else:
                    messagebox.showerror("Error", f"Failed to launch: {e}")
                    self.status_text.set("Launch failed")
        else:
            # KILL mode - use existing /action with terminate_game
            try:
                self.status_text.set("Killing game...")
                self.root.update()
                
                # Kill via existing action endpoint
                result = self.network.send_action({"type": "terminate_game"})
                
                if result.get("status") == "success":
                    self.status_text.set("Game terminated")
                else:
                    self.status_text.set(f"Terminate result: {result.get('status', 'unknown')}")
                
                # Reset button
                self.game_running = False
                self.test_kill_btn.config(text="▶ Launch Game", bg="#4CAF50")  # Green
                    
            except Exception as e:
                messagebox.showerror("Error", f"Failed to kill: {e}")
    
    def _retry_process_detection(self, process_name, max_retries=5, interval=3):
        """Retry process detection in the background with status updates."""
        import threading
        
        def _detect_loop():
            for attempt in range(1, max_retries + 1):
                try:
                    self.status_text.set(f"Detecting process... ({attempt}/{max_retries})")
                    self.root.update()
                    
                    response = self.network.session.post(
                        f"{self.network.base_url}/check_process",
                        json={"process_name": process_name},
                        timeout=5
                    )
                    result = response.json()
                    
                    if result.get("running"):
                        pid = result.get("pid", "?")
                        self.status_text.set(f"Process found! PID: {pid}")
                        self.test_kill_btn.config(bg="#f44336")  # Red - confirmed running, ready to kill
                        return
                    
                    time.sleep(interval)
                    
                except Exception as e:
                    logger.warning(f"Process detection attempt {attempt} failed: {e}")
                    time.sleep(interval)
            
            # Max retries reached
            self.status_text.set(f"Process '{process_name}' not detected (may still be running)")
        
        # Run in background thread to not block UI
        thread = threading.Thread(target=_detect_loop, daemon=True)
        thread.start()
    
    def _check_game_process(self):
        """Check if game process is still running."""
        if not self.network or not self.game_running:
            return
        
        process_name = self.process_name.get()
        if not process_name:
            return
        
        try:
            response = self.network.session.post(
                f"{self.network.base_url}/check_process",
                json={"process_name": process_name},
                timeout=5
            )
            result = response.json()
            
            if result.get("running"):
                self.status_text.set(f"Running: {result.get('name')} (PID: {result.get('pid')})")
            else:
                # Process exited
                self.game_running = False
                self.test_kill_btn.config(text="▶ Launch Game", bg="#4CAF50")
                self.status_text.set("Game exited")
        except Exception as e:
            logger.warning(f"Process check failed: {e}")


    def connect_sut(self):
        """Connect to SUT service."""
        try:
            ip = self.sut_ip.get()
            port = int(self.sut_port.get())

            self.network = NetworkManager(ip, port)
            self.screenshot_mgr = ScreenshotManager(self.network)

            self.sut_status_label.config(text="●", foreground="green")
            self.status_text.set(f"Connected to SUT at {ip}:{port}")
            messagebox.showinfo("Success", "Connected to SUT successfully!")

        except Exception as e:
            self.sut_status_label.config(text="●", foreground="red")
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

            status_label.config(text="●", foreground="green")
            self.status_text.set(f"Connected to {model_name} at {url}")
            messagebox.showinfo("Success", f"Connected to {model_name} successfully!")

        except Exception as e:
            if self.vision_var.get() == "omniparser":
                self.vision_status_label.config(text="●", foreground="red")
                # Clear saved connection on failure
                self.omniparser_connection = None
            else:
                self.gemma_status_label.config(text="●", foreground="red")
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

            # Add to history (parsed will be added later)
            self.screenshot_history.append({
                'captured': screenshot_path,
                'parsed': None,
                'timestamp': timestamp
            })

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

            # Update history with parsed image
            if self.screenshot_history:
                self.screenshot_history[-1]['parsed'] = annotation_path
                self.refresh_ribbon()

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
            elif step.action_type in ["right_click", "double_click", "middle_click", "text", "drag", "key", "hotkey", "hold_click"]:
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

            # Handle hold_click - needs coordinates like find_and_click
            elif step.action_type == "hold_click" and step.action_config:
                if step.selected_bbox:
                    bbox = step.selected_bbox
                    x = bbox.x + bbox.width // 2
                    y = bbox.y + bbox.height // 2
                else:
                    messagebox.showwarning("Warning", "Hold Click requires a selected element! Please edit the step and select a target.")
                    return
                
                # Build action with coordinates
                action = step.action_config.copy()
                action["x"] = x
                action["y"] = y
                
                self.network.send_action(action)
                self.status_text.set(f"Tested step {step.step_number}: Hold click at ({x}, {y})")
                messagebox.showinfo("Success", f"Hold click executed at ({x}, {y})!")

            # Handle other actions with action_config
            elif step.action_config:
                self.network.send_action(step.action_config)
                self.status_text.set(f"Tested step {step.step_number}")
                messagebox.showinfo("Success", "Action executed on SUT!")

            else:
                messagebox.showwarning("Warning", "This step has no executable action configured!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to test action: {str(e)}")

    def toggle_flow(self):
        """Toggle between starting and stopping flow."""
        if self.flow_running:
            # Stop was requested
            self.flow_stop_requested = True
            self.status_text.set("Stopping flow...")
        else:
            # Start flow
            self.test_full_flow()

    def test_full_flow(self):
        """Test full flow from selected step onwards."""
        selection = self.steps_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a starting step!")
            return

        if not self.network:
            messagebox.showwarning("Warning", "Please connect to SUT first!")
            return

        start_idx = selection[0]
        total_steps = len(self.workflow_steps) - start_idx
        
        if not messagebox.askyesno("Test Full Flow", 
            f"Execute {total_steps} steps starting from step {start_idx + 1}?"):
            return

        # Set running state and update button
        self.flow_running = True
        self.flow_stop_requested = False
        self.flow_btn.config(text="■ Stop", bg="#f44336")  # Red
        self.root.update()

        failed_step = None
        stopped = False
        executed = 0
        
        for i in range(start_idx, len(self.workflow_steps)):
            # Check for stop request
            if self.flow_stop_requested:
                stopped = True
                break
            
            step = self.workflow_steps[i]
            self.steps_listbox.selection_clear(0, tk.END)
            self.steps_listbox.selection_set(i)
            self.steps_listbox.see(i)
            self.root.update()
            
            self.status_text.set(f"Executing step {i + 1}/{len(self.workflow_steps)}: {step.description}")
            self.root.update()
            
            try:
                # Execute step
                if step.action_type == "find_and_click":
                    # Capture and parse screenshot to find element dynamically
                    if self.screenshot_mgr and self.vision_model:
                        self.status_text.set(f"Step {i + 1}: Capturing screenshot...")
                        self.root.update()
                        
                        # Capture screenshot
                        import os
                        os.makedirs("workflow_builder_temp", exist_ok=True)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        screenshot_path = f"workflow_builder_temp/flow_step_{i+1}_{timestamp}.png"
                        self.screenshot_mgr.capture(screenshot_path)
                        
                        if self.flow_stop_requested:
                            stopped = True
                            break
                        
                        self.status_text.set(f"Step {i + 1}: Parsing UI elements...")
                        self.root.update()
                        
                        # Parse screenshot
                        bboxes = self.vision_model.detect_ui_elements(screenshot_path)
                        
                        if self.flow_stop_requested:
                            stopped = True
                            break
                        
                        # Find matching element
                        found_bbox = None
                        search_text = getattr(step, 'text', '')
                        search_type = getattr(step, 'element_type', 'any')
                        match_mode = getattr(step, 'text_match', 'contains')
                        
                        for bbox in bboxes:
                            type_match = search_type in ["any", bbox.element_type]
                            
                            if type_match and search_text:
                                if match_mode == "contains" and search_text.lower() in bbox.element_text.lower():
                                    found_bbox = bbox
                                    break
                                elif match_mode == "exact" and search_text.lower() == bbox.element_text.lower():
                                    found_bbox = bbox
                                    break
                                elif match_mode == "startswith" and bbox.element_text.lower().startswith(search_text.lower()):
                                    found_bbox = bbox
                                    break
                        
                        if found_bbox:
                            x = found_bbox.x + found_bbox.width // 2
                            y = found_bbox.y + found_bbox.height // 2
                            self.status_text.set(f"Step {i + 1}: Found '{search_text}' at ({x}, {y})")
                            self.root.update()
                        else:
                            # Fallback to stored bbox if available
                            if step.selected_bbox:
                                bbox = step.selected_bbox
                                x = bbox.x + bbox.width // 2
                                y = bbox.y + bbox.height // 2
                                self.status_text.set(f"Step {i + 1}: Element not found, using stored coords ({x}, {y})")
                                self.root.update()
                            else:
                                self.status_text.set(f"Step {i + 1}: Element '{search_text}' not found!")
                                self.root.update()
                                failed_step = (i + 1, f"Element '{search_text}' not found")
                                break
                    elif step.selected_bbox:
                        # No vision model, use stored bbox
                        bbox = step.selected_bbox
                        x = bbox.x + bbox.width // 2
                        y = bbox.y + bbox.height // 2
                    else:
                        self.status_text.set(f"Step {i + 1}: No vision model and no stored coords, skipping...")
                        self.root.update()
                        continue
                    
                    action = {"type": "click", "x": x, "y": y, "button": getattr(step, 'button', 'left')}
                    self.network.send_action(action)
                
                elif step.action_config:
                    # Handle wait actions locally to avoid network timeout
                    if step.action_config.get('type') == 'wait':
                        wait_duration = step.action_config.get('duration', 1)
                        self.status_text.set(f"Step {i + 1}: Waiting {wait_duration}s...")
                        self.root.update()
                        import time
                        for sec in range(int(wait_duration)):
                            if self.flow_stop_requested:
                                stopped = True
                                break
                            # Update status every 10 seconds only
                            if sec % 10 == 0:
                                self.status_text.set(f"Step {i + 1}: Waiting... {wait_duration - sec}s remaining")
                                self.root.update()
                            time.sleep(1)
                        if stopped:
                            break
                    else:
                        self.network.send_action(step.action_config)
                
                else:
                    self.status_text.set(f"Step {i + 1}: No action configured, skipping...")
                    self.root.update()
                    continue
                
                executed += 1
                
                # Wait for expected delay (in small chunks to check stop)
                delay = getattr(step, 'expected_delay', 1)
                import time
                for _ in range(int(delay * 10)):
                    if self.flow_stop_requested:
                        stopped = True
                        break
                    time.sleep(0.1)
                    self.root.update()
                
                if stopped:
                    break
                
            except Exception as e:
                failed_step = (i + 1, str(e))
                break
        
        # Reset button state
        self.flow_running = False
        self.flow_stop_requested = False
        self.flow_btn.config(text="▶▶ Flow", bg="#2E7D32")  # Green
        self.root.update()
        
        if stopped:
            self.status_text.set(f"Flow stopped after {executed} steps")
            messagebox.showinfo("Stopped", f"Flow stopped after {executed} steps")
        elif failed_step:
            messagebox.showerror("Flow Failed", f"Step {failed_step[0]} failed: {failed_step[1]}")
            self.status_text.set(f"Flow failed at step {failed_step[0]}")
        else:
            messagebox.showinfo("Success", f"Executed {total_steps} steps successfully!")
            self.status_text.set(f"Flow complete: {total_steps} steps executed")

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

    def save_yaml(self):
        """Save workflow to YAML file."""
        if not self.workflow_steps:
            messagebox.showwarning("Warning", "No steps to save!")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")],
            initialdir="config/games"
        )

        if not filename:
            return

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

        if not filename:
            return

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

        # Reset all metadata fields
        self.game_name.set("My Game")
        self.version.set("1.0")
        self.game_path.set("")
        self.process_id.set("")
        self.process_name.set("")
        self.benchmark_name.set("")
        self.benchmark_duration.set("120")
        self.startup_wait.set("30")
        self.resolution.set("1920x1080")
        self.preset.set("High")
        self.engine.set("")
        self.graphics_api.set("DirectX 11")

        self.status_text.set("New workflow created")

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
