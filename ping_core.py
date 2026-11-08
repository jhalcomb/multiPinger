"""Platform-aware, testable ping routines and per-destination statistics.

This module intentionally does not import tkinter; workers can call ping_once,
while the GUI thread alone updates PingStats and visual widgets.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import ipaddress
import math
import platform
import re
import subprocess
import time
from typing import Deque, Optional


_LATENCY_RE = re.compile(r"\btime\s*[=<]\s*(\d+(?:[.,]\d+)?)\s*ms\b", re.I)
_HOST_LABEL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class PingResult:
    target: str
    timestamp: datetime
    success: bool
    latency_ms: Optional[float]
    detail: str


@dataclass
class PingStats:
    sent: int = 0
    received: int = 0
    total_latency_ms: float = 0.0
    measured: int = 0
    min_latency_ms: Optional[float] = None
    max_latency_ms: Optional[float] = None
    last_latency_ms: Optional[float] = None
    last_success: Optional[bool] = None
    last_checked: Optional[datetime] = None
    last_detail: str = "Not checked yet"
    consecutive_failures: int = 0
    history: Deque[Optional[float]] = field(default_factory=lambda: deque(maxlen=60))

    def record(self, result: PingResult) -> None:
        self.sent += 1
        self.last_success = result.success
        self.last_latency_ms = result.latency_ms if result.success else None
        self.last_checked = result.timestamp
        self.last_detail = result.detail
        self.history.append(result.latency_ms if result.success else None)
        if result.success:
            self.received += 1
            self.consecutive_failures = 0
            if result.latency_ms is not None:
                value = result.latency_ms
                self.measured += 1
                self.total_latency_ms += value
                self.min_latency_ms = value if self.min_latency_ms is None else min(self.min_latency_ms, value)
                self.max_latency_ms = value if self.max_latency_ms is None else max(self.max_latency_ms, value)
        else:
            self.consecutive_failures += 1

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.sent - self.received) / self.sent if self.sent else 0.0

    @property
    def avg_latency_ms(self) -> Optional[float]:
        return self.total_latency_ms / self.measured if self.measured else None


def validate_target(value: str) -> str:
    """Return a safe, normalized IP address or hostname, or raise ValueError.

    subprocess is *never* invoked through a shell. Nonetheless validating the
    target also prevents accidental ping-option injection or invalid hostnames.
    """
    target = value.strip()
    if not target or len(target) > 253:
        raise ValueError("Enter a hostname or an IPv4/IPv6 address (max 253 characters).")

    try:
        return str(ipaddress.ip_address(target)).lower()
    except ValueError:
        pass

    trimmed = target.rstrip(".")
    labels = trimmed.split(".")
    if (not trimmed or len(target) - len(trimmed) > 1
            or any(not label or len(label) > 63 or not _HOST_LABEL_RE.fullmatch(label)
                   or label.startswith("-") or label.endswith("-") for label in labels)):
        raise ValueError(f"Invalid IP address or hostname: {target}")
    return target.lower()


def build_ping_command(target: str, timeout_seconds: float, os_name: Optional[str] = None) -> list[str]:
    """Build an argument list (never a shell command).

    Linux ping -W accepts integer seconds. Windows and macOS IPv4 ping -W
    accept milliseconds. Older macOS ping6 uses -W for a *different purpose*,
    so IPv6 relies on the subprocess timeout instead.
    """
    system = os_name or platform.system()
    try:
        ipv6 = ipaddress.ip_address(target.split("%", 1)[0]).version == 6
    except ValueError:
        ipv6 = False

    if system == "Windows":
        args = ["ping"]
        if ipv6:
            args.append("-6")
        return args + ["-n", "1", "-w", str(max(1, round(timeout_seconds * 1000))), target]
    if system == "Darwin":
        if ipv6:
            # On some macOS releases ping6 -W means an IPv6 node-info query,
            # NOT a timeout. Let subprocess.run enforce the time limit.
            return ["ping6", "-n", "-c", "1", target]
        return ["ping", "-n", "-c", "1",
                "-W", str(max(1, round(timeout_seconds * 1000))), target]
    args = ["ping"]
    if ipv6:
        args.append("-6")
    return args + ["-n", "-c", "1", "-W", str(max(1, math.ceil(timeout_seconds))), target]


def _failure_detail(output: str) -> str:
    lower = output.lower()
    known = (
        ("destination host unreachable", "Destination host unreachable"),
        ("destination net unreachable", "Destination network unreachable"),
        ("time to live exceeded", "TTL exceeded"),
        ("ttl expired", "TTL exceeded"),
        ("network is unreachable", "Network is unreachable"),
        ("request timed out", "Request timed out"),
        ("could not find host", "Could not resolve hostname"),
        ("name or service not known", "Could not resolve hostname"),
        ("temporary failure in name resolution", "DNS resolution failed"),
        ("unknown host", "Unknown host"),
        ("100% packet loss", "No ICMP reply"),
        ("100.0% packet loss", "No ICMP reply"),
        ("general failure", "Ping failed locally"),
        ("transmit failed", "Ping transmit failed"),
        ("operation not permitted", "ICMP permission denied"),
    )
    for fragment, message in known:
        if fragment in lower:
            return message
    return "No ICMP reply"


def ping_once(target: str, timeout_seconds: float = 2.0, os_name: Optional[str] = None) -> PingResult:
    """Issue one ping and return a result. Safe to call from a worker thread."""
    timestamp = datetime.now().astimezone()
    command = build_ping_command(target, timeout_seconds, os_name)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=timeout_seconds + 1.0, check=False,
            # shell=False is intentional to avoid executing user input.
        )
    except subprocess.TimeoutExpired:
        return PingResult(target, timestamp, False, None, "Ping process timed out")
    except FileNotFoundError:
        return PingResult(target, timestamp, False, None, "System ping command not found")
    except OSError as error:
        return PingResult(target, timestamp, False, None, f"Ping error: {error.strerror or type(error).__name__}")

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    # Windows can occasionally report a successful process exit despite a
    # gateway answering "Destination host unreachable" (not an Echo Reply).
    failure_markers = (
        "destination host unreachable", "destination net unreachable",
        "network is unreachable", "request timed out", "could not find host",
        "name or service not known", "unknown host", "general failure",
        "transmit failed", "time to live exceeded", "ttl expired",
    )
    if completed.returncode != 0 or any(marker in output.lower() for marker in failure_markers):
        return PingResult(target, timestamp, False, None, _failure_detail(output))
    match = _LATENCY_RE.search(output)
    latency = float(match.group(1).replace(",", ".")) if match else None
    return PingResult(target, timestamp, True, latency, "Reply received")
