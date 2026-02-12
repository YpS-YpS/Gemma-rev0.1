"""
Intelligent automation engine combining OmniParser (element detection) with
VLM reasoning (semantic decision-making). Each step loops through:
  Stabilize → Capture → Parse → Reason → Execute → Verify

Falls back to deterministic step matching when VLM is unavailable.
"""

import os
import time
import logging
import yaml
from typing import List, Dict, Any, Optional

from modules.gemma_client import BoundingBox
from modules.vlm_reasoning_client import VLMReasoningClient, VLMDecision
from modules.screen_stabilizer import ScreenStabilizer

logger = logging.getLogger(__name__)


class IntelligentAutomation:
    """Goal-driven automation engine using OmniParser + VLM reasoning."""

    def __init__(
        self,
        config_path: str,
        network,
        screenshot_mgr,
        omniparser,
        vlm_client: VLMReasoningClient,
        stop_event=None,
        run_dir: str = None,
        annotator=None,
        progress_callback=None,
        stabilization_enabled: bool = True,
    ):
        """
        Args:
            config_path: Path to YAML configuration file.
            network: NetworkManager instance for SUT communication.
            screenshot_mgr: ScreenshotManager instance.
            omniparser: OmniparserClient instance for element detection.
            vlm_client: VLMReasoningClient for semantic reasoning.
            stop_event: Threading event for stopping automation.
            run_dir: Directory to save logs and screenshots.
            annotator: Annotator instance for drawing bounding boxes.
            progress_callback: Object with total_steps/completed_steps attributes.
            stabilization_enabled: Whether to use screen stabilization.
        """
        self.config_path = config_path
        self.network = network
        self.screenshot_mgr = screenshot_mgr
        self.omniparser = omniparser
        self.vlm_client = vlm_client
        self.stop_event = stop_event
        self.annotator = annotator
        self.progress_callback = progress_callback
        self.stabilization_enabled = stabilization_enabled

        # Load configuration
        try:
            from modules.simple_config_parser import SimpleConfigParser
            config_parser = SimpleConfigParser(config_path)
            self.config = config_parser.get_config()
            logger.info("Using SimpleConfigParser for step-based configuration")
        except (ImportError, ValueError):
            logger.info("SimpleConfigParser not available, loading YAML directly")
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)

        # Game metadata
        self.game_name = self.config.get("metadata", {}).get("game_name", "Unknown Game")
        self.run_dir = run_dir or f"logs/{self.game_name}"

        # Retry / timing
        self.retry_delay = self.config.get("metadata", {}).get("retry_delay", 2.0)

        # Hooks
        self.hooks = self.config.get("hooks", {})
        self.persistent_processes: Dict[int, Dict] = {}

        # Screen stabilizer
        if self.stabilization_enabled:
            self.stabilizer = ScreenStabilizer(screenshot_mgr)
        else:
            self.stabilizer = None

        # Action history for VLM context
        self.action_history: List[str] = []

        logger.info(
            f"IntelligentAutomation initialized for {self.game_name} "
            f"(stabilization={'ON' if stabilization_enabled else 'OFF'})"
        )

        # Log SUT resolution
        try:
            resolution = self.network.get_resolution()
            logger.info(f"Detected SUT Resolution: {resolution['width']}x{resolution['height']}")
        except Exception as e:
            logger.warning(f"Could not determine SUT resolution: {e}")

    # ========================================================================
    # MAIN RUN LOOP
    # ========================================================================

    def run(self) -> bool:
        """Run the intelligent step-by-step automation."""
        steps = self.config.get("steps", {})
        if not steps:
            logger.error("No steps defined in configuration")
            return False

        # Normalize keys
        steps = {str(k): v for k, v in steps.items()}
        logger.info(f"Starting intelligent automation with {len(steps)} steps")

        # Pre-hooks
        try:
            self._execute_pre_hooks()
            self._start_persistent_hooks()
        except Exception as e:
            logger.error(f"Pre-hook execution failed: {e}")
            self._stop_persistent_hooks()
            return False

        if self.progress_callback:
            self.progress_callback.total_steps = len(steps)

        current_step = 1
        while current_step <= len(steps):
            step_key = str(current_step)
            if step_key not in steps:
                logger.error(f"Step {step_key} not found in configuration")
                self._stop_persistent_hooks()
                self._execute_post_hooks()
                return False

            step = steps[step_key]
            step_description = step.get("description", "No description")
            is_optional = step.get("optional", False) or "[OPTIONAL]" in step_description.upper()
            max_iterations = step.get("max_iterations", 10)

            logger.info("=" * 60)
            logger.info(f"STEP {current_step}/{len(steps)}: {step_description}")
            logger.info("=" * 60)

            if self.stop_event and self.stop_event.is_set():
                logger.info("Stop event detected, ending automation")
                break

            # VLM-driven iteration loop for this step
            goal = self._build_goal_from_step(step)
            step_completed = False
            last_action_key = None
            same_action_count = 0
            max_same_action = 3  # Auto-advance if VLM repeats identical action this many times

            for iteration in range(1, max_iterations + 1):
                if self.stop_event and self.stop_event.is_set():
                    break

                logger.info(f"  Iteration {iteration}/{max_iterations} for step {current_step}")

                try:
                    completed, action_key = self._execute_step(current_step, step, iteration, goal)
                    if completed:
                        step_completed = True
                        break

                    # Stuck detection: if VLM repeats the same action N times, assume step done
                    if action_key and action_key == last_action_key:
                        same_action_count += 1
                        if same_action_count >= max_same_action:
                            logger.warning(
                                f"  VLM repeated '{action_key}' {same_action_count} times — "
                                f"assuming step {current_step} is complete (stuck detection)"
                            )
                            step_completed = True
                            break
                    else:
                        same_action_count = 1
                    last_action_key = action_key

                except Exception as e:
                    logger.error(f"  Step execution error on iteration {iteration}: {e}")
                    time.sleep(self.retry_delay)

            if step_completed:
                logger.info(f">> Step {current_step} completed successfully")
                if self.progress_callback:
                    self.progress_callback.completed_steps = current_step
                current_step += 1
            elif is_optional:
                logger.warning(f"Optional step {current_step} not completed after {max_iterations} iterations, skipping")
                current_step += 1
            else:
                logger.error(f"Step {current_step} failed after {max_iterations} iterations")
                self._stop_persistent_hooks()
                self._execute_post_hooks()
                return False

        automation_success = current_step > len(steps)
        try:
            self._stop_persistent_hooks()
            self._execute_post_hooks()
        except Exception as e:
            logger.error(f"Post-hook execution failed: {e}")

        return automation_success

    # ========================================================================
    # STEP EXECUTION
    # ========================================================================

    def _execute_step(self, step_num: int, step: Dict, iteration: int, goal: str):
        """
        Execute one iteration of a step:
        1. Stabilize screen
        2. Capture screenshot
        3. OmniParser detect elements
        4. VLM reason about action
        5. If goal_achieved → return (True, action_key)
        6. Execute VLM decision
        7. Handle sideload
        8. Wait expected_delay

        Returns (is_completed, action_key) — action_key is a string identifying the
        action taken (e.g. "key:space") for stuck detection.
        """
        screenshot_dir = f"{self.run_dir}/screenshots"
        annotated_dir = f"{self.run_dir}/annotated"
        os.makedirs(screenshot_dir, exist_ok=True)
        os.makedirs(annotated_dir, exist_ok=True)

        screenshot_path = f"{screenshot_dir}/step{step_num}_iter{iteration}.png"
        som_path = f"{annotated_dir}/step{step_num}_iter{iteration}_som.png"

        # --- 1. STABILIZE ---
        if self.stabilizer and self.stabilization_enabled:
            logger.info("  Waiting for screen to stabilize...")
            is_stable, screenshot_path = self.stabilizer.wait_for_stable(
                screenshot_path, self.stop_event
            )
            if not is_stable:
                logger.warning("  Screen did not fully stabilize, proceeding anyway")
        else:
            # Direct capture
            self.screenshot_mgr.capture(screenshot_path)

        # --- 2. PARSE with OmniParser ---
        bounding_boxes = []
        som_available = False
        try:
            bounding_boxes = self.omniparser.detect_ui_elements(
                screenshot_path, annotation_path=som_path
            )
            som_available = os.path.exists(som_path)
            logger.info(f"  OmniParser detected {len(bounding_boxes)} elements")
        except Exception as e:
            logger.warning(f"  OmniParser detection failed: {e}")
            # VLM can still reason from raw screenshot alone

        # Create annotator overlay if available
        if self.annotator and bounding_boxes:
            try:
                local_annotated = f"{annotated_dir}/step{step_num}_iter{iteration}_annotated.png"
                self.annotator.draw_bounding_boxes(screenshot_path, bounding_boxes, local_annotated)
            except Exception:
                pass

        # --- 3. FORMAT elements for VLM ---
        elements = self._format_elements_for_vlm(bounding_boxes)

        # --- 4. VLM REASONING ---
        step_context = step.get("description", "")
        decision = None
        try:
            decision = self.vlm_client.reason(
                screenshot_path=screenshot_path,
                som_image_path=som_path if som_available else None,
                elements=elements,
                goal=goal,
                action_history=self.action_history,
                step_context=step_context,
            )
        except Exception as e:
            logger.warning(f"  VLM reasoning failed: {e}")

        # --- 5. CHECK GOAL ACHIEVED ---
        if decision and decision.goal_achieved and decision.confidence >= 0.5:
            logger.info(f"  VLM says goal achieved (confidence={decision.confidence:.2f}): {decision.reasoning}")
            self.action_history.append(f"Step {step_num}: Goal achieved - {goal}")
            return True, "done"

        # --- 6. EXECUTE ACTION ---
        action_taken = False
        action_key = None  # For stuck detection: "key:space", "click:3", "fallback", etc.
        if decision and decision.action_type != "wait":
            action_taken = self._execute_vlm_decision(decision, elements, bounding_boxes)
            # Build action_key for stuck detection
            if decision.action_type == "key":
                action_key = f"key:{decision.key}"
            elif decision.action_type == "click":
                action_key = f"click:{decision.element_id}"
            elif decision.action_type == "type":
                action_key = f"type:{decision.text}"
            else:
                action_key = decision.action_type
            action_desc = (
                f"Step {step_num} iter {iteration}: {decision.action_type}"
                f" elem={decision.element_id} ({decision.reasoning[:50]})"
            )
            self.action_history.append(action_desc)
        elif decision and decision.action_type == "wait":
            action_key = "wait"
            logger.info(f"  VLM decided to wait: {decision.reasoning}")
            self.action_history.append(f"Step {step_num} iter {iteration}: wait")
        else:
            # VLM failed — try deterministic fallback
            logger.info("  VLM unavailable, attempting deterministic fallback")
            action_taken = self._fallback_to_simple(step, bounding_boxes, step_num)
            if action_taken:
                action_key = "fallback"
                self.action_history.append(f"Step {step_num} iter {iteration}: deterministic fallback")

        # --- 7. SIDELOAD ---
        if "sideload" in step and action_taken:
            self._handle_sideload_action(step["sideload"])

        # --- 8. WAIT ---
        expected_delay = step.get("expected_delay", 1)
        if expected_delay > 0 and action_taken:
            logger.info(f"  Waiting {expected_delay}s after action...")
            time.sleep(expected_delay)

        return False, action_key  # Not yet achieved, continue iterating

    # ========================================================================
    # GOAL CONSTRUCTION
    # ========================================================================

    def _build_goal_from_step(self, step: Dict) -> str:
        """Build a goal string from step configuration. Priority: goal > description > synthesized."""
        if "goal" in step:
            return step["goal"]
        if "description" in step:
            return step["description"]
        # Synthesize from find + action
        parts = []
        if "find" in step:
            find = step["find"]
            etype = find.get("type", "element")
            etext = find.get("text", "")
            parts.append(f"Find {etype}" + (f" with text '{etext}'" if etext else ""))
        if "action" in step:
            action = step["action"]
            if isinstance(action, dict):
                parts.append(f"and {action.get('type', 'interact')}")
            else:
                parts.append(f"and {action}")
        return " ".join(parts) if parts else "Complete this step"

    # ========================================================================
    # ELEMENT FORMATTING
    # ========================================================================

    def _format_elements_for_vlm(self, bounding_boxes: List[BoundingBox]) -> List[Dict]:
        """Convert BoundingBox list to VLM-friendly dicts with sequential IDs."""
        elements = []
        for i, bbox in enumerate(bounding_boxes):
            elements.append({
                "id": i,
                "type": bbox.element_type,
                "text": bbox.element_text or "",
                "bbox": {
                    "x": bbox.x,
                    "y": bbox.y,
                    "w": bbox.width,
                    "h": bbox.height,
                },
                "interactivity": bbox.confidence >= 0.5,  # Use confidence as proxy
            })
        return elements

    # ========================================================================
    # ACTION EXECUTION
    # ========================================================================

    def _execute_vlm_decision(
        self, decision: VLMDecision, elements: List[Dict], bounding_boxes: List[BoundingBox]
    ) -> bool:
        """Execute a VLM decision by dispatching the appropriate action."""

        if decision.action_type == "done":
            return True

        if decision.action_type == "click":
            return self._execute_click(decision, elements, bounding_boxes)

        if decision.action_type == "key":
            key = decision.key or "Escape"
            try:
                self.network.send_action({"type": "key", "key": key})
                logger.info(f"  Pressed key: {key}")
                return True
            except Exception as e:
                logger.error(f"  Key press failed: {e}")
                return False

        if decision.action_type == "type":
            text = decision.text or ""
            if not text:
                logger.warning("  Type action with no text")
                return False
            try:
                for char in text:
                    if char == " ":
                        self.network.send_action({"type": "key", "key": "space"})
                    elif char == "\n":
                        self.network.send_action({"type": "key", "key": "Return"})
                    else:
                        self.network.send_action({"type": "key", "key": char})
                    time.sleep(0.05)
                logger.info(f"  Typed: '{text[:40]}'")
                return True
            except Exception as e:
                logger.error(f"  Type action failed: {e}")
                return False

        if decision.action_type == "scroll":
            direction = decision.scroll_direction or "down"
            try:
                self.network.send_action({
                    "type": "scroll",
                    "x": 960, "y": 540,  # Center screen
                    "direction": direction,
                    "clicks": 3,
                })
                logger.info(f"  Scrolled {direction}")
                return True
            except Exception as e:
                logger.error(f"  Scroll failed: {e}")
                return False

        if decision.action_type == "wait":
            time.sleep(2)
            return True

        logger.warning(f"  Unhandled action type: {decision.action_type}")
        return False

    def _execute_click(
        self, decision: VLMDecision, elements: List[Dict], bounding_boxes: List[BoundingBox]
    ) -> bool:
        """Execute a click action using element_id to look up coordinates."""
        eid = decision.element_id
        if eid is None:
            logger.warning("  Click action with no element_id")
            return False

        if eid < 0 or eid >= len(bounding_boxes):
            logger.warning(f"  element_id {eid} out of range (0-{len(bounding_boxes)-1})")
            return False

        bbox = bounding_boxes[eid]
        x = bbox.x + bbox.width // 2
        y = bbox.y + bbox.height // 2

        element_desc = f"'{bbox.element_text}'" if bbox.element_text else f"{bbox.element_type}"

        try:
            self.network.send_action({
                "type": "click",
                "x": x,
                "y": y,
                "button": "left",
                "move_duration": 0.5,
                "click_delay": 0.1,
            })
            logger.info(f"  Clicked element [{eid}] {element_desc} at ({x}, {y})")
            return True
        except Exception as e:
            logger.error(f"  Click on element [{eid}] failed: {e}")
            return False

    # ========================================================================
    # DETERMINISTIC FALLBACK
    # ========================================================================

    def _fallback_to_simple(self, step: Dict, bounding_boxes: List[BoundingBox], step_num: int) -> bool:
        """Fall back to deterministic find+action matching (same as SimpleAutomation)."""
        if "find" not in step or "action" not in step:
            logger.info("  No find/action in step for deterministic fallback")
            # Try pressing Escape as last resort
            try:
                self.network.send_action({"type": "key", "key": "Escape"})
                logger.info("  Pressed Escape as last-resort fallback")
            except Exception:
                pass
            return False

        target_def = step["find"]
        target_type = target_def.get("type", "any")
        target_text = target_def.get("text", "")
        match_type = target_def.get("text_match", "contains")

        # Find matching element
        target_element = None
        for bbox in bounding_boxes:
            type_match = target_type == "any" or bbox.element_type == target_type
            text_match = False
            if bbox.element_text and target_text:
                bbox_lower = bbox.element_text.lower()
                target_lower = target_text.lower()
                if match_type == "exact":
                    text_match = target_lower == bbox_lower
                elif match_type == "contains":
                    text_match = target_lower in bbox_lower
                elif match_type == "startswith":
                    text_match = bbox_lower.startswith(target_lower)
                elif match_type == "endswith":
                    text_match = bbox_lower.endswith(target_lower)
            elif not target_text:
                text_match = True

            if type_match and text_match:
                target_element = bbox
                break

        if not target_element:
            logger.warning(f"  Deterministic fallback: target '{target_text}' not found")
            return False

        # Execute the action
        action_config = step["action"]
        if isinstance(action_config, dict):
            action_type = action_config.get("type", "click")
        else:
            action_type = str(action_config)

        if action_type == "click":
            x = target_element.x + target_element.width // 2
            y = target_element.y + target_element.height // 2
            try:
                self.network.send_action({"type": "click", "x": x, "y": y})
                logger.info(f"  Deterministic fallback: clicked '{target_element.element_text}' at ({x}, {y})")
                return True
            except Exception as e:
                logger.error(f"  Deterministic fallback click failed: {e}")
                return False
        elif action_type in ("key", "keypress"):
            key = action_config.get("key", "Escape") if isinstance(action_config, dict) else "Escape"
            try:
                self.network.send_action({"type": "key", "key": key})
                logger.info(f"  Deterministic fallback: pressed {key}")
                return True
            except Exception as e:
                logger.error(f"  Deterministic fallback key failed: {e}")
                return False
        elif action_type == "wait":
            duration = action_config.get("duration", 5) if isinstance(action_config, dict) else 5
            time.sleep(duration)
            return True

        logger.warning(f"  Deterministic fallback: unsupported action type '{action_type}'")
        return False

    # ========================================================================
    # FALLBACK ACTION
    # ========================================================================

    def _execute_fallback(self):
        """Execute fallback action when step fails."""
        fallback = self.config.get("fallbacks", {}).get("general", {})
        if fallback:
            action_type = fallback.get("action")
            if action_type == "key":
                key = fallback.get("key", "Escape")
                logger.info(f"Executing fallback: Press key {key}")
                try:
                    self.network.send_action({"type": "key", "key": key})
                except Exception as e:
                    logger.error(f"Failed to execute fallback key action: {e}")
        time.sleep(fallback.get("expected_delay", 1) if fallback else 1)

    # ========================================================================
    # HOOK MANAGEMENT (same pattern as SimpleAutomation)
    # ========================================================================

    def _execute_pre_hooks(self):
        """Execute non-persistent pre-hooks before step 1 starts."""
        pre_hooks = self.hooks.get("pre", [])
        if not pre_hooks:
            return

        logger.info("=" * 60)
        logger.info("EXECUTING PRE-HOOKS")
        logger.info("=" * 60)

        for i, hook in enumerate(pre_hooks):
            if hook.get("persistent", False):
                continue

            path = hook.get("path")
            if not path:
                logger.warning(f"Pre-hook {i+1} missing 'path', skipping")
                continue

            logger.info(f"Executing pre-hook {i+1}: {path}")
            try:
                result = self.network.execute_command(
                    path=path,
                    args=hook.get("args", []),
                    timeout=hook.get("timeout", 300),
                    working_dir=hook.get("working_dir"),
                    wait=True,
                    shell=hook.get("shell"),
                )
                if result.get("status") == "success":
                    logger.info(f"Pre-hook {i+1} completed (exit code: {result.get('exit_code', 0)})")
                else:
                    logger.warning(f"Pre-hook {i+1} failed: {result.get('error', 'Unknown error')}")
            except Exception as e:
                logger.error(f"Pre-hook {i+1} execution failed: {e}")
                raise

        logger.info("Pre-hooks completed")

    def _start_persistent_hooks(self):
        """Start persistent hooks that run throughout automation."""
        pre_hooks = self.hooks.get("pre", [])
        persistent = [h for h in pre_hooks if h.get("persistent", False)]
        if not persistent:
            return

        logger.info("=" * 60)
        logger.info("STARTING PERSISTENT HOOKS")
        logger.info("=" * 60)

        for i, hook in enumerate(persistent):
            path = hook.get("path")
            if not path:
                continue

            logger.info(f"Starting persistent hook {i+1}: {path}")
            try:
                result = self.network.execute_command(
                    path=path,
                    args=hook.get("args", []),
                    timeout=0,
                    working_dir=hook.get("working_dir"),
                    wait=False,
                    shell=hook.get("shell"),
                )
                if result.get("status") == "success":
                    pid = result.get("pid")
                    logger.info(f"Persistent hook {i+1} started (PID: {pid})")
                    self.persistent_processes[pid] = {"path": path, "hook_config": hook}
                else:
                    logger.error(f"Failed to start persistent hook {i+1}: {result.get('error')}")
            except Exception as e:
                logger.error(f"Persistent hook {i+1} start failed: {e}")
                raise

        if self.persistent_processes:
            logger.info(f"Started {len(self.persistent_processes)} persistent hook(s)")

    def _stop_persistent_hooks(self):
        """Stop all persistent hooks after automation completes."""
        if not self.persistent_processes:
            return

        logger.info("=" * 60)
        logger.info("STOPPING PERSISTENT HOOKS")
        logger.info("=" * 60)

        for pid, info in list(self.persistent_processes.items()):
            logger.info(f"Terminating persistent hook: {info.get('path')} (PID: {pid})")
            try:
                result = self.network.terminate_process(pid)
                if result.get("terminated"):
                    logger.info(f"Persistent hook terminated: PID {pid}")
                else:
                    logger.warning(f"Could not confirm termination of PID {pid}")
            except Exception as e:
                logger.warning(f"Error terminating persistent hook PID {pid}: {e}")

        self.persistent_processes.clear()
        logger.info("Persistent hooks cleanup completed")

    def _execute_post_hooks(self):
        """Execute post-hooks after all steps complete."""
        post_hooks = self.hooks.get("post", [])
        if not post_hooks:
            return

        logger.info("=" * 60)
        logger.info("EXECUTING POST-HOOKS")
        logger.info("=" * 60)

        for i, hook in enumerate(post_hooks):
            path = hook.get("path")
            if not path:
                continue

            logger.info(f"Executing post-hook {i+1}: {path}")
            try:
                result = self.network.execute_command(
                    path=path,
                    args=hook.get("args", []),
                    timeout=hook.get("timeout", 300),
                    working_dir=hook.get("working_dir"),
                    wait=True,
                    shell=hook.get("shell"),
                )
                if result.get("status") == "success":
                    logger.info(f"Post-hook {i+1} completed (exit code: {result.get('exit_code', 0)})")
                else:
                    logger.warning(f"Post-hook {i+1} failed: {result.get('error', 'Unknown error')}")
            except Exception as e:
                logger.error(f"Post-hook {i+1} execution failed: {e}")

        logger.info("Post-hooks completed")

    # ========================================================================
    # SIDELOAD
    # ========================================================================

    def _handle_sideload_action(self, action_config: Dict[str, Any]) -> bool:
        """Execute an external script/executable within a step."""
        path = action_config.get("path")
        if not path:
            logger.error("Sideload action missing 'path'")
            return False

        wait_for_completion = action_config.get("wait_for_completion", True)
        check_exit_code = action_config.get("check_exit_code", True)

        logger.info(f"Executing sideload: {path}")
        try:
            result = self.network.execute_command(
                path=path,
                args=action_config.get("args", []),
                timeout=action_config.get("timeout", 300),
                working_dir=action_config.get("working_dir"),
                wait=wait_for_completion,
                shell=action_config.get("shell"),
            )

            if wait_for_completion:
                exit_code = result.get("exit_code", -1)
                if result.get("stdout"):
                    logger.info(f"Sideload stdout: {result['stdout'][:1000]}")
                if result.get("stderr"):
                    logger.warning(f"Sideload stderr: {result['stderr'][:1000]}")
                if check_exit_code and exit_code != 0:
                    logger.error(f"Sideload failed with exit code: {exit_code}")
                    return False
                logger.info(f"Sideload completed (exit code: {exit_code})")
            else:
                logger.info(f"Sideload started in background (PID: {result.get('pid')})")
            return True

        except Exception as e:
            logger.error(f"Sideload execution failed: {e}")
            return False
