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

# Initialize console - auto-detect terminal size
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

# Request log with timestamps - increased for larger display
request_log = deque(maxlen=20)
event_log = deque(maxlen=15)


def create_header() -> Panel:
    """Create simple header."""
    header_text = Text()
    header_text.append("OMNIPARSER QUEUE SERVICE", style="bold magenta")
    header_text.append("  │  ", style="dim")
    header_text.append(datetime.now().strftime("%H:%M:%S"), style="bold white")

    return Panel(Align.center(header_text), style="bold blue", height=3)


def create_server_panel(stats: dict) -> Panel:
    """Create LARGE server status panel for visibility from distance."""
    if not stats or "per_server" not in stats:
        return Panel(
            Align.center(Text("\n\n\nCONNECTING...\n\n\n", style="bold dim")),
            title="[bold]SERVERS",
            border_style="dim"
        )

    from rich.box import HEAVY

    table = Table(
        show_header=True,
        header_style="bold white on dark_blue",
        expand=True,
        box=HEAVY,
        padding=(1, 3),
        row_styles=["", "dim"]
    )
    table.add_column("SERVER", style="bold cyan", justify="center")
    table.add_column("STATUS", justify="center")
    table.add_column("REQUESTS", justify="center")
    table.add_column("SUCCESS", justify="center")
    table.add_column("FAILED", justify="center")
    table.add_column("AVG TIME", justify="center")

    for name, server in stats.get("per_server", {}).items():
        # Status indicator - BIG and BOLD
        status = server.get("state", "unknown")
        if status == "idle":
            status_text = Text("■ IDLE ■", style="bold green on default")
        elif status == "busy":
            status_text = Text("▶ BUSY ▶", style="bold yellow on default")
        else:
            status_text = Text("✖ DOWN ✖", style="bold red on default")

        # Stats
        total = server.get("total_requests", 0)
        success = server.get("successful_requests", 0)
        failed = server.get("failed_requests", 0)
        avg_time = server.get("average_processing_time", 0)

        table.add_row(
            Text(name.upper(), style="bold cyan"),
            status_text,
            Text(str(total), style="bold white"),
            Text(str(success), style="bold green"),
            Text(str(failed), style="bold red" if failed > 0 else "dim"),
            Text(f"{avg_time:.1f}s", style="bold white")
        )

    # Add queue info at bottom
    queue_size = stats.get("current_queue_size", 0)
    queue_color = "green" if queue_size == 0 else "yellow" if queue_size < 5 else "red"

    content = Group(
        table,
        Text(""),
        Align.center(Text(f"━━━  QUEUE: {queue_size}  ━━━", style=f"bold {queue_color}"))
    )

    return Panel(content, title="[bold magenta]⚡ SERVER POOL ⚡", border_style="magenta", padding=(1, 2))


