# MultiPinger — improved

A lightweight desktop utility for continuously testing multiple network destinations. Built with the Python standard library and Tkinter; no third-party packages or admin privileges needed on typical desktop installs.

## Features

- **Concurrent checks** of up to 64 IP addresses or hostnames, including IPv6.
- **Reliable Start / Stop**: no busy-loop, no orphaned UI updates, no Tkinter calls from background threads.
- **Live statistics**: reachability, most recent / minimum / maximum / average latency, number of checks and packet-loss percentage.
- **Latency chart** for the selected destination (last 60 checks), marking failed checks in red.
- **Status-change history**: when a destination becomes unreachable or recovers, with failure counts on recovery.
- **Quick entry and bulk paste** for several destinations. Remove multiple selected rows using Ctrl-click / Shift-click.
- **Configurable interval** (1–60 seconds) and timeout (0.5–10 seconds). Change them between monitoring sessions.
- **Automatic save** of your destination list and settings. No usernames, passwords, or credentials are collected.
- **Export observations to CSV** for a troubleshooting report; keeps the most recent 20,000 checks in memory.
- **Sortable columns** by clicking the headings.
- **Windows, macOS and Linux** support, using the OS's native `ping` utility.

## Run

1. Install **Python 3.10+**. Windows: use Python from [python.org](https://www.python.org/downloads/). On Linux, you may also need your distro's `python3-tk` and `ping` packages.
2. Unzip the folder and open a terminal in the `MultiPinger-Improved` directory.
3. Run:

   ```bash
   python app.py
   ```

   Or `python3 app.py` on some macOS/Linux systems.

The app starts with **1.1.1.1** and **8.8.8.8** as example destinations; remove these and add the devices you're troubleshooting. Nothing is pinged until you click **Start Monitoring**.

## Using the app

Enter a hostname or address and click **+ Add**. You can paste comma-separated destinations into the entry box, or use **Paste list…** for many lines at once. Set the check interval and ping timeout, then click **Start Monitoring**. Select a destination to inspect its history graph. **Stop** pauses checking without clearing accumulated statistics. **Clear statistics** resets all counters and the current run's event history. **Export CSV…** saves individual results and timestamps from this session.

Packet-loss percentages and average/min/max latency are accumulated **since the last Clear statistics or app launch**. Changing destinations does not erase results for the other destinations. Only the **targets and check settings** are saved between launches, not the historical ping measurements.

Settings locations:

- Windows: `%APPDATA%\\MultiPinger\\settings.json`
- macOS: `~/Library/Application Support/MultiPinger/settings.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/multipinger/settings.json`

## Important troubleshooting notes

- ICMP Echo may be blocked by firewalls or devices, so **unreachable does not necessarily mean the destination is offline**.
- One worker runs per destination and submits results to the Tkinter UI through a thread-safe queue. Each check has a subprocess timeout; stopping or closing the app prevents late checks from being displayed but an in-flight ping process may take up to the configured timeout (plus a one-second guard) to finish.
- This checks **ICMP**, not TCP port availability. It does not have the elevated privileges required for raw ICMP packet crafting; it simply invokes your system's `ping` executable safely, without a shell.
- The Linux ping timeout flag uses whole seconds; sub-second timeout values are enforced by the subprocess timeout guard rather than by the `ping` CLI itself.
- On macOS, IPv6 uses the system `ping6` utility and a subprocess timeout (macOS `ping6 -W` has a different meaning from IPv4 `ping -W` on some releases). The tools must be available in your PATH.

## Run tests

```bash
python -m unittest discover -s tests -v
```

No external libraries are required for the tests.
