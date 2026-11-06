#!/usr/bin/env python3
"""Export allowlisted chronological research JSON and self-contained Hungarian HTML.

Only local output files are written. No network, credentials, or trading actions.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re

SCENARIOS = ("base", "double", "quadruple", "one_extra_day")
SCENARIO_LABELS = ("Alapköltség", "Kétszeres költség", "Négyszeres költség", "+1 nap késés")
METRICS = ("return_pct", "cagr_pct", "max_daily_close_drawdown_pct", "sharpe",
           "fills", "buys", "sells", "fees", "turnover", "mean_exposure_pct",
           "peak_close_exposure_pct", "ending_equity", "audit_passed")
GATE_LABELS = {
    "not_baseline": "Összehasonlító alapszabály, nem jelölt",
    "positive_double_all": "Pozitív hozam minden epizódban kétszeres költséggel",
    "window_fraction": "Elég pozitív teljes 180 napos ablak",
    "window_count": "Elegendő teljes 180 napos ablak",
    "median_excess": "Pozitív medián többlet az újrasúlyozott alaphoz képest",
    "drawdown": "Visszaesési korlát", "fills": "Elegendő teljesülés",
    "CI": "Pozitív alsó konfidenciahatár minden epizódban és blokkhosszon",
    "FWER": "Többszörös próbákra korrigált p-érték",
    "walk_forward": "A teljes készlet időrendi kiválasztásának pozitív eredménye",
}
LIMITATIONS_HU = [
    "Feltáró, visszatekintő kutatás; nem érintetlen holdout és nem jövőbeli forward teszt.",
    "Az USDT és USDC epizód külön induló tőkét használ; hozamuk nem adható össze és nem láncolható egy számlává.",
    "A drawdown napi záróértékekből számolt, ezért a napon belüli visszaesést alulbecsülheti.",
    "A célallokáció nem veszteséglimit; az árfolyammozgás a tényleges kitettséget a cél fölé viheti.",
    "A költségek és mennyiségi limitek szimulációs feltételezések, nem rekonstruált historikus ajánlati könyv vagy tőzsdei limitek.",
    "A blokk-bootstrap állandó eloszlást közelít; a családonkénti korrekció csak erre a stratégiakészletre vonatkozik, nem a teljes korábbi kutatásra.",
    "A walk-forward eredmény a teljes készlet kiválasztási szabályáé, nem az egyes stratégiák önálló teljesítménye.",
    "Egy átmenő eredmény is csak előzetes historikus jelölt: a konzisztens nyereséghez új, előre rögzített forward bizonyíték szükséges.",
]
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,99}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE_KEY = re.compile(r"(^|_)(password|secret|token|credentials?|api_key|private_key|authorization|cookie)(_|$)", re.I)
UNSAFE_TEXT = re.compile(r"/Users/|/home/|/private/|file://|[A-Za-z]:\\|github_pat_|gh[pousr]_[A-Za-z0-9]|BEGIN [A-Z ]*PRIVATE KEY|Bearer\s+[A-Za-z0-9]")


def _scan(value, path=()):
    # Raw dirty-worktree output is local-only and never copied to a public field.
    if path == ("provenance", "git_status"):
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("Nonfinite research value")
    elif isinstance(value, str):
        if value.startswith(("/", "~/")) or UNSAFE_TEXT.search(value):
            raise ValueError("Unsafe private value in research report")
    elif isinstance(value, list):
        for item in value:
            _scan(item, path + ("[]",))
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or SENSITIVE_KEY.search(key):
                raise ValueError("Unsafe research field")
            _scan(key, path + ("key",))
            _scan(item, path + (key,))
    else:
        raise ValueError("Unsupported research value")


def _pick(value, keys):
    if not isinstance(value, dict):
        raise ValueError("Expected a research object")
    try:
        return {key: value[key] for key in keys}
    except KeyError as exc:
        raise ValueError("Missing required research field") from exc


def _number(value, minimum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite research number")
    if (minimum is not None and value < minimum) or (integer and not isinstance(value, int)):
        raise ValueError("Research number outside permitted range")
    return value


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Expected an ISO date")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _metrics(value):
    result = _pick(value, METRICS)
    for key, item in result.items():
        if key == "audit_passed":
            if item is not True:
                raise ValueError("Refusing unaudited research results")
        elif key == "sharpe" and item is None:
            continue
        else:
            _number(item, 0 if key in ("fills", "buys", "sells", "fees", "turnover") else None,
                    key in ("fills", "buys", "sells"))
    if result["buys"] + result["sells"] != result["fills"]:
        raise ValueError("Inconsistent fill counts")
    return result


def _walk_forward(value, names):
    folds = []
    for fold in value["folds"]:
        if fold["chosen"] != "cash" and fold["chosen"] not in names:
            raise ValueError("Unknown walk-forward strategy")
        indices = [fold[k] for k in ("training_start", "training_stop_exclusive", "test_start", "test_stop_exclusive")]
        for index in indices:
            _number(index, 0, True)
        a, b, c, d = indices
        if not a < b == c < d:
            raise ValueError("Invalid walk-forward chronology")
        start = _date(fold["test_start_utc"])
        folds.append({
            "training_start_utc": (start - timedelta(days=b-a)).isoformat(),
            "training_end_exclusive_utc": start.isoformat(),
            "test_start_utc": start.isoformat(),
            "test_end_exclusive_utc": (start + timedelta(days=d-c)).isoformat(),
            "days": d-c, "chosen": fold["chosen"],
            "return_pct": _number(fold["test_return_pct"]),
        })
    result = value.get("result")
    if bool(folds) != (result is not None):
        raise ValueError("Inconsistent walk-forward availability")
    return {"result": _metrics(result) if result is not None else None, "folds": folds}


def sanitized(source, generated_at_utc=None):
    """Build schema v1 from explicit fields, without raw private provenance."""
    _scan(source)
    protocol = source["protocol"]
    encoded = json.dumps(protocol, indent=2, sort_keys=True, allow_nan=False).encode()
    if source.get("protocol_sha256") != hashlib.sha256(encoded).hexdigest():
        raise ValueError("Protocol hash mismatch")
    names = list(protocol["strategies"])
    if not names or not source.get("episodes"):
        raise ValueError("Empty historical research")
    definitions = {}
    for name in names:
        if not ID.fullmatch(name):
            raise ValueError("Invalid strategy identifier")
        definition = _pick(protocol["strategies"][name], ("description", "parameters"))
        if not isinstance(definition["description"], str) or not isinstance(definition["parameters"], dict):
            raise ValueError("Invalid strategy definition")
        definitions[name] = definition
    gates = {}
    for name in names:
        gate = _pick(source["candidate_gates"][name], ("metrics", "checks", "passed"))
        if not gate["checks"] or any(type(v) is not bool for v in gate["checks"].values()):
            raise ValueError("Invalid candidate checks")
        if type(gate["passed"]) is not bool or gate["passed"] != all(gate["checks"].values()):
            raise ValueError("Candidate result disagrees with checks")
        gates[name] = {**gate, "failures": [key for key, passed in gate["checks"].items() if not passed]}
    qualified = [name for name in names if gates[name]["passed"]]
    if sorted(source.get("qualified_candidates", [])) != sorted(qualified):
        raise ValueError("Qualified candidates disagree with gates")
    execution = _pick(protocol["execution"], ("capital", "cap", "fee_bps", "impact_bps", "delay_days", "rebalance_band", "min_notional"))
    for key, value in execution.items():
        _number(value, 0, key == "delay_days")
    if execution["capital"] <= 0 or not 0 < execution["cap"] <= 1 or execution["delay_days"] < 1:
        raise ValueError("Invalid execution assumptions")
    scenarios = _pick(protocol["cost_scenarios"], SCENARIOS)
    for values in scenarios.values():
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError("Invalid cost scenario")
        for value in values:
            _number(value, 0)
        _number(values[2], 1, True)
    selection = _pick(protocol["selection"], (
        "train_days", "test_days", "min_training_fills", "positive_training_return",
        "positive_training_excess_vs_rebalanced_equal_weight", "ranking", "fallback"))
    for key in ("train_days", "test_days", "min_training_fills"):
        _number(selection[key], 1, True)
    bootstrap = _pick(protocol["bootstrap"], ("resamples", "block_days", "seed", "method"))
    _number(bootstrap["resamples"], 1, True)
    for size in bootstrap["block_days"]:
        _number(size, 1, True)
    episodes, coverage, seen = [], [], set()
    registered_episodes = {item["name"]: item for item in protocol["episodes"]}
    if len(registered_episodes) != len(protocol["episodes"]):
        raise ValueError("Duplicate registered episode")
    for ep in source["episodes"]:
        info = _pick(ep["episode"], ("name", "symbols", "download_start", "end_exclusive", "warmup_bars"))
        if not ID.fullmatch(info["name"]) or info["name"] in seen:
            raise ValueError("Invalid or duplicate episode")
        seen.add(info["name"])
        if info != _pick(registered_episodes.get(info["name"]), info):
            raise ValueError("Episode differs from registered protocol")
        if not info["symbols"] or set(info["symbols"]) - {"BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"}:
            raise ValueError("Unexpected historical market")
        quotes = {symbol[-4:] for symbol in info["symbols"]}
        if len(quotes) != 1 or _date(ep["start_utc"]) >= _date(ep["end_utc"]):
            raise ValueError("Invalid episode currency or chronology")
        manifests = []
        for item in ep["data_manifest"]:
            clean = _pick(item, ("symbol", "source", "start_ms", "end_exclusive_ms", "rows"))
            if clean["symbol"] not in info["symbols"] or clean["source"] not in (
                "https://data-api.binance.vision", "https://data-api.binance.vision/api/v3/klines"
            ):
                raise ValueError("Unexpected market-data provenance")
            for key in ("start_ms", "end_exclusive_ms", "rows"):
                _number(clean[key], 0, True)
            for key in ("sha256", "permitted_subset_sha256"):
                if key in item:
                    if not isinstance(item[key], str) or not HASH.fullmatch(item[key]):
                        raise ValueError("Invalid market-data hash")
                    clean[key] = item[key]
            if not any(key in clean for key in ("sha256", "permitted_subset_sha256")):
                raise ValueError("Missing market-data hash")
            manifests.append(clean)
        if sorted(item["symbol"] for item in manifests) != sorted(info["symbols"]):
            raise ValueError("Data manifests do not match the episode markets")
        coverage.append({**info, "quote_currency": next(iter(quotes)),
                         "start_utc": ep["start_utc"], "end_utc": ep["end_utc"],
                         "study_days": _number(ep["study_days"], 1, True), "data_manifest": manifests})
        if set(ep["results"]) != set(names):
            raise ValueError("Results differ from registered strategies")
        results = {}
        for name in names:
            block = ep["results"][name]
            result = {label: _metrics(block[label]) for label in SCENARIOS}
            result["years"] = {}
            for year, value in block["years"].items():
                if not re.fullmatch(r"\d{4}", year):
                    raise ValueError("Invalid calendar year")
                result["years"][year] = {"days": _number(value["days"], 1, True), "return_pct": _number(value["return_pct"])}
            result["windows"] = []
            for window in block["windows"]:
                clean = _pick(window, ("start_utc", "days", "return_pct", "excess_vs_rebalanced_pct", "fills"))
                _date(clean["start_utc"])
                _number(clean["days"], 1, True)
                _number(clean["fills"], 0, True)
                _number(clean["return_pct"])
                _number(clean["excess_vs_rebalanced_pct"])
                result["windows"].append(clean)
            result["confidence_intervals"] = {}
            for size in protocol["bootstrap"]["block_days"]:
                interval = _pick(ep["bootstrap"][str(size)][name], (
                    "annualized_log_mean_ci95_pct", "familywise_p_positive_mean", "block_days", "resamples", "family_size"))
                ci = interval["annualized_log_mean_ci95_pct"]
                if not isinstance(ci, list) or len(ci) != 2 or _number(ci[0]) > _number(ci[1]):
                    raise ValueError("Invalid confidence interval")
                if not 0 <= _number(interval["familywise_p_positive_mean"]) <= 1:
                    raise ValueError("Invalid familywise p-value")
                for key in ("block_days", "resamples", "family_size"):
                    _number(interval[key], 1, True)
                result["confidence_intervals"][str(size)] = interval
            result["batch_gate_failures"] = gates[name]["failures"]
            results[name] = result
        episodes.append({"name": info["name"], "strategies": results,
                         "buy_hold_initial_20": _metrics(ep["buy_hold_initial_20"]),
                         "batch_walk_forward": {"base": _walk_forward(ep["walk_forward"], names),
                                                "double": _walk_forward(ep["walk_forward_double"], names)}})
    if seen != set(registered_episodes):
        raise ValueError("Missing registered episode")
    source_hashes = {}
    for name, digest in source["provenance"]["source_sha256"].items():
        if not re.fullmatch(r"[A-Za-z0-9_/.-]+", name) or name.startswith("/") or ".." in name.split("/"):
            raise ValueError("Unsafe source provenance path")
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError("Invalid source hash")
        source_hashes[name] = digest
    git_head = source["provenance"].get("git_head")
    if git_head is not None and not re.fullmatch(r"[0-9a-f]{40}", git_head):
        raise ValueError("Invalid source revision")
    generated = generated_at_utc or datetime.now(timezone.utc).isoformat()
    _date(generated)
    return {
        "schema_version": 1, "type": "PUBLIC_HISTORICAL_RESEARCH_ONLY",
        "generated_at_utc": generated,
        "research_status": "PROVISIONAL_HISTORICAL_CANDIDATE" if qualified else "NO_VALIDATED_WINNER",
        "qualified_candidates": qualified, "protocol_sha256": source["protocol_sha256"],
        "source_provenance": {"git_head": git_head, "source_sha256": source_hashes},
        "methodology": {
            "research_scope": "RETROSPECTIVE_DISCOVERY", "protocol_version": protocol["version"],
            "batch": _number(protocol.get("batch", 1), 1, True),
            "batch_note": protocol.get("batch_note", ""),
            "registered_before_results": protocol["registered_before_results"],
            "execution": execution, "cost_scenarios": scenarios,
            "selection": selection, "bootstrap": bootstrap,
            "candidate_gate": protocol["candidate_gate"],
            "fixed_nonoverlap_stress_days": protocol["fixed_nonoverlap_stress_days"],
            "excluded_prior_reserved_intervals_utc": protocol["excluded_prior_reserved_intervals_utc"],
            "data_exclusion_note": protocol.get("data_exclusion_note", ""),
        },
        "strategy_definitions": definitions, "coverage": coverage, "episodes": episodes,
        "candidate_gates": gates, "limitations_hu": LIMITATIONS_HU,
    }


def _text(value):
    return escape(str(value), quote=True)


def _pct(value):
    return "–" if value is None else f"{value:+.2f}%".replace(".", ",")


def _table(headers, rows):
    return ('<div class="scroll"><table><thead><tr>' +
            ''.join(f'<th>{_text(x)}</th>' for x in headers) + '</tr></thead><tbody>' +
            ''.join('<tr>' + ''.join(f'<td>{_text(x)}</td>' for x in row) + '</tr>' for row in rows) +
            '</tbody></table></div>')


def render_html(feed):
    """Render sanitized values as escaped text, without scripts or external assets."""
    _scan(feed)
    if feed.get("schema_version") != 1 or feed.get("type") != "PUBLIC_HISTORICAL_RESEARCH_ONLY":
        raise ValueError("Expected sanitized historical feed")
    status = feed["research_status"]
    if status not in ("NO_VALIDATED_WINNER", "PROVISIONAL_HISTORICAL_CANDIDATE"):
        raise ValueError("Unknown research status")
    title = "Nincs validált nyertes" if status == "NO_VALIDATED_WINNER" else "Előzetes historikus jelölt"
    m = feed["methodology"]
    c, selection = m["execution"], m["selection"]
    body = [f'<header><p class="eyebrow">AUTOTRADING · HISTORIKUS KUTATÁS</p><h1>{title}</h1>',
            '<p>A pozitív múltbeli hozam önmagában nem bizonyít konzisztens nyereséget. '
            'A jelentés a rögzített kutatási feltételek teljesülését mutatja.</p>',
            f'<p class="muted">Export: {_text(feed["generated_at_utc"])} · protokoll {_text(m["protocol_version"])}</p></header>',
            '<section><h2>Módszer és költségek</h2>',
            f'<p>Epizódonként {_text(c["capital"])} egység közös BTC–ETH szimulált tőke, '
            f'{c["cap"]*100:g}% maximális célallokáció. Lezárt napi gyertyákból képzett jel után '
            f'a következő napi gyertya nyitóján modellezett teljesülés (napi gyertyaeltolás: {c["delay_days"]}). '
            'A záró tőkeérték a becsült eladási költséget is levonja a nyitott állományból.</p>',
            _table(["Forgatókönyv", "Díj / oldal (bps)", "Csúszás + fél spread / oldal (bps)", "Napi gyertyaeltolás"],
                   [[label, *m["cost_scenarios"][key]] for key, label in zip(SCENARIOS, SCENARIO_LABELS)]),
            f'<p>Időrendi kiválasztás: {selection["train_days"]} nap tanulóidőszak, utána legfeljebb '
            f'{selection["test_days"]} nap teszt; csak korábbi adatokból választott szabály, egyébként készpénz. '
            'Az epizódon belüli tesztablakok számlája folytonos. A részleges év és rövidebb utolsó ablak külön jelölt.</p>',
            f'<p>Bootstrap: {_text(m["bootstrap"]["resamples"])} újramintavétel, '
            f'{_text(", ".join(map(str, m["bootstrap"]["block_days"])))} napos blokkok. '
            'A 95%-os intervallum az évesített logaritmikus átlaghozamé, nem garantált hozamsáv.</p></section>']
    if m["batch"] > 1:
        body.append(
            f'<section><h2>Kutatási kör: {m["batch"]}</h2>'
            '<p>Ezt a kört a korábbi eredmények ismeretében állítottuk össze. '
            'A dátumok továbbra is feltáró adatok, a kör nem független megerősítő teszt.</p>'
            f'<p class="muted">{_text(m["batch_note"])}</p></section>')
    for ep, cover in zip(feed["episodes"], feed["coverage"]):
        body.extend([f'<section><h2>{_text(ep["name"])}</h2>',
                     f'<p>{_text(", ".join(cover["symbols"]))} · {_text(cover["start_utc"][:10])} → '
                     f'{_text(cover["end_utc"][:10])} · {cover["study_days"]} vizsgált nap. '
                     f'Adatkezdés: {_text(cover["download_start"])}; bemelegítés: {_text(cover["warmup_bars"])} nap.</p>',
                     '<p class="muted">Önálló epizód; az eltérő jegyzési pénznemű időszakokból nem készült összefűzött hozamgörbe.</p>'])
        rows = []
        for name, result in ep["strategies"].items():
            base = result["base"]
            rows.append([name, *[_pct(result[key]["return_pct"]) for key in SCENARIOS],
                         f'{base["max_daily_close_drawdown_pct"]:.2f}%', base["fills"],
                         "Előzetes jelölt" if feed["candidate_gates"][name]["passed"] else "Nem teljesült"])
        body.append(_table(["Stratégia", *[x + " nettó" for x in SCENARIO_LABELS],
                            "Max. napi DD", "Teljesülés", "Közös szűrő"], rows))
        bh = ep["buy_hold_initial_20"]
        body.append(f'<p>Kezdeti 20%-os vétel és tartás: {_pct(bh["return_pct"])} nettó, '
                    f'{bh["max_daily_close_drawdown_pct"]:.2f}% napi drawdown. Az equal_weight_20 '
                    'naponta újrasúlyozott összehasonlítás; ez két eltérő viszonyítási alap. '
                    'A teljesülésszám vételi és eladási eseményeket számol, nem lezárt ügyleteket.</p>')
        body.append('<h3>A teljes stratégiakészlet időrendi kiválasztása</h3>')
        for scenario, wf in ep["batch_walk_forward"].items():
            label = "Alapköltség" if scenario == "base" else "Kétszeres költség"
            if wf["result"] is None:
                body.append(f'<p>{label}: nincs elegendő adat tesztidőszakhoz.</p>')
                continue
            body.append(f'<p><strong>{label}: {_pct(wf["result"]["return_pct"])}</strong> · '
                        f'{len(wf["folds"])} tesztablak · {wf["result"]["fills"]} teljesülés.</p>')
            body.append('<details><summary>Tesztablakok és választott szabályok</summary>' +
                        _table(["Kezdet (UTC)", "Vége (kizáró)", "Nap", "Választott", "Nettó hozam"],
                               [[f["test_start_utc"][:10], f["test_end_exclusive_utc"][:10], f["days"],
                                 "készpénz" if f["chosen"] == "cash" else f["chosen"],
                                 _pct(f["return_pct"])] for f in wf["folds"]]) + '</details>')
        for name, result in ep["strategies"].items():
            definition = feed["strategy_definitions"][name]
            body.extend([f'<details><summary>{_text(name)} · éves bontás, ablakok és bizonytalanság</summary>',
                         f'<p>{_text(definition["description"])}</p>',
                         f'<p class="muted">Rögzített paraméterek: {_text(json.dumps(definition["parameters"], ensure_ascii=False))}</p>',
                         _table(["Év", "Vizsgált nap", "Nettó hozam"],
                                [[year, v["days"], _pct(v["return_pct"])] for year, v in result["years"].items()]),
                         _table(["Ablak kezdete", "Nap", "Nettó hozam", "Többlet vs újrasúlyozott alap (százalékpont)", "Teljesülés"],
                                [[w["start_utc"][:10], w["days"], _pct(w["return_pct"]),
                                  f'{w["excess_vs_rebalanced_pct"]:+.2f}', w["fills"]] for w in result["windows"]]),
                         _table(["Blokkhossz", "Évesített logátlag 95%-os CI", "Családonként korrigált p"],
                                [[b, ' … '.join(_pct(v) for v in ci["annualized_log_mean_ci95_pct"]),
                                  f'{ci["familywise_p_positive_mean"]:.4f}'] for b, ci in result["confidence_intervals"].items()])])
            failures = result["batch_gate_failures"]
            body.append('<p><strong>Az epizódokat együtt vizsgáló szűrő hibái:</strong> ' +
                        _text('; '.join(GATE_LABELS.get(x, x) for x in failures) if failures
                              else 'Nincs; kizárólag előzetes historikus jelölt.') + '</p></details>')
        body.append('</section>')
    body.extend(['<section><h2>Korlátok és kihagyott időszakok</h2><ul>',
                 *[f'<li>{_text(x)}</li>' for x in feed["limitations_hu"]], '</ul>',
                 _table(["Fenntartott időszak kezdete (UTC)", "Vége (UTC)"], m["excluded_prior_reserved_intervals_utc"]),
                 f'<p>{_text(m["data_exclusion_note"])}</p></section>',
                 '<section><h2>Ellenőrizhetőség</h2><p>A kód és az adat ujjlenyomata az eredeti méréshez tartozik; '
                 'a Git-revízió önmagában nem írja le a még nem commitolt forrást.</p>',
                 f'<p>Protokoll SHA-256: <code>{_text(feed["protocol_sha256"])}</code></p>',
                 _table(["Forrásfájl", "SHA-256"], feed["source_provenance"]["source_sha256"].items()),
                 _table(["Epizód / piac", "Adat SHA-256"],
                        [[c["name"] + ' / ' + item["symbol"], item.get("sha256", item.get("permitted_subset_sha256"))]
                         for c in feed["coverage"] for item in c["data_manifest"]]),
                 '</section><footer>Offline kutatási jelentés. Nincs benne élő számlaállapot vagy kereskedési vezérlés.</footer>'])
    css = """body{margin:0;background:#f4f6f8;color:#182630;font:15px/1.55 system-ui,sans-serif}main{max-width:1320px;margin:auto;padding:32px}header,section{background:white;border:1px solid #dbe2e7;border-radius:12px;padding:26px;margin-bottom:22px}header{border-top:6px solid #c18b2e}h1{font-size:36px;line-height:1.15;margin:12px 0}h2{font-size:24px;margin:0 0 12px}h3{font-size:18px}.eyebrow{letter-spacing:.15em;color:#667985;font-size:12px}.muted,footer{color:#667985}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid #dbe2e7;text-align:right;vertical-align:top}th:first-child,td:first-child{text-align:left}th{background:#edf3f6}td:first-child{overflow-wrap:anywhere}code{overflow-wrap:anywhere}.scroll{overflow:auto;margin:16px 0}details{margin:14px 0;padding:14px;border:1px solid #dbe2e7;border-radius:8px}summary{cursor:pointer;font-weight:600}li{margin:7px 0}footer{font-size:12px;padding:0 8px 24px}@media(max-width:700px){main{padding:12px}header,section{padding:16px}h1{font-size:29px}}@media print{body{background:white}main{padding:0}section,header{break-inside:avoid;border:0}.scroll{overflow:visible}table{font-size:9px}}"""
    return ('<!doctype html><html lang="hu"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>AutoTrading · ' + title + '</title><style>' + css +
            '</style></head><body><main>' + ''.join(body) + '</main></body></html>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--public-json", type=Path)
    args = parser.parse_args(argv)
    destinations = [args.html] + ([args.public_json] if args.public_json else [])
    if len({p.resolve() for p in [args.input, *destinations]}) != len(destinations) + 1:
        parser.error("Input and output paths must differ")
    try:
        feed = sanitized(json.loads(args.input.read_text(encoding="utf-8")))
        html = render_html(feed)
        public_json = json.dumps(feed, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(f"Invalid research report: {exc}")
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(html, encoding="utf-8")
    if args.public_json:
        args.public_json.parent.mkdir(parents=True, exist_ok=True)
        args.public_json.write_text(public_json, encoding="utf-8")
    print(f"Exported historical research: {feed['research_status']}; {len(feed['episodes'])} episodes")


if __name__ == "__main__":
    main()
