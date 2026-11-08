"""Optional GUI smoke test. Run with a display or: xvfb-run -a python tests/gui_smoke.py"""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as module
from ping_core import PingResult


def main() -> None:
    with TemporaryDirectory() as tmp:
        module.config_path = lambda: Path(tmp) / "settings.json"
        checks = {}

        def fake_ping(host: str, timeout: float) -> PingResult:
            checks[host] = checks.get(host, 0) + 1
            good = host == "1.1.1.1" or checks[host] % 2 == 0
            return PingResult(host, datetime.now().astimezone(), good,
                              15.0 if good else None, "Reply received" if good else "No ICMP reply")

        module.ping_once = fake_ping
        gui = module.MultiPinger()
        gui.interval_var.set("1")
        gui.timeout_var.set("1")
        gui._add_targets(["router.local", "2001:db8::1"])

        def check():
            try:
                assert gui.running
                assert gui.stats["1.1.1.1"].sent >= 1
                assert gui.stats["8.8.8.8"].sent >= 1
                assert gui.stats["router.local"].sent >= 1
                assert gui.stats["2001:db8::1"].sent >= 1
                gui._sort_by("loss")
                gui._sort_by("avg")
                gui._stop()
                assert not gui.running
                assert len(gui.samples) >= 4
                gui._save_config()
                assert module.load_config()["targets"] == list(gui.stats)
                gui._clear_stats()
                assert all(s.sent == 0 for s in gui.stats.values())
                print("GUI smoke test: PASS")
            finally:
                gui._close()

        gui.after(120, gui._start)
        gui.after(650, check)
        gui.mainloop()


if __name__ == "__main__":
    main()
