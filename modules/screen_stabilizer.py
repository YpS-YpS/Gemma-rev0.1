"""
Screen stability detection via pixel-level image comparison.
Captures consecutive screenshots and compares them to determine when the UI has settled.
"""

import os
import time
import logging
import numpy as np
from typing import Tuple, Optional
from PIL import Image
from threading import Event

from modules.screenshot import ScreenshotManager

logger = logging.getLogger(__name__)


class ScreenStabilizer:
    """Detects when the screen has stabilized by comparing consecutive screenshots."""

    def __init__(
        self,
        screenshot_mgr: ScreenshotManager,
        similarity_threshold: float = 0.98,
        max_wait_seconds: float = 15.0,
        poll_interval: float = 1.0,
        min_consecutive_stable: int = 2,
    ):
        """
        Args:
            screenshot_mgr: ScreenshotManager for capturing screenshots.
            similarity_threshold: Minimum similarity (0-1) to consider frames stable.
            max_wait_seconds: Maximum time to wait for stability before giving up.
            poll_interval: Seconds between screenshot captures.
            min_consecutive_stable: Number of consecutive stable comparisons required.
        """
        self.screenshot_mgr = screenshot_mgr
        self.similarity_threshold = similarity_threshold
        self.max_wait_seconds = max_wait_seconds
        self.poll_interval = poll_interval
        self.min_consecutive_stable = min_consecutive_stable

    def wait_for_stable(
        self, output_path: str, stop_event: Optional[Event] = None
    ) -> Tuple[bool, str]:
        """
        Wait until the screen content stabilizes, then save the stable screenshot.

        Captures screenshots at poll_interval, compares consecutive pairs.
        Returns once min_consecutive_stable comparisons exceed the similarity threshold,
        or when max_wait_seconds is exceeded.

        Args:
            output_path: Path to save the final stable screenshot.
            stop_event: Optional threading.Event to cancel early.

        Returns:
            (is_stable, screenshot_path) — is_stable is True if stabilization was
            confirmed, False if timed out. screenshot_path is the path of the last
            captured screenshot (saved to output_path).
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        start_time = time.time()
        consecutive_stable = 0
        prev_path: Optional[str] = None
        iteration = 0
        temp_paths = []

        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed >= self.max_wait_seconds:
                    logger.warning(
                        f"Screen stabilization timed out after {elapsed:.1f}s "
                        f"(threshold={self.similarity_threshold}, "
                        f"consecutive={consecutive_stable}/{self.min_consecutive_stable})"
                    )
                    # Save whatever we have as the output
                    if prev_path and prev_path != output_path:
                        _safe_copy(prev_path, output_path)
                    return False, output_path

                if stop_event and stop_event.is_set():
                    logger.info("Screen stabilization cancelled by stop event")
                    if prev_path and prev_path != output_path:
                        _safe_copy(prev_path, output_path)
                    return False, output_path

                # Capture a temporary screenshot
                tmp_path = f"{output_path}.tmp_stab_{iteration}.png"
                temp_paths.append(tmp_path)
                try:
                    self.screenshot_mgr.capture(tmp_path)
                except Exception as e:
                    logger.error(f"Screenshot capture failed during stabilization: {e}")
                    time.sleep(self.poll_interval)
                    iteration += 1
                    continue

                if prev_path is not None:
                    similarity = self._compare_images(prev_path, tmp_path)
                    logger.debug(
                        f"Stabilization check {iteration}: similarity={similarity:.4f} "
                        f"(threshold={self.similarity_threshold})"
                    )

                    if similarity >= self.similarity_threshold:
                        consecutive_stable += 1
                        if consecutive_stable >= self.min_consecutive_stable:
                            # Stable — save the latest capture as the output
                            _safe_copy(tmp_path, output_path)
                            elapsed = time.time() - start_time
                            logger.info(
                                f"Screen stabilized after {elapsed:.1f}s "
                                f"(similarity={similarity:.4f}, "
                                f"consecutive={consecutive_stable})"
                            )
                            return True, output_path
                    else:
                        consecutive_stable = 0

                prev_path = tmp_path
                iteration += 1
                time.sleep(self.poll_interval)

        finally:
            # Clean up temporary files
            for tp in temp_paths:
                try:
                    if os.path.exists(tp):
                        os.remove(tp)
                except OSError:
                    pass

    def _compare_images(self, img_a_path: str, img_b_path: str) -> float:
        """
        Compare two images and return a similarity score.

        Uses mean absolute pixel difference normalized to 0-1 range.
        1.0 means identical, 0.0 means completely different.

        Args:
            img_a_path: Path to first image.
            img_b_path: Path to second image.

        Returns:
            Similarity score between 0.0 and 1.0.
        """
        try:
            with Image.open(img_a_path) as img_a, Image.open(img_b_path) as img_b:
                # Resize to match if dimensions differ
                if img_a.size != img_b.size:
                    img_b = img_b.resize(img_a.size, Image.LANCZOS)

                arr_a = np.asarray(img_a.convert("RGB"), dtype=np.float32)
                arr_b = np.asarray(img_b.convert("RGB"), dtype=np.float32)

                # Mean absolute difference normalized to 0-1
                diff = np.mean(np.abs(arr_a - arr_b)) / 255.0
                similarity = 1.0 - diff
                return similarity

        except Exception as e:
            logger.error(f"Image comparison failed: {e}")
            return 0.0


def _safe_copy(src: str, dst: str) -> None:
    """Copy file from src to dst, overwriting dst if it exists."""
    import shutil
    try:
        shutil.copy2(src, dst)
    except Exception as e:
        logger.warning(f"Failed to copy {src} -> {dst}: {e}")
