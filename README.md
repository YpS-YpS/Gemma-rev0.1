# Gemma Rev0.1 - Game Automation Framework

This repository contains the **Gemma Automation Framework**, a computer vision-based system for automating and benchmarking game UI navigation. It uses a Client-Server architecture to separate the decision-making "Brain" from the action-taking "Hands".

## 🚀 Key Features

*   **Multi-SUT Support**: Control multiple Systems Under Test (SUTs) from a single controller.
*   **Computer Vision**: Uses Vision Language Models (Gemini, Qwen, OmniParser) to "see" and interact with game UIs.
*   **Data-Driven Automation**: Define game states and transitions using simple YAML configuration files.
*   **Robust Game Launching**: 
    *   **Steam Integration**: Auto-resolves installation paths from Steam App IDs.
    *   **Strict Foreground Enforcement**: Ensures the game window is actually visible and active before proceeding (with retry logic).
    *   **Process Tracking**: Monitors specific process names/IDs for accurate status reporting.

## 📂 Repository Structure

*   `sut_service_installer/gemma_service_0.1.py`: **The SUT Agent**. Runs on the gaming machine. Handles input (mouse/keyboard), game launching, and screen capture.
*   `gui_app_multi_sut.py`: **The Controller**. Runs on the host machine. Connects to SUTs, sends commands, and runs the automation logic.
*   `modules/`: Shared logic for networking, game launching, and automation.
*   `config/games/`: YAML configuration files defining game states (e.g., `Start Menu` -> `Click Play`).

## 🛠️ Setup & Usage

### 1. On the Gaming Machine (SUT)

1.  Requires **Python 3.10+** and Administrator privileges (for input simulation).
2.  Install dependencies:
    ```bash
    pip install flask pyautogui psutil pywin32 requests
    ```
3.  Run the client:
    ```bash
    python sut_service_installer/gemma_service_0.1.py
    ```
    *   *Note: This service listens on port 8080 by default.*

### 2. On the Controller Machine

1.  Update `mysuts.json` with the IP address of your SUT:
    ```json
    {
      "suts": [
        {
          "name": "My Gaming PC",
          "ip": "192.168.1.100",
          "port": 8080
        }
      ]
    }
    ```
2.  Run the GUI:
    ```bash
    python gui_app_multi_sut.py
    ```
3.  Select your SUT, load a Game Configuration, and click **"Start Automation"**.

## 🎮 Game Configuration

Configurations are stored in `config/games/`. Example (`cs2.yaml`):

```yaml
states:
  main_menu:
    identifiers:
      - text: "PLAY"
    transitions:
      - action: "click"
        target: "PLAY"
        next_state: "play_menu"
```

## ⚠️ Troubleshooting

*   **"Access Denied" when launching games**: Ensure `gemma_service_0.1.py` is running as **Administrator**.
*   **Game window not focusing**: The client includes a robust `AttachThreadInput` mechanism. If it fails initially, it will retry after 3 seconds. Check logs for `[WARN] Initial foreground attempt failed`.

---

## 📝 Recent Changes / Changelog

*   Fixed individual SUT logging (removed shared logger to prevent race conditions).
*   Added multi-game queue addition (Gaming Campaign).
*   Improved UI with colored layout for visual distinction.
*   Fixed SUT config additions and saving.
*   Added individual SUT preview (0.25 fps).
*   Structured logging for campaign vs. single game mode.
*   Added JSON support for saving/loading campaigns.
