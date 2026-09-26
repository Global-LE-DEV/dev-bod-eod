"""Unit tests: telnet per-port alert gating (time window + down-only).

Regression guard for the mail flood: send_to_notify() used to POST an alert for
every port on every */2min run, including healthy ones (status 2), 24x7.
Alerts are now limited to ports that are actually DOWN and to the
ALERT_WINDOW_START-ALERT_WINDOW_END window (env-driven; fallback 09:00-23:30).

The helpers are exec'd in isolation so nothing imports FinspotUtils, touches
Redis/RabbitMQ, or posts to the notify service.
"""
import datetime
import os
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "overlayFiles")


def _load_gate(env=None, filename="telnet_checker.py"):
    """Exec just the window/gate helper block from the checker under a given env."""
    path = os.path.join(ROOT, filename)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    start = src.index("ALERT_WINDOW_START = ")
    end = src.index("def load_config")
    saved = {k: os.environ.get(k) for k in ("ALERT_WINDOW_START", "ALERT_WINDOW_END", "ALERT_WINDOW_ENABLED")}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update(env or {})
    try:
        ns = {"os": os, "datetime": datetime.datetime}
        exec(compile(src[start:end], path, "exec"), ns)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return ns


def _at(hour, minute):
    return datetime.datetime(2026, 9, 24, hour, minute)


class TestTelnetAlertWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = _load_gate()
        cls.should_alert = staticmethod(ns["should_alert"])
        cls.in_alert_window = staticmethod(ns["in_alert_window"])

    def test_up_port_never_alerts_even_in_window(self):
        for hh, mm in [(9, 0), (12, 0), (18, 0), (23, 29), (23, 30)]:
            self.assertFalse(self.should_alert(2, _at(hh, mm)), f"{hh:02d}:{mm:02d} up")

    def test_down_port_alerts_inside_window(self):
        for hh, mm in [(9, 0), (13, 37), (18, 0), (22, 0), (23, 30)]:
            self.assertTrue(self.should_alert(0, _at(hh, mm)), f"{hh:02d}:{mm:02d} down")

    def test_down_port_silent_outside_window(self):
        for hh, mm in [(0, 0), (3, 0), (8, 59), (23, 31), (23, 59)]:
            self.assertFalse(self.should_alert(0, _at(hh, mm)), f"{hh:02d}:{mm:02d} down")

    def test_amber_treated_as_down(self):
        self.assertTrue(self.should_alert(1, _at(12, 0)))
        self.assertFalse(self.should_alert(1, _at(3, 0)))

    def test_boundaries_are_inclusive(self):
        self.assertFalse(self.in_alert_window(_at(8, 59)))
        self.assertTrue(self.in_alert_window(_at(9, 0)))
        self.assertTrue(self.in_alert_window(_at(23, 30)))
        self.assertFalse(self.in_alert_window(_at(23, 31)))

    def test_non_numeric_status_is_treated_as_down(self):
        for bad in (None, "bad", ""):
            self.assertTrue(self.should_alert(bad, _at(12, 0)), bad)
            self.assertFalse(self.should_alert(bad, _at(3, 0)), bad)


class TestEnvOverrides(unittest.TestCase):
    def test_window_from_env(self):
        ns = _load_gate({"ALERT_WINDOW_START": "08:30", "ALERT_WINDOW_END": "18:00"})
        self.assertTrue(ns["in_alert_window"](_at(8, 30)))
        self.assertTrue(ns["in_alert_window"](_at(18, 0)))
        self.assertFalse(ns["in_alert_window"](_at(8, 29)))
        self.assertFalse(ns["in_alert_window"](_at(18, 1)))

    def test_bad_window_config_falls_back_to_defaults(self):
        ns = _load_gate({"ALERT_WINDOW_START": "not-a-time", "ALERT_WINDOW_END": "25:99"})
        self.assertTrue(ns["in_alert_window"](_at(12, 0)))
        self.assertFalse(ns["in_alert_window"](_at(3, 0)))
        self.assertTrue(ns["in_alert_window"](_at(23, 30)))
        self.assertFalse(ns["in_alert_window"](_at(23, 31)))

    def test_window_disabled_mails_down_ports_anytime_but_never_up(self):
        ns = _load_gate({"ALERT_WINDOW_ENABLED": "no"})
        self.assertTrue(ns["should_alert"](0, _at(3, 0)))
        self.assertFalse(ns["should_alert"](2, _at(3, 0)))
        self.assertFalse(ns["should_alert"](2, _at(12, 0)))


if __name__ == "__main__":
    unittest.main()
