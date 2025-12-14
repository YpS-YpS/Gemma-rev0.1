# Katana - Game Automation Framework

**Version: Nightly Release (December 2024)**

Katana (formerly Gemma) is a **computer vision-based game automation and benchmarking framework**. It uses a distributed Client-Server architecture where a Controller machine orchestrates multiple Systems Under Test (SUTs) running game workloads.

---

## 🚀 Key Features

| Feature | Description |
|---------|-------------|
| **Multi-SUT Control** | Manage multiple gaming machines from a single controller with independent automation threads |
| **Computer Vision** | Vision Language Models (OmniParser, Gemini, Qwen) for UI element detection and interaction |
| **Campaign Mode** | Queue multiple games with configurable run counts and delays |
| **Step-Based Automation** | YAML-defined automation steps with find-action patterns |
| **State Machine Automation** | Complex game flow support with state transitions |
| **Live Preview** | Real-time screenshot streaming from SUTs at configurable FPS |
| **Steam Integration** | Auto-login, path resolution from Steam App IDs |
| **Robust Game Launching** | Process tracking, foreground enforcement, startup wait handling |

---

## 📂 Project Structure

```
Katana/
├── gui_app_multi_sut.py      # Main Controller GUI (Tkinter)
├── workflow_builder.py       # Visual workflow/config builder tool
├── main.py                   # Legacy single-SUT automation script
│
├── modules/                  # Core automation logic
│   ├── network.py            # HTTP client for SUT communication
│   ├── screenshot.py         # Screenshot capture and caching
│   ├── game_launcher.py      # Game process launching with Steam support
│   ├── simple_automation.py  # Step-based automation engine
│   ├── decision_engine.py    # State machine automation engine
│   ├── omniparser_client.py  # OmniParser vision model client
│   ├── gemma_client.py       # Gemma/LM Studio vision client
│   ├── qwen_client.py        # Qwen VL vision client
│   └── annotator.py          # Screenshot annotation utilities
│
├── sut_service_installer/    # SUT Agent files
│   ├── gemma_service_0.2.py  # ⭐ Latest SUT agent with CPU optimizations
│   ├── gemma_service_0.1.py  # Legacy SUT agent  
│   └── requirements.txt      # SUT dependencies
│
├── config/                   # Configuration files
│   ├── games/                # Game-specific YAML configs
│   │   ├── cyberpunk2077.yaml
│   │   ├── cs2_benchmark.yaml
│   │   ├── rdr2.yaml
│   │   └── ...
│   └── campaigns/            # Campaign definitions
│
└── omniparser_queue_service.py  # Batch OmniParser processing
```

---

## 🔧 Installation & Setup

### Prerequisites
- Python 3.10+
- Windows 10/11 (SUT machines)
- [OmniParser](https://github.com/microsoft/OmniParser) running on localhost:9000

### 1. Controller Machine Setup

```bash
# Clone repository
git clone https://github.com/YourOrg/Katana.git
cd Katana

# Install dependencies
pip install tkinter pillow pyyaml requests

# Run the controller
python gui_app_multi_sut.py
```

### 2. SUT (Gaming Machine) Setup

```bash
# Copy sut_service_installer folder to gaming machine
cd sut_service_installer

# Install dependencies
pip install -r requirements.txt

# Run as Administrator (required for input simulation)
python gemma_service_0.2.py
```

> **Note**: The SUT agent listens on port 8080 by default.

---

## 🎮 Quick Start

1. **Start OmniParser** on localhost:9000
2. **Start SUT Agent** on your gaming machine as Administrator
3. **Launch Controller**: `python gui_app_multi_sut.py`
4. **Add SUT**: Enter IP and port of your gaming machine
5. **Select Config**: Choose a game YAML from config/games/
6. **Start Automation**: Click "Start" and watch the magic!

---

## 📝 Configuration Files

### Game Config Example (`config/games/cyberpunk2077.yaml`)

```yaml
metadata:
  game_name: Cyberpunk2077
  path: C:\Steam\steamapps\common\Cyberpunk 2077\bin\x64\Cyberpunk2077.exe
  process_id: Cyberpunk2077
  startup_wait: 80
  benchmark_duration: 100

steps:
  1:
    description: PRESS SPACE TO CONTINUE
    find:
      type: any
      text: SPACE
      text_match: contains
    action:
      type: key
      key: space
    timeout: 20
  
  2:
    description: CLICK ON SETTINGS
    find:
      type: any
      text: SETTINGS
    action:
      type: click
      button: left
```

---

## 📋 File Changelog (This Release)

| File | Change | Reason |
|------|--------|--------|
| `gui_app_multi_sut.py` | Modified | Enhanced multi-SUT control, improved logging, campaign mode fixes |
| `modules/network.py` | Modified | Steam login support, improved error handling |
| `modules/game_launcher.py` | Modified | Process tracking, foreground enforcement with retry logic |
| `modules/simple_automation.py` | Modified | Progress callbacks, improved step execution |
| `sut_service_installer/gemma_service_0.2.py` | **NEW** | CPU-optimized SUT agent with Event.wait() instead of polling |
| `sut_service_installer/requirements.txt` | **NEW** | Dependencies for SUT agent |
| `config/games/rdr2.yaml` | **NEW** | Red Dead Redemption 2 automation config |
| `config/games/Cyberpunk2077-test.yaml` | Modified | Updated benchmark workflow |
| `workflow_builder.py` | Modified | Visual improvements, step editor enhancements |

---

## ⚠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| "Access Denied" when launching games | Run `gemma_service_0.2.py` as **Administrator** |
| Game window not focusing | Check logs for retry attempts; increase `startup_wait` in config |
| OmniParser connection failed | Ensure OmniParser is running on localhost:9000 |
| High CPU on SUT | Use `gemma_service_0.2.py` which uses Event.wait() instead of polling |

---

## 📄 License

MIT License - See [LICENSE](LICENSE) for details.

---

**Built with ❤️ for automated game benchmarking**
