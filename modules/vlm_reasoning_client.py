"""
Multi-provider VLM (Vision Language Model) reasoning client.
Sends raw screenshot + OmniParser SOM annotated image + element list + goal context
to a VLM and receives structured action decisions.

Supported providers: OpenAI, Anthropic, Google (Gemini), Local (LM Studio / OpenAI-compatible).
"""

import os
import base64
import json
import re
import logging
import requests
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class VLMDecision:
    """Structured decision returned by the VLM."""
    action_type: str = "wait"           # click, key, type, scroll, wait, done
    element_id: Optional[int] = None    # OmniParser element ID (None for non-element actions)
    key: Optional[str] = None           # For "key" actions (e.g. "escape", "enter")
    text: Optional[str] = None          # For "type" actions
    scroll_direction: Optional[str] = None  # up, down, left, right
    reasoning: str = ""                 # VLM's explanation
    goal_achieved: bool = False         # Is current step's goal done?
    confidence: float = 0.0            # 0.0-1.0


SYSTEM_PROMPT = """You are a game UI automation agent. You analyze screenshots of a game and decide what action to take next to achieve a given goal.

You receive:
1. A raw screenshot of the current game screen
2. An annotated screenshot where UI elements are labeled with numbered IDs (SOM image)
3. A list of detected UI elements with their IDs, types, positions, and text content
4. The current goal to achieve
5. Recent action history showing what was already done

STEP-BY-STEP REASONING (follow this order):

STEP 1 - CHECK IF GOAL IS ALREADY ACHIEVED:
Look at the CURRENT screenshot and element list. Has the goal already been completed?
- If the goal was "press space to continue" and the screen now shows a main menu (not a "press space" prompt), the goal IS achieved.
- If the goal was "click PLAY" and the screen has moved past the play button to a new screen, the goal IS achieved.
- If a previous action in the history already performed what the goal asks, and the screen has changed, the goal IS achieved.
- When the goal is achieved, respond with action_type="done" and goal_achieved=true.

STEP 2 - IF GOAL NOT YET ACHIEVED, DECIDE AN ACTION:
Only if the goal is clearly NOT yet achieved, decide what action to take.

Respond with ONLY a JSON object (no markdown, no extra text):
{
    "action_type": "click|key|type|scroll|wait|done",
    "element_id": <integer ID from element list, or null for non-element actions>,
    "key": <key name string for "key" actions, e.g. "escape", "enter", null otherwise>,
    "text": <text string for "type" actions, null otherwise>,
    "scroll_direction": <"up"|"down"|"left"|"right" for scroll actions, null otherwise>,
    "reasoning": "Brief explanation of why this action was chosen",
    "goal_achieved": <true if the goal appears to be already achieved, false otherwise>,
    "confidence": <0.0 to 1.0 confidence in this decision>
}

CRITICAL RULES:
- ALWAYS check goal completion FIRST before deciding an action. This is the most important step.
- When clicking, you MUST specify element_id from the provided element list. NEVER invent coordinates.
- If the goal appears already achieved in the current screenshot, set goal_achieved=true and action_type="done".
- If you already performed an action (check history) and the screen changed, the goal is likely achieved.
- Do NOT repeat the same action if it was already done and the screen looks different from before.
- If you're unsure, use action_type="wait" with low confidence to let the system re-evaluate.
- Keep reasoning concise (1-2 sentences).
- Respond with ONLY the JSON object."""


