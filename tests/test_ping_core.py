from datetime import datetime, timezone
import subprocess
import unittest
from unittest.mock import patch

from ping_core import PingResult, PingStats, build_ping_command, ping_once, validate_target


class TargetTests(unittest.TestCase):
    def test_ip_addresses_and_hostnames(self):
        self.assertEqual(validate_target(" 8.8.8.8 "), "8.8.8.8")
        self.assertEqual(validate_target("EXAMPLE.COM"), "example.com")
        self.assertEqual(validate_target("localhost"), "localhost")
        self.assertEqual(validate_target("2001:DB8::1"), "2001:db8::1")
        self.assertEqual(validate_target("switch_01.mgmt"), "switch_01.mgmt")

    def test_reject_injection_and_bad_names(self):
        for target in ("", "-t", "--help", "8.8.8.8 & calc", "host; rm -rf /",
                       "https://example.com", "host..test", "host\nexample.com", "bad-"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_target(target)

    def test_platform_ping_commands(self):
        self.assertEqual(build_ping_command("8.8.8.8", 1.5, "Windows"),
                         ["ping", "-n", "1", "-w", "1500", "8.8.8.8"])
        self.assertEqual(build_ping_command("2001:db8::1", 2, "Windows"),
                         ["ping", "-6", "-n", "1", "-w", "2000", "2001:db8::1"])
        self.assertEqual(build_ping_command("host.local", 1.5, "Linux"),
                         ["ping", "-n", "-c", "1", "-W", "2", "host.local"])
        self.assertEqual(build_ping_command("::1", 1, "Darwin"),
                         ["ping6", "-n", "-c", "1", "::1"])


class PingTests(unittest.TestCase):
    @patch("ping_core.subprocess.run")
    def test_success_parses_latency(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "Reply from 8.8.8.8: time=12ms", "")
        result = ping_once("8.8.8.8", 1, "Windows")
        self.assertTrue(result.success)
        self.assertEqual(result.latency_ms, 12)
        self.assertIsInstance(run.call_args.args[0], list)

    @patch("ping_core.subprocess.run")
    def test_sub_ms_reply(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "Reply from ::1: time<1ms", "")
        self.assertEqual(ping_once("::1", 1, "Windows").latency_ms, 1)

    @patch("ping_core.subprocess.run")
    def test_failure_reason(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "Destination host unreachable", "")
        result = ping_once("10.5.3.1", 1, "Windows")
        self.assertFalse(result.success)
        self.assertIn("unreachable", result.detail)

    @patch("ping_core.subprocess.run")
    def test_windows_exit_zero_but_gateway_reports_unreachable(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, "Reply from 10.0.0.1: Destination host unreachable.", "")
        result = ping_once("10.0.0.20", 1, "Windows")
        self.assertFalse(result.success)
        self.assertEqual(result.detail, "Destination host unreachable")

    @patch("ping_core.subprocess.run", side_effect=subprocess.TimeoutExpired("ping", 1))
    def test_timeout(self, _):
        self.assertIn("timed out", ping_once("8.8.8.8").detail)

    @patch("ping_core.subprocess.run", side_effect=FileNotFoundError())
    def test_ping_missing(self, _):
        self.assertIn("not found", ping_once("8.8.8.8").detail)


class StatisticsTests(unittest.TestCase):
    def test_loss_average_and_recovery(self):
        stat = PingStats()
        now = datetime.now(timezone.utc)
        for ok, latency in ((True, 10.0), (False, None), (True, 30.0)):
            stat.record(PingResult("host", now, ok, latency, "test"))
        self.assertEqual(stat.sent, 3)
        self.assertEqual(stat.received, 2)
        self.assertAlmostEqual(stat.loss_pct, 100 / 3)
        self.assertEqual(stat.avg_latency_ms, 20)
        self.assertEqual(stat.min_latency_ms, 10)
        self.assertEqual(stat.max_latency_ms, 30)
        self.assertEqual(stat.consecutive_failures, 0)
        self.assertEqual(list(stat.history), [10, None, 30])


if __name__ == "__main__":
    unittest.main()
