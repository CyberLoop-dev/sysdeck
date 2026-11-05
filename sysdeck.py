"""sysdeck: a sci-fi system dashboard for your terminal.

CPU per core, RAM, disks, network speed, battery and top processes,
live, with sparklines. Works on Windows, Linux and macOS.
"""

import argparse
import platform
import socket
import time
from collections import deque

import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

__version__ = "1.0.0"

SPARKS = "▁▂▃▄▅▆▇█"

THEMES = {
    "cyber": {"main": "cyan", "accent": "magenta", "warn": "yellow", "bad": "red", "dim": "grey42"},
    "matrix": {"main": "green", "accent": "bright_green", "warn": "yellow", "bad": "red", "dim": "grey35"},
    "amber": {"main": "orange1", "accent": "gold1", "warn": "yellow", "bad": "red", "dim": "grey39"},
}


# ---------- small pure helpers (unit tested) ----------

def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_rate(n: float) -> str:
    return f"{human_bytes(n)}/s"


def format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def rate(previous: float, current: float, seconds: float) -> float:
    """Bytes per second between two counter readings (never negative)."""
    if seconds <= 0:
        return 0.0
    return max(0.0, (current - previous) / seconds)


def sparkline(values, width: int, maximum: float = None) -> str:
    vals = list(values)[-width:]
    if not vals:
        return " " * width
    top = maximum if maximum else max(max(vals), 1e-9)
    chars = []
    for v in vals:
        ratio = min(max(v / top, 0.0), 1.0)
        chars.append(SPARKS[int(round(ratio * (len(SPARKS) - 1)))])
    return "".join(chars).rjust(width, " ")


def level_color(percent: float, theme: dict) -> str:
    if percent >= 90:
        return theme["bad"]
    if percent >= 70:
        return theme["warn"]
    return theme["main"]


def bar(percent: float, width: int, theme: dict, label: bool = True) -> Text:
    percent = max(0.0, min(100.0, percent))
    width = max(width, 4)
    filled = round(width * percent / 100)
    text = Text()
    text.append("█" * filled, style=level_color(percent, theme))
    text.append("░" * (width - filled), style=theme["dim"])
    if label:
        text.append(f" {percent:5.1f}%")
    return text


# ---------- data collection ----------

class Sampler:
    def __init__(self, history: int = 60):
        self.cpu_history = deque(maxlen=history)
        self.down_history = deque(maxlen=history)
        self.up_history = deque(maxlen=history)
        self.cores = psutil.cpu_percent(percpu=True)  # primes the counters
        self.cpu_total = 0.0
        self.net_down = self.net_up = 0.0
        self.disk_read = self.disk_write = 0.0
        self._last_time = time.time()
        self._last_net = psutil.net_io_counters()
        self._last_disk = psutil.disk_io_counters()
        self.cpu_count = psutil.cpu_count() or 1

    def sample(self):
        now = time.time()
        dt = now - self._last_time
        self._last_time = now

        self.cores = psutil.cpu_percent(percpu=True)
        self.cpu_total = sum(self.cores) / len(self.cores) if self.cores else 0.0
        self.cpu_history.append(self.cpu_total)

        net = psutil.net_io_counters()
        if net and self._last_net:
            self.net_down = rate(self._last_net.bytes_recv, net.bytes_recv, dt)
            self.net_up = rate(self._last_net.bytes_sent, net.bytes_sent, dt)
        self._last_net = net
        self.down_history.append(self.net_down)
        self.up_history.append(self.net_up)

        disk = psutil.disk_io_counters()
        if disk and self._last_disk:
            self.disk_read = rate(self._last_disk.read_bytes, disk.read_bytes, dt)
            self.disk_write = rate(self._last_disk.write_bytes, disk.write_bytes, dt)
        self._last_disk = disk

    def top_processes(self, count: int):
        rows = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            info = proc.info
            name = info.get("name") or "?"
            if info["pid"] == 0 or name == "System Idle Process":
                continue
            rows.append(
                (
                    info["pid"],
                    name,
                    (info.get("cpu_percent") or 0.0) / self.cpu_count,
                    info.get("memory_percent") or 0.0,
                )
            )
        rows.sort(key=lambda r: (r[2], r[3]), reverse=True)
        return rows[:count]


IGNORED_FS = {"squashfs", "iso9660", "tmpfs", "devtmpfs", "overlay"}


def disk_rows(limit: int = 4):
    rows = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or part.fstype == "" or part.fstype.lower() in IGNORED_FS:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        if usage.total < 512 * 1024 * 1024:  # skip tiny system mounts
            continue
        rows.append((part.mountpoint, usage))
        if len(rows) >= limit:
            break
    return rows


def battery_info():
    getter = getattr(psutil, "sensors_battery", None)
    return getter() if getter else None


# ---------- rendering ----------

PCT_WIDTH = 7  # " 100.0%"


def panel(title: str, body, theme: dict) -> Panel:
    return Panel(body, title=f"[bold {theme['accent']}]{title}[/]", border_style=theme["main"], padding=(0, 1))


