"""
Omniparser Queue Service Dashboard
Real-time monitoring console with animated visuals and colored logs.
"""

import asyncio
import httpx
from datetime import datetime
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.style import Style
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.align import Align
from collections import deque
import argparse
import sys

# Initialize console
console = Console()

# Color scheme
COLORS = {
    "healthy": "green",
    "unhealthy": "red",
    "idle": "cyan",
    "busy": "yellow",
    "header": "bold magenta",
    "success": "green",
    "error": "red",
    "warning": "yellow",
    "info": "blue",
    "request_in": "bold cyan",
    "request_out": "bold green",
}

# Request log with timestamps
request_log = deque(maxlen=15)
event_log = deque(maxlen=10)


def create_header() -> Panel:
    """Create the header panel."""
    header_text = Text()
    header_text.append("🚀 ", style="bold")
    header_text.append("OMNIPARSER QUEUE SERVICE", style="bold magenta")
    header_text.append(" - ", style="dim")
    header_text.append("Load Balancer Dashboard", style="italic cyan")

    time_text = Text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), style="dim white")

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(header_text, time_text)

    return Panel(grid, style="bold blue", height=3)


def create_server_panel(stats: dict) -> Panel:
    """Create server status panel with visual indicators."""
    if not stats or "per_server" not in stats:
        return Panel(Text("Connecting...", style="dim"), title="[bold]Servers", border_style="dim")

    table = Table(show_header=True, header_style="bold white", expand=True, box=None)
    table.add_column("Server", style="bold", min_width=12)
    table.add_column("Status", justify="center", min_width=10)
    table.add_column("State", justify="center", min_width=8)
    table.add_column("Reqs", justify="right", min_width=6)
    table.add_column("OK", justify="right", min_width=6)
    table.add_column("Fail", justify="right", min_width=6)
    table.add_column("Avg Time", justify="right", min_width=9)

    for name, server in stats.get("per_server", {}).items():
        # Status indicator
        status = server.get("state", "unknown")
        if status == "idle":
            status_text = Text("● IDLE", style="bold green")
            state_style = "green"
        elif status == "busy":
            status_text = Text("◉ BUSY", style="bold yellow")
            state_style = "yellow"
        else:
            status_text = Text("○ DOWN", style="bold red")
            state_style = "red"

        # Current request indicator
        current_req = server.get("current_request_id")
        state_text = Text(f"[{current_req[:6]}]" if current_req else "—", style=state_style)

        # Stats
        total = server.get("total_requests", 0)
        success = server.get("successful_requests", 0)
        failed = server.get("failed_requests", 0)
        avg_time = server.get("average_processing_time", 0)

        success_text = Text(str(success), style="green")
        failed_text = Text(str(failed), style="red" if failed > 0 else "dim")

        table.add_row(
            Text(name, style="bold cyan"),
            status_text,
            state_text,
            str(total),
            success_text,
            failed_text,
            f"{avg_time:.2f}s"
        )

    return Panel(table, title="[bold magenta]⚡ Server Pool", border_style="magenta")