class VLMReasoningClient:
    """Multi-provider VLM client for UI automation reasoning."""

    PROVIDERS = {
        "openai": {
            "base_url": "https://api.openai.com",
            "default_model": "gpt-4o",
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com",
            "default_model": "claude-sonnet-4-20250514",
        },
        "google": {
            "base_url": "https://generativelanguage.googleapis.com",
            "default_model": "gemini-2.0-flash",
        },
        "local": {
            "base_url": "http://127.0.0.1:1234",
            "default_model": "auto",
        },
    }

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ):
        """
        Args:
            provider: One of "openai", "anthropic", "google", "local".
            api_key: API key for the provider.
            model: Model name override (uses provider default if empty).
            base_url: Base URL override (uses provider default if empty).
            temperature: Sampling temperature.
            max_tokens: Max tokens in response.
        """
        self.provider = provider.lower()
        if self.provider not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}. Use one of: {list(self.PROVIDERS.keys())}")

        provider_config = self.PROVIDERS[self.provider]
        self.base_url = base_url.rstrip("/") if base_url else provider_config["base_url"]
        self.model = model if model else provider_config["default_model"]
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.session = requests.Session()

        logger.info(
            f"VLMReasoningClient initialized: provider={self.provider}, "
            f"model={self.model}, base_url={self.base_url}"
        )

    def reason(
        self,
        screenshot_path: str,
        som_image_path: Optional[str],
        elements: List[Dict],
        goal: str,
        action_history: List[str],
        step_context: Optional[str] = None,
    ) -> VLMDecision:
        """
        Send images and context to the VLM and get an action decision.

        Args:
            screenshot_path: Path to the raw screenshot.
            som_image_path: Path to OmniParser SOM annotated image (can be None).
            elements: List of element dicts [{id, type, text, bbox, interactivity}, ...].
            goal: Current step goal string.
            action_history: List of recent action description strings.
            step_context: Optional additional context about the step.

        Returns:
            VLMDecision with the chosen action.
        """
        prompt = self._build_prompt(elements, goal, action_history, step_context)

        # Build image list
        images = []
        if os.path.exists(screenshot_path):
            images.append(("raw screenshot", screenshot_path))
        if som_image_path and os.path.exists(som_image_path):
            images.append(("annotated screenshot (SOM)", som_image_path))

        if not images:
            logger.error("No valid images to send to VLM")
            return VLMDecision(reasoning="No screenshots available")

        try:
            if self.provider in ("openai", "local"):
                raw_response = self._call_openai(prompt, images)
            elif self.provider == "anthropic":
                raw_response = self._call_anthropic(prompt, images)
            elif self.provider == "google":
                raw_response = self._call_google(prompt, images)
            else:
                raise ValueError(f"Unsupported provider: {self.provider}")

            decision = self._parse_vlm_response(raw_response)
            logger.info(
                f"VLM decision: action={decision.action_type}, "
                f"element_id={decision.element_id}, "
                f"goal_achieved={decision.goal_achieved}, "
                f"confidence={decision.confidence:.2f}, "
                f"reasoning={decision.reasoning[:80]}"
            )
            return decision

        except Exception as e:
            logger.error(f"VLM reasoning failed: {e}")
            return VLMDecision(reasoning=f"VLM error: {str(e)}")

    def _build_prompt(
        self,
        elements: List[Dict],
        goal: str,
        action_history: List[str],
        step_context: Optional[str] = None,
    ) -> str:
        """Build the user prompt with element list, goal, and history."""
        parts = []

        parts.append(f"Current Goal: \"{goal}\"")

        if step_context:
            parts.append(f"Step Context: {step_context}")

        if elements:
            parts.append(f"\nDetected Elements ({len(elements)} total):")
            for elem in elements:
                eid = elem.get("id", "?")
                etype = elem.get("type", "unknown")
                etext = elem.get("text", "")
                bbox = elem.get("bbox", {})
                interactive = elem.get("interactivity", False)
                bbox_str = f"({bbox.get('x', 0)},{bbox.get('y', 0)}) {bbox.get('w', 0)}x{bbox.get('h', 0)}"
                text_part = f": '{etext}'" if etext else ""
                inter_tag = " (interactive)" if interactive else ""
                parts.append(f"  [{eid}] {etype} at {bbox_str}{text_part}{inter_tag}")
        else:
            parts.append("\nNo UI elements detected by OmniParser.")

        if action_history:
            parts.append(f"\nRecent Actions (last {len(action_history)}):")
            for action in action_history[-5:]:
                parts.append(f"  - {action}")
        else:
            parts.append("\nNo previous actions taken yet.")

        parts.append("\nAnalyze the screenshots and decide the next action.")

        return "\n".join(parts)

    def _encode_image_base64(self, image_path: str) -> str:
        """Read and base64-encode an image file."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _get_image_media_type(self, image_path: str) -> str:
        """Determine MIME type from file extension."""
        ext = os.path.splitext(image_path)[1].lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "image/png")

    def _call_openai(self, prompt: str, images: List[tuple]) -> str:
        """Call OpenAI-compatible API (also used for local/LM Studio)."""
        content = []
        for label, img_path in images:
            b64 = self._encode_image_base64(img_path)
            media_type = self._get_image_media_type(img_path)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            })
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = self.session.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _call_anthropic(self, prompt: str, images: List[tuple]) -> str:
        """Call Anthropic Messages API."""
        content = []
        for label, img_path in images:
            b64 = self._encode_image_base64(img_path)
            media_type = self._get_image_media_type(img_path)
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "temperature": self.temperature,
        }

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        response = self.session.post(
            f"{self.base_url}/v1/messages",
            json=payload,
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        # Anthropic returns content as a list of blocks
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(text_blocks)

    def _call_google(self, prompt: str, images: List[tuple]) -> str:
        """Call Google Gemini generateContent API."""
        parts = []
        for label, img_path in images:
            b64 = self._encode_image_base64(img_path)
            media_type = self._get_image_media_type(img_path)
            parts.append({
                "inline_data": {
                    "mime_type": media_type,
                    "data": b64,
                }
            })
        # System instruction + user prompt combined
        parts.append({"text": SYSTEM_PROMPT + "\n\n" + prompt})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }

        url = (
            f"{self.base_url}/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )

        response = self.session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        # Extract text from Gemini response
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            texts = [p["text"] for p in parts if "text" in p]
            return "\n".join(texts)
        return ""

    def _parse_vlm_response(self, raw_response: str) -> VLMDecision:
        """Parse VLM raw text response into a VLMDecision."""
        # Strip markdown code fences if present
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        # Try direct JSON parse
        try:
            data = json.loads(cleaned)
            return self._dict_to_decision(data)
        except json.JSONDecodeError:
            pass

        # Try to find a JSON object in the response
        json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        match = re.search(json_pattern, cleaned)
        if match:
            try:
                data = json.loads(match.group(0))
                return self._dict_to_decision(data)
            except json.JSONDecodeError:
                pass

        logger.warning(f"Could not parse VLM response as JSON: {raw_response[:200]}")
        return VLMDecision(
            action_type="wait",
            reasoning=f"Failed to parse VLM response: {raw_response[:100]}",
            confidence=0.0,
        )

    def _dict_to_decision(self, data: Dict) -> VLMDecision:
        """Convert a parsed dict to a VLMDecision with validation."""
        valid_actions = {"click", "key", "type", "scroll", "wait", "done"}
        action_type = str(data.get("action_type", "wait")).lower()
        if action_type not in valid_actions:
            logger.warning(f"Unknown action_type '{action_type}', defaulting to 'wait'")
            action_type = "wait"

        element_id = data.get("element_id")
        if element_id is not None:
            try:
                element_id = int(element_id)
            except (ValueError, TypeError):
                element_id = None

        confidence = data.get("confidence", 0.5)
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (ValueError, TypeError):
            confidence = 0.5

        return VLMDecision(
            action_type=action_type,
            element_id=element_id,
            key=data.get("key"),
            text=data.get("text"),
            scroll_direction=data.get("scroll_direction"),
            reasoning=str(data.get("reasoning", "")),
            goal_achieved=bool(data.get("goal_achieved", False)),
            confidence=confidence,
        )

    def test_connection(self) -> bool:
        """Test connectivity to the VLM provider. Returns True if reachable."""
        try:
            if self.provider in ("openai", "local"):
                resp = self.session.get(
                    f"{self.base_url}/v1/models",
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=10,
                )
                return resp.status_code == 200
            elif self.provider == "anthropic":
                # Send a minimal request to check auth
                resp = self.session.post(
                    f"{self.base_url}/v1/messages",
                    json={
                        "model": self.model,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    timeout=10,
                )
                # 200 = success, 401 = bad key, but connection works
                return resp.status_code in (200, 400)
            elif self.provider == "google":
                resp = self.session.get(
                    f"{self.base_url}/v1beta/models?key={self.api_key}",
                    timeout=10,
                )
                return resp.status_code == 200
            return False
        except Exception as e:
            logger.error(f"VLM connection test failed: {e}")
            return False

    def close(self):
        """Close the HTTP session."""
        self.session.close()
        logger.info("VLM reasoning client session closed")