def render(sampler: Sampler, theme: dict, procs: int, width: int):
    inner = max(width // 2 - 4, 30)  # usable text width inside one half-width panel

    def bar_width(prefix: int) -> int:
        return max(inner - prefix - PCT_WIDTH - 1, 6)

    # header
    header = Text()
    header.append("◢ SYSDECK ◣  ", style=f"bold {theme['accent']}")
    header.append(f"{socket.gethostname()}  •  {platform.system()} {platform.release()}  •  ", style=theme["main"])
    header.append(f"up {format_uptime(time.time() - psutil.boot_time())}  •  ", style=theme["main"])
    header.append(time.strftime("%H:%M:%S"), style=f"bold {theme['main']}")

    # cpu
    cpu_lines = [
        Text.assemble(("TOTAL ", f"bold {theme['accent']}"), bar(sampler.cpu_total, bar_width(6), theme)),
        Text(sparkline(sampler.cpu_history, inner, 100), style=theme["main"]),
    ]
    core_grid = Table.grid(expand=True, padding=(0, 1))
    core_grid.add_column(ratio=1)
    core_grid.add_column(ratio=1)
    cell_inner = max(inner // 2 - 1, 16)
    core_bar = max(cell_inner - 3 - PCT_WIDTH - 1, 4)
    cells = [
        Text.assemble((f"{i:>2} ", theme["dim"]), bar(c, core_bar, theme))
        for i, c in enumerate(sampler.cores)
    ]
    for i in range(0, len(cells), 2):
        core_grid.add_row(*cells[i : i + 2])
    cpu_panel = panel("CPU", Group(*cpu_lines, core_grid), theme)

    # memory
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    mem_lines = [
        Text.assemble(("RAM  ", f"bold {theme['accent']}"), bar(ram.percent, bar_width(5), theme)),
        Text(f"     {human_bytes(ram.used)} / {human_bytes(ram.total)}", style=theme["dim"]),
        Text.assemble(("SWAP ", f"bold {theme['accent']}"), bar(swap.percent, bar_width(5), theme)),
    ]
    battery = battery_info()
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        mem_lines.append(Text.assemble(("BATT ", f"bold {theme['accent']}"), bar(battery.percent, bar_width(5), theme)))
        mem_lines.append(Text(f"     {state}", style=theme["dim"]))
    mem_panel = panel("MEMORY" + (" & POWER" if battery is not None else ""), Group(*mem_lines), theme)

    # network
    net_lines = [
        Text.assemble(("DOWN ", f"bold {theme['accent']}"), (human_rate(sampler.net_down), theme["main"])),
        Text(sparkline(sampler.down_history, inner), style=theme["main"]),
        Text.assemble(("UP   ", f"bold {theme['accent']}"), (human_rate(sampler.net_up), theme["main"])),
        Text(sparkline(sampler.up_history, inner), style=theme["accent"]),
    ]
    net_panel = panel("NETWORK", Group(*net_lines), theme)

    # disks
    disk_lines = []
    for mount, usage in disk_rows():
        disk_lines.append(Text.assemble((f"{mount[:8]:<8} ", theme["main"]), bar(usage.percent, bar_width(9), theme)))
        disk_lines.append(Text(f"         {human_bytes(usage.used)} / {human_bytes(usage.total)}", style=theme["dim"]))
    disk_lines.append(
        Text(f"I/O  read {human_rate(sampler.disk_read)}  write {human_rate(sampler.disk_write)}", style=theme["accent"])
    )
    disk_panel = panel("DISKS", Group(*disk_lines), theme)

    # processes
    table = Table(expand=True, box=None, header_style=f"bold {theme['accent']}", padding=(0, 1))
    table.add_column("PID", justify="right", style=theme["dim"], width=7, no_wrap=True)
    table.add_column("NAME", style=theme["main"], overflow="ellipsis", no_wrap=True, ratio=1)
    table.add_column("CPU", justify="right", width=7, no_wrap=True)
    table.add_column("MEM", justify="right", width=7, no_wrap=True)
    for pid, name, cpu, mem in sampler.top_processes(procs):
        table.add_row(str(pid), name, f"{cpu:.1f}%", f"{mem:.1f}%")
    proc_panel = panel("TOP PROCESSES", table, theme)

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(Group(cpu_panel, disk_panel), Group(mem_panel, net_panel))
    return Group(Panel(header, border_style=theme["main"]), grid, proc_panel)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sysdeck", description="A sci-fi system dashboard for your terminal")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between updates (default 1)")
    parser.add_argument("--theme", choices=sorted(THEMES), default="cyber", help="color theme (default cyber)")
    parser.add_argument("--procs", type=int, default=8, help="how many top processes to show (default 8)")
    parser.add_argument("--once", action="store_true", help="print one frame and exit")
    parser.add_argument("--version", action="version", version=f"sysdeck {__version__}")
    args = parser.parse_args(argv)

    theme = THEMES[args.theme]
    console = Console()
    sampler = Sampler()

    if args.once:
        time.sleep(0.5)
        sampler.sample()
        # second pass so per-process CPU numbers are real
        sampler.top_processes(1)
        time.sleep(0.5)
        sampler.sample()
        console.print(render(sampler, theme, args.procs, console.width))
        return 0

    time.sleep(0.3)
    try:
        with Live(console=console, screen=True, refresh_per_second=4) as live:
            while True:
                sampler.sample()
                live.update(render(sampler, theme, args.procs, console.width))
                time.sleep(max(args.interval, 0.2))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
