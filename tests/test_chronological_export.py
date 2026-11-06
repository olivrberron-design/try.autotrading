"""Public export preserves results and rejects misleading or unsafe source data."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from html.parser import HTMLParser
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from publish_chronological import main, render_html, sanitized


def rehash(report):
    encoded = json.dumps(report["protocol"], indent=2, sort_keys=True, allow_nan=False).encode()
    report["protocol_sha256"] = hashlib.sha256(encoded).hexdigest()
    return report


def fixture():
    metrics = {
        "return_pct": 4.0, "cagr_pct": 2.0, "max_daily_close_drawdown_pct": 3.0,
        "sharpe": None, "fills": 10, "buys": 5, "sells": 5,
        "fees": 2.0, "turnover": 2000.0, "mean_exposure_pct": 10.0,
        "peak_close_exposure_pct": 21.0, "ending_equity": 5200.0,
        "audit_passed": True, "cash": 5000.0, "private_debug": "omit me",
    }
    info = {"name": "USDT_TEST", "symbols": ["BTCUSDT", "ETHUSDT"],
            "download_start": "2018-01-01", "end_exclusive": "2021-01-01", "warmup_bars": 240}
    protocol = {
        "version": 1, "registered_before_results": "2026-11-06",
        "strategies": {"alpha": {"description": "An example <b>strategy</b>", "parameters": {"window": 21}}},
        "execution": {"capital": 5000, "cap": .2, "fee_bps": 10, "impact_bps": 6,
                      "delay_days": 1, "rebalance_band": .01, "min_notional": 5},
        "cost_scenarios": {"base": [10, 6, 1], "double": [20, 12, 1],
                           "quadruple": [40, 24, 1], "one_extra_day": [10, 6, 2]},
        "selection": {"train_days": 365, "test_days": 90, "min_training_fills": 12,
                      "positive_training_return": True, "positive_training_excess_vs_rebalanced_equal_weight": True,
                      "ranking": "training Sharpe", "fallback": "cash"},
        "bootstrap": {"resamples": 1000, "block_days": [30], "seed": 42, "method": "block bootstrap"},
        "candidate_gate": {"max_drawdown_pct_at_most": 15}, "fixed_nonoverlap_stress_days": 180,
        "excluded_prior_reserved_intervals_utc": [["2022-07-08T02:00:00Z", "2022-09-29T02:59:59Z"]],
        "episodes": [deepcopy(info)],
    }
    result = {key: deepcopy(metrics) for key in protocol["cost_scenarios"]}
    result.update({"years": {"2019": {"days": 365, "return_pct": -1.5}},
                   "windows": [{"start_utc": "2019-01-01T00:00:00Z", "days": 180,
                                "return_pct": -1.2, "excess_vs_rebalanced_pct": -.5, "fills": 4}]})
    wf = {"result": {**metrics, "return_pct": -2.5}, "folds": [{
        "training_start": 241, "training_stop_exclusive": 606,
        "test_start": 606, "test_stop_exclusive": 650,
        "test_start_utc": "2020-01-01T00:00:00Z", "chosen": "cash",
        "test_return_pct": 0, "training_scores": {"alpha": metrics},
    }]}
    ep = {
        "episode": info, "start_utc": "2018-08-30T00:00:00Z", "end_utc": "2020-12-31T23:59:59Z",
        "study_days": 850, "results": {"alpha": result},
        "data_manifest": [{"symbol": symbol, "source": "https://data-api.binance.vision",
                           "start_ms": 1514764800000, "end_exclusive_ms": 1609459200000,
                           "rows": 1096, "sha256": "a" * 64} for symbol in info["symbols"]],
        "bootstrap": {"30": {"alpha": {"annualized_log_mean_ci95_pct": [-2, 5],
                       "familywise_p_positive_mean": .2, "block_days": 30,
                       "resamples": 1000, "family_size": 1}}},
        "buy_hold_initial_20": deepcopy(metrics), "walk_forward": wf, "walk_forward_double": deepcopy(wf),
    }
    return rehash({
        "protocol": protocol, "provenance": {"git_head": "a" * 40,
            "source_sha256": {"research.py": "b" * 64}, "git_status": ["?? /Users/private/person.txt"]},
        "episodes": [ep], "candidate_gates": {"alpha": {
            "passed": False, "checks": {"CI": False, "walk_forward": False, "drawdown": True},
            "metrics": {"worst_drawdown": 3}}},
        "qualified_candidates": [], "conclusion": "Not copied",
    })


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)


class TestChronologicalExport(unittest.TestCase):
    def test_no_winner_keeps_losses_and_batch_selection_separate(self):
        feed = sanitized(fixture(), "2026-11-06T12:00:00Z")
        self.assertEqual(feed["research_status"], "NO_VALIDATED_WINNER")
        self.assertEqual(feed["qualified_candidates"], [])
        ep = feed["episodes"][0]
        self.assertEqual(ep["strategies"]["alpha"]["years"]["2019"]["return_pct"], -1.5)
        self.assertEqual(ep["batch_walk_forward"]["base"]["result"]["return_pct"], -2.5)
        self.assertNotIn("walk_forward", ep["strategies"]["alpha"])
        self.assertEqual(ep["strategies"]["alpha"]["batch_gate_failures"], ["CI", "walk_forward"])
        fold = ep["batch_walk_forward"]["base"]["folds"][0]
        self.assertEqual(fold["days"], 44)
        self.assertEqual(fold["test_end_exclusive_utc"], "2020-02-14T00:00:00+00:00")

    def test_no_local_paths_dirty_status_training_details_or_extra_metrics(self):
        exported = json.dumps(sanitized(fixture()))
        for forbidden in ("/Users/", "git_status", "private_debug", "training_scores", "omit me", "Not copied"):
            self.assertNotIn(forbidden, exported)
        self.assertIn("b" * 64, exported)
        self.assertIn("a" * 64, exported)

    def test_rejects_nonfinite_even_in_unexported_fields(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            report = fixture()
            report["episodes"][0]["results"]["alpha"]["base"]["private_debug"] = value
            with self.assertRaisesRegex(ValueError, "Nonfinite"):
                sanitized(report)

    def test_rejects_credentials_local_paths_and_unsafe_source_paths(self):
        for key, value in (("api_key", "placeholder"), ("password", "placeholder"),
                           ("note", "/tmp/private-cache"), ("note", "Bearer somecredential")):
            report = fixture()
            report[key] = value
            with self.assertRaises(ValueError):
                sanitized(report)
        report = fixture()
        report["provenance"]["source_sha256"] = {"../private.py": "a" * 64}
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            sanitized(report)

    def test_rejects_hash_mismatch_unaudited_data_and_false_winner(self):
        report = fixture()
        report["protocol"]["execution"]["fee_bps"] = 0
        with self.assertRaisesRegex(ValueError, "hash"):
            sanitized(report)
        report = fixture()
        report["episodes"][0]["results"]["alpha"]["base"]["audit_passed"] = False
        with self.assertRaisesRegex(ValueError, "unaudited"):
            sanitized(report)
        report = fixture()
        report["qualified_candidates"] = ["alpha"]
        with self.assertRaisesRegex(ValueError, "disagree"):
            sanitized(report)
        report = fixture()
        report["candidate_gates"]["alpha"]["passed"] = True
        with self.assertRaisesRegex(ValueError, "disagrees"):
            sanitized(report)

    def test_new_strategy_definitions_are_dynamic_and_never_claim_validated_profit(self):
        report = fixture()
        report["protocol"]["strategies"]["future_strategy"] = deepcopy(report["protocol"]["strategies"]["alpha"])
        report["candidate_gates"]["future_strategy"] = {
            "metrics": {}, "checks": {"CI": True, "walk_forward": True}, "passed": True}
        report["qualified_candidates"] = ["future_strategy"]
        ep = report["episodes"][0]
        ep["results"]["future_strategy"] = deepcopy(ep["results"]["alpha"])
        ep["bootstrap"]["30"]["future_strategy"] = deepcopy(ep["bootstrap"]["30"]["alpha"])
        feed = sanitized(rehash(report))
        self.assertEqual(feed["research_status"], "PROVISIONAL_HISTORICAL_CANDIDATE")
        self.assertIn("future_strategy", render_html(feed))
        self.assertIn("Előzetes historikus jelölt", render_html(feed))

    def test_html_escapes_untrusted_text_and_has_no_active_or_external_resources(self):
        report = fixture()
        report["protocol"]["strategies"]["alpha"]["description"] = '<script>alert(1)</script><img src="https://example.org/track">'
        html = render_html(sanitized(rehash(report)))
        self.assertIn("Nincs validált nyertes", html)
        self.assertIn("&lt;script&gt;", html)
        parsed = Tags()
        parsed.feed(html)
        self.assertNotIn("script", parsed.tags)
        self.assertNotIn("img", parsed.tags)
        self.assertFalse(any(key in ("src", "href", "onload") for key, _ in parsed.attributes))
        self.assertIn("Content-Security-Policy", html)
        report["protocol"]["selection"]["train_days"] = '<img src="x">'
        with self.assertRaises(ValueError):
            sanitized(rehash(report))

    def test_absent_walk_forward_stays_unavailable_not_zero(self):
        report = fixture()
        report["episodes"][0]["walk_forward"] = {"folds": [], "result": None}
        feed = sanitized(report)
        self.assertIsNone(feed["episodes"][0]["batch_walk_forward"]["base"]["result"])
        self.assertIn("nincs elegendő adat tesztidőszakhoz", render_html(feed))

    def test_batch_note_and_missing_episode_cannot_be_silently_lost(self):
        report = fixture()
        report["protocol"].update({"batch": 2, "batch_note": "Prior results informed this new batch."})
        feed = sanitized(rehash(report))
        self.assertEqual(feed["methodology"]["batch"], 2)
        self.assertIn("nem független megerősítő teszt", render_html(feed))
        report["protocol"]["episodes"].append({
            **report["protocol"]["episodes"][0], "name": "missing_episode"})
        with self.assertRaisesRegex(ValueError, "Missing registered episode"):
            sanitized(rehash(report))

    def test_cli_writes_compact_json_and_offline_html_without_overwriting_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "report.json"
            html = root / "out" / "research.html"
            public = root / "out" / "feed.json"
            source.write_text(json.dumps(fixture()), encoding="utf-8")
            before = source.read_bytes()
            with redirect_stdout(StringIO()):
                main(["--input", str(source), "--html", str(html), "--public-json", str(public)])
            self.assertEqual(json.loads(public.read_text())["research_status"], "NO_VALIDATED_WINNER")
            self.assertEqual(public.read_text().count("\n"), 1)
            self.assertIn('<html lang="hu">', html.read_text())
            self.assertEqual(source.read_bytes(), before)
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                main(["--input", str(source), "--html", str(source)])


if __name__ == "__main__":
    unittest.main()