def create_flow_diagram(stats: dict) -> Panel:
    """Create animated request flow diagram supporting any number of servers."""
    if not stats:
        return Panel(Text("Loading...", style="dim"), title="Request Flow", border_style="dim")

    queue_size = stats.get("current_queue_size", 0)
    per_server = stats.get("per_server", {})
    server_names = list(per_server.keys())
    num_servers = len(server_names)

    lines = []

    # All elements aligned to a common center point (position 30 in a 60-char ref width)
    ref_w = 60
    center = ref_w // 2
    box_w = 15  # width of ┌─────────────┐
    bp = " " * (center - box_w // 2)  # box left padding
    cp = " " * center  # center pipe padding

    # Clients section
    lines.append(Text(f"{bp}┌─────────────┐", style="cyan"))
    lines.append(Text(f"{bp}│   CLIENTS   │", style="bold cyan"))
    lines.append(Text(f"{bp}└──────┬──────┘", style="cyan"))
    lines.append(Text(f"{cp}│", style="dim"))
    lines.append(Text(f"{cp}▼", style="bold white"))

    # Queue section
    queue_color = "green" if queue_size == 0 else "yellow" if queue_size < 10 else "red"
    lines.append(Text(f"{bp}┌─────────────┐", style=queue_color))
    lines.append(Text(f"{bp}│  QUEUE [{queue_size:3d}] │", style=f"bold {queue_color}"))
    lines.append(Text(f"{bp}└──────┬──────┘", style=queue_color))
    lines.append(Text(f"{cp}│", style="dim"))

    # Load balancer
    lines.append(Text(f"{bp}┌──────┴──────┐", style="magenta"))
    lines.append(Text(f"{bp}│ LOAD BALANCE│", style="bold magenta"))
    lines.append(Text(f"{bp}└──────┬──────┘", style="magenta"))

    if num_servers == 0:
        lines.append(Text(f"{cp}│", style="dim"))
        lines.append(Text(f"{' ' * (center - 5)}No servers", style="dim red"))
    else:
        lines.append(Text(f"{cp}│", style="dim"))

        # Server boxes - 8 chars per column, centered on same center point
        col_w = 8
        pad = " " * max(0, (ref_w - num_servers * col_w) // 2)

        # Arrows line - ▼ at position 4 to align with box center
        arrow_line = Text()
        arrow_line.append(pad + "    ▼   " * num_servers, style="bold white")
        lines.append(arrow_line)

        # Top border of boxes
        top_line = Text()
        top_line.append(pad + " ┌─────┐" * num_servers, style="white")
        lines.append(top_line)

        # Server labels with colors
        label_line = Text()
        label_line.append(pad)
        for i, name in enumerate(server_names):
            server = per_server.get(name, {})
            state = server.get("state", "unknown")
            color = "green" if state == "idle" else "yellow" if state == "busy" else "red"
            label_line.append(f" │SRV-{i+1}│", style=f"bold {color}")
        lines.append(label_line)

        # Bottom border of boxes
        bottom_line = Text()
        bottom_line.append(pad + " └─────┘" * num_servers, style="white")
        lines.append(bottom_line)

        # Status indicators (request IDs or state)
        status_line = Text()
        status_line.append(pad)
        for name in server_names:
            server = per_server.get(name, {})
            state = server.get("state", "unknown")
            req_id = server.get("current_request_id")
            if req_id:
                status_line.append(f"[{req_id[:4]}]".center(col_w), style="yellow")
            elif state == "idle":
                status_line.append("IDLE".center(col_w), style="dim green")
            elif state == "unhealthy":
                status_line.append("DOWN".center(col_w), style="dim red")
            else:
                status_line.append("──".center(col_w), style="dim")
        lines.append(status_line)

    content = Text("\n").join(lines)
    return Panel(content, title="[bold blue]📊 Request Flow", border_style="blue")


def create_stats_panel(stats: dict) -> Panel:
    """Create aggregate statistics panel."""
    if not stats or "aggregate" not in stats:
        return Panel(Text("Loading...", style="dim"), title="Statistics", border_style="dim")

    agg = stats.get("aggregate", {})

    table = Table(show_header=False, box=None, expand=True)
    table.add_column("Metric", style="dim", min_width=20)
    table.add_column("Value", justify="right", style="bold")

    total = agg.get("total_requests", 0)
    success = agg.get("successful_requests", 0)
    failed = agg.get("failed_requests", 0)
    success_rate = (success / total * 100) if total > 0 else 100

    table.add_row("Total Requests", Text(str(total), style="bold white"))
    table.add_row("Successful", Text(str(success), style="bold green"))
    table.add_row("Failed", Text(str(failed), style="bold red" if failed > 0 else "dim"))
    table.add_row("Success Rate", Text(f"{success_rate:.1f}%", style="bold green" if success_rate >= 95 else "yellow"))
    table.add_row("", "")
    table.add_row("Total Servers", str(agg.get("total_servers", 0)))
    table.add_row("Healthy", Text(str(agg.get("healthy_servers", 0)), style="green"))
    table.add_row("Busy", Text(str(agg.get("busy_servers", 0)), style="yellow"))
    table.add_row("Unhealthy", Text(str(agg.get("unhealthy_servers", 0)), style="red" if agg.get("unhealthy_servers", 0) > 0 else "dim"))
    table.add_row("Queue Size", Text(str(stats.get("current_queue_size", 0)), style="cyan"))

    return Panel(table, title="[bold green]📈 Statistics", border_style="green")


def create_request_log_panel() -> Panel:
    """Create scrolling request log panel."""
    if not request_log:
        content = Text("Waiting for requests...", style="dim italic")
    else:
        lines = []
        for entry in list(request_log)[-12:]:
            lines.append(entry)
        content = Text("\n").join(lines)

    return Panel(content, title="[bold cyan]📝 Request Log", border_style="cyan")


def create_event_log_panel() -> Panel:
    """Create event log panel for system events."""
    if not event_log:
        content = Text("No events yet...", style="dim italic")
    else:
        lines = []
        for entry in list(event_log)[-8:]:
            lines.append(entry)
        content = Text("\n").join(lines)

    return Panel(content, title="[bold yellow]⚡ Events", border_style="yellow")


def create_layout() -> Layout:
    """Create the dashboard layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=16),
    )

    layout["body"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=1),
    )

    layout["left"].split_column(
        Layout(name="servers", size=8),
        Layout(name="flow"),
    )

    layout["right"].split_column(
        Layout(name="stats"),
    )

    layout["footer"].split_row(
        Layout(name="request_log", ratio=2),
        Layout(name="event_log", ratio=1),
    )

    return layout


async def fetch_stats(client: httpx.AsyncClient, base_url: str) -> dict:
    """Fetch stats from the queue service."""
    try:
        response = await client.get(f"{base_url}/stats", timeout=5.0)
        return response.json()
    except Exception as e:
        return None


async def monitor_service(base_url: str, refresh_rate: float = 0.5):
    """Main monitoring loop."""
    layout = create_layout()
    last_stats = None
    last_total_requests = 0

    async with httpx.AsyncClient() as client:
        with Live(layout, console=console, refresh_per_second=4, screen=True) as live:
            while True:
                try:
                    # Fetch current stats
                    stats = await fetch_stats(client, base_url)

                    if stats:
                        # Check for new requests
                        current_total = stats.get("aggregate", {}).get("total_requests", 0)
                        if current_total > last_total_requests:
                            new_requests = current_total - last_total_requests
                            timestamp = datetime.now().strftime("%H:%M:%S")

                            # Find which server processed it
                            for name, server in stats.get("per_server", {}).items():
                                if server.get("state") == "busy":
                                    req_id = server.get("current_request_id", "unknown")
                                    entry = Text()
                                    entry.append(f"[{timestamp}] ", style="dim")
                                    entry.append("→ ", style="bold cyan")
                                    entry.append(f"REQ ", style="cyan")
                                    entry.append(f"{req_id[:8]} ", style="bold white")
                                    entry.append(f"→ {name}", style="yellow")
                                    request_log.append(entry)

                            last_total_requests = current_total

                        # Check for completed requests
                        if last_stats:
                            for name, server in stats.get("per_server", {}).items():
                                last_server = last_stats.get("per_server", {}).get(name, {})
                                if server.get("successful_requests", 0) > last_server.get("successful_requests", 0):
                                    timestamp = datetime.now().strftime("%H:%M:%S")
                                    avg_time = server.get("average_processing_time", 0)
                                    entry = Text()
                                    entry.append(f"[{timestamp}] ", style="dim")
                                    entry.append("✓ ", style="bold green")
                                    entry.append(f"DONE ", style="green")
                                    entry.append(f"on {name} ", style="bold white")
                                    entry.append(f"({avg_time:.2f}s)", style="dim")
                                    request_log.append(entry)

                                # Check for server state changes
                                if server.get("state") != last_server.get("state"):
                                    timestamp = datetime.now().strftime("%H:%M:%S")
                                    new_state = server.get("state")
                                    entry = Text()
                                    entry.append(f"[{timestamp}] ", style="dim")
                                    if new_state == "unhealthy":
                                        entry.append("⚠ ", style="bold red")
                                        entry.append(f"{name} ", style="bold white")
                                        entry.append("went UNHEALTHY", style="red")
                                    elif new_state == "idle" and last_server.get("state") == "unhealthy":
                                        entry.append("✓ ", style="bold green")
                                        entry.append(f"{name} ", style="bold white")
                                        entry.append("RECOVERED", style="green")
                                    elif new_state == "idle":
                                        entry.append("✓ ", style="bold green")
                                        entry.append(f"{name} ", style="bold white")
                                        entry.append("completed", style="green")
                                    elif new_state == "busy":
                                        entry.append("● ", style="bold yellow")
                                        entry.append(f"{name} ", style="bold white")
                                        entry.append("processing", style="yellow")
                                    event_log.append(entry)

                        last_stats = stats

                    # Update layout
                    layout["header"].update(create_header())
                    layout["servers"].update(create_server_panel(stats))
                    layout["flow"].update(create_flow_diagram(stats))
                    layout["stats"].update(create_stats_panel(stats))
                    layout["request_log"].update(create_request_log_panel())
                    layout["event_log"].update(create_event_log_panel())

                except Exception as e:
                    error_panel = Panel(
                        Text(f"Connection error: {e}", style="bold red"),
                        title="[bold red]Error",
                        border_style="red"
                    )
                    layout["servers"].update(error_panel)

                await asyncio.sleep(refresh_rate)


def main():
    parser = argparse.ArgumentParser(description="Omniparser Queue Service Dashboard")
    parser.add_argument(
        "--url",
        type=str,
        default="http://localhost:9000",
        help="Queue service URL (default: http://localhost:9000)"
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=0.5,
        help="Refresh rate in seconds (default: 0.5)"
    )

    args = parser.parse_args()

    console.print(Panel(
        Text("Starting Omniparser Dashboard...", style="bold cyan"),
        border_style="cyan"
    ))
    console.print(f"Connecting to: [bold]{args.url}[/bold]")
    console.print("Press [bold]Ctrl+C[/bold] to exit\n")

    try:
        asyncio.run(monitor_service(args.url, args.refresh))
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Dashboard stopped.[/bold yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