def create_flow_diagram(stats: dict) -> Panel:
    """Create animated request flow diagram supporting any number of servers."""
    if not stats:
        return Panel(Text("Loading...", style="bold dim"), title="Request Flow", border_style="dim", padding=(1, 2))

    queue_size = stats.get("current_queue_size", 0)

    # Build the flow diagram - LARGER boxes for visibility
    lines = []

    # Clients section - larger
    lines.append(Text("         ┌───────────────────┐", style="bold cyan"))
    lines.append(Text("         │      CLIENTS      │", style="bold cyan"))
    lines.append(Text("         └─────────┬─────────┘", style="bold cyan"))
    lines.append(Text("                   │", style="bold white"))
    lines.append(Text("                   ▼", style="bold white"))

    # Queue section with dynamic indicator - larger
    queue_color = "green" if queue_size == 0 else "yellow" if queue_size < 10 else "red"

    lines.append(Text("         ┌───────────────────┐", style=f"bold {queue_color}"))
    lines.append(Text(f"         │    QUEUE [{queue_size:4d}]    │", style=f"bold {queue_color}"))
    lines.append(Text("         └─────────┬─────────┘", style=f"bold {queue_color}"))
    lines.append(Text("                   │", style="bold white"))

    # Load balancer - larger
    lines.append(Text("         ┌─────────┴─────────┐", style="bold magenta"))
    lines.append(Text("         │   LOAD BALANCER   │", style="bold magenta"))
    lines.append(Text("         └─────────┬─────────┘", style="bold magenta"))

    # Servers - dynamic layout for any number
    per_server = stats.get("per_server", {})
    server_names = list(per_server.keys())
    num_servers = len(server_names)

    if num_servers == 0:
        lines.append(Text("                   │", style="bold white"))
        lines.append(Text("           No servers", style="bold red"))
    else:
        # Create branching lines
        lines.append(Text("                   │", style="bold white"))

        # Build server boxes dynamically - LARGER
        # Arrows line
        arrow_line = Text()
        arrows = "    ▼    " * num_servers
        arrow_line.append(arrows.center(80), style="bold white")
        lines.append(arrow_line)

        # Top border of boxes - LARGER
        top_line = Text()
        top_parts = "  ┌─────────┐" * num_servers
        top_line.append(top_parts.center(80), style="bold white")
        lines.append(top_line)

        # Server labels with colors - LARGER
        label_line = Text()
        label_line.append(" " * ((80 - num_servers * 12) // 2))  # centering
        for i, name in enumerate(server_names):
            server = per_server.get(name, {})
            state = server.get("state", "unknown")
            color = "green" if state == "idle" else "yellow" if state == "busy" else "red"
            label_line.append(f"  │ SRV-{i+1} │", style=f"bold {color}")
        lines.append(label_line)

        # Bottom border of boxes - LARGER
        bottom_line = Text()
        bottom_parts = "  └─────────┘" * num_servers
        bottom_line.append(bottom_parts.center(80), style="bold white")
        lines.append(bottom_line)

        # Status indicators (request IDs or state) - LARGER
        status_line = Text()
        status_line.append(" " * ((80 - num_servers * 12) // 2))  # centering
        for name in server_names:
            server = per_server.get(name, {})
            state = server.get("state", "unknown")
            req_id = server.get("current_request_id")
            if req_id:
                status_line.append(f"  [{req_id[:6]}]  ", style="bold yellow")
            elif state == "idle":
                status_line.append("    IDLE    ", style="bold green")
            elif state == "unhealthy":
                status_line.append("    DOWN    ", style="bold red")
            else:
                status_line.append("    ━━━━    ", style="dim")
        lines.append(status_line)

    content = Text("\n").join(lines)
    return Panel(Align.center(content), title="[bold blue]📊 Request Flow", border_style="blue", height=22, padding=(1, 2))


def create_stats_panel(stats: dict) -> Panel:
    """Create LARGE statistics panel for visibility from distance."""
    if not stats or "aggregate" not in stats:
        return Panel(
            Align.center(Text("\n\nLOADING...\n\n", style="bold dim")),
            title="[bold]STATS",
            border_style="dim"
        )

    agg = stats.get("aggregate", {})

    total = agg.get("total_requests", 0)
    success = agg.get("successful_requests", 0)
    failed = agg.get("failed_requests", 0)
    success_rate = (success / total * 100) if total > 0 else 100
    healthy = agg.get("healthy_servers", 0)
    busy = agg.get("busy_servers", 0)
    unhealthy = agg.get("unhealthy_servers", 0)

    # Build large stat display
    lines = []
    lines.append(Text(""))
    lines.append(Text(f"  TOTAL", style="bold white"))
    lines.append(Text(f"    {total}", style="bold white"))
    lines.append(Text(""))
    lines.append(Text(f"  SUCCESS", style="bold green"))
    lines.append(Text(f"    {success}", style="bold green"))
    lines.append(Text(""))
    lines.append(Text(f"  FAILED", style="bold red" if failed > 0 else "dim"))
    lines.append(Text(f"    {failed}", style="bold red" if failed > 0 else "dim"))
    lines.append(Text(""))
    lines.append(Text(f"  RATE", style="bold green" if success_rate >= 95 else "bold yellow"))
    lines.append(Text(f"    {success_rate:.0f}%", style="bold green" if success_rate >= 95 else "bold yellow"))
    lines.append(Text(""))
    lines.append(Text("  ─────────", style="dim"))
    lines.append(Text(""))
    lines.append(Text(f"  HEALTHY: {healthy}", style="bold green"))
    lines.append(Text(f"  BUSY: {busy}", style="bold yellow"))
    lines.append(Text(f"  DOWN: {unhealthy}", style="bold red" if unhealthy > 0 else "dim"))

    content = Text("\n").join(lines)
    return Panel(content, title="[bold green]📈 STATS", border_style="green", padding=(1, 2))


def create_request_log_panel() -> Panel:
    """Create scrolling request log panel."""
    if not request_log:
        content = Text("Waiting for requests...", style="bold dim")
    else:
        lines = []
        for entry in list(request_log)[-10:]:
            lines.append(entry)
        content = Text("\n").join(lines)

    return Panel(content, title="[bold cyan]REQUEST LOG", border_style="cyan", padding=(0, 1))


def create_event_log_panel() -> Panel:
    """Create event log panel for system events."""
    if not event_log:
        content = Text("No events...", style="bold dim")
    else:
        lines = []
        for entry in list(event_log)[-10:]:
            lines.append(entry)
        content = Text("\n").join(lines)

    return Panel(content, title="[bold yellow]EVENTS", border_style="yellow", padding=(0, 1))


def create_layout() -> Layout:
    """Create a SIMPLE, LARGE dashboard layout for visibility from distance."""
    layout = Layout()

    # Simple 2-row layout - top for status, bottom for logs
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="logs", size=15),
    )

    # Main area: servers on left, stats on right
    layout["main"].split_row(
        Layout(name="servers", ratio=3),
        Layout(name="stats", ratio=1),
    )

    # Logs at bottom
    layout["logs"].split_row(
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
                                    elif new_state == "busy":
                                        entry.append("● ", style="bold yellow")
                                        entry.append(f"{name} ", style="bold white")
                                        entry.append("processing", style="yellow")
                                    event_log.append(entry)

                        last_stats = stats

                    # Update layout - simplified for visibility
                    layout["header"].update(create_header())
                    layout["servers"].update(create_server_panel(stats))
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
