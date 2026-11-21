# Mintázatkeresés: indikátorok, order flow, naptár (2026-11-21)

Csak historikus kutatás, kereskedés nem történt. Kód:
[`features.py`](features.py), [`patterns.py`](patterns.py),
[`pattern_strategy.py`](pattern_strategy.py). Részletes eredmény:
`python3 -m lab4.patterns`, illetve a backteszthez `--backtest`.

## Módszer

- **41 jellemző minden érmére.** Mindegyik csak a döntés napjáig lezárt
  adatból számol:
  - hozamok (1–90 nap);
  - mozgóátlag-távolság;
  - MACD;
  - RSI(2/14);
  - sztochasztikus;
  - Bollinger %B;
  - Donchian;
  - 365 napos csúcs-közelség;
  - 90 napos visszaesés;
  - Kaufman-hatékonyság;
  - volatilitás, ATR, ferdeség, „lottó” (max/min napi hozam);
  - forgalom;
  - **order flow:**
    - taker buy arány;
    - a taker buy arány változása;
    - kötésszám;
    - átlagos kötésméret;
    - money flow index;
    - OBV-áramlás;
  - BTC-béta, BTC-korreláció, saját (idioszinkratikus) volatilitás.
- **Három kérdéscsalád, összesen 268 teszt:**
  - *Keresztmetszet:* melyik érme teljesít jobban a többinél a következő
    1/7/28 napban? (napi rangkorreláció, IC)
  - *BTC-időzítés:* előrejelzi-e a BTC saját jellemzője a BTC következő
    1/7/28 napját?
  - *Naptár:* a hét napja és az UTC napszak.
- **Szűrés:**
  - Benjamini–Hochberg FDR q=5% mind a 268 tesztre együtt;
  - azonos előjel és |t| ≥ 1 a fejlesztési időszak mindkét felében
    (2018-04–2020-06 és 2020-07–2022-12).
- **Valós historikus order book 2018–2022-re nem érhető el nyilvánosan.**
  Ezért az order flow-t a gyertyák taker buy forgalma közelíti.

## Eredmény

268 tesztből 24 ment át az FDR-szűrőn, ebből **18 volt stabil mindkét félidőben**.

**Ami túlélt (keresztmetszet: melyik érme teljesít jobban):**

| Jellemző | Irány | Horizont | 2023–2026-ban megmaradt? |
|---|---|---|---|
| saját volatilitás (idio_vol_90) | alacsony → jobb | 1/7/28 nap | igen (t = −5,6 / −3,0 / −4,3) |
| ATR / volatilitás / napi tartomány | alacsony → jobb | 1–7 nap | igen |
| 90 napos csúcshoz közel (drawdown_90) | közel → jobb | 1/7/28 nap | igen (t = +4,3 / +3,1 / +3,0) |
| forgalom (log_qv_30) | nagy → jobb | 1/7 nap | igen |
| legrosszabb napi hozam (min_ret_30) | enyhébb → jobb | 1 nap | igen |
| 3 napos hozam | visszafordul | 1 nap | igen (gyengén) |
| BTC-korreláció (corr_btc_90) | nagy → jobb | 1/7/28 nap | **nem** |

BTC-időzítés: egyetlen túlélő van, a BTC napi hozama a következő napon kicsit
visszafordul (ρ = −0,07). Ez kisebb, mint a ki-be kereskedés költsége.

**Ami nem élte túl:**

- MACD, RSI, sztochasztikus, Bollinger, Donchian;
- taker buy arány és változása, kötésszám, kötésméret, OBV;
- a 28 napos momentum rangsora;
- minden naptári hatás.

Közülük néhány egyenként „szignifikánsnak” látszott (MACD p=0,021,
RSI2 p=0,020, 21 órás napszak p=0,013). 268 tesztnél viszont kb. 13
ilyen véletlen találat várható.

**Értelmezés:** a kriptóban a stabil, likvid, a csúcsuk közelében lévő
érmék teljesítenek jobban, a „lottószerű”, ugráló érmék rosszabbul. Ez
egybevág a részvénypiaci alacsony-volatilitás és 52 hetes csúcs anomáliával.

## A mintázatokból épített stratégia: `pattern_top5_vt`

**Felépítés:**

- Az 5 jellemző, amelyik heti vagy hosszabb horizonton túlélt.
- Egyenlő súly, a felfedezéskori előjellel.
- Hetente a top 5 érme, BTC > SMA100 szűrővel, 40%-os
  volatilitás-célzással. Ez ugyanaz a váz, mint az `xs_mom_top5_vt`-é.
- A jellemzőket csak a fejlesztési adatból választottam, egyetlen súlyt sem
  illesztettem.

| | Fejlesztés CAGR / Sharpe / MDD | 2023–2026 CAGR / Sharpe / MDD | Évek 2023–2026 |
|---|---|---|---|
| `pattern_top5_vt` | 42,4% / 1,38 / 25% (mintán belül) | 23,2% / 0,82 / 34% | +61% / +30% / +23% / −13% |
| `xs_mom_top5_vt` (összevetés) | 32,4% / 1,25 / 29% | **33,1% / 1,19 / 33%** | +37% / +40% / +46% / +7% |
| S&P 500 (SPY) | 10,0% / 0,55 / 34% | 20,9% / 1,36 / 19% | |

**Tanulság:**

- A mintázatok statisztikailag megmaradtak, a belőlük épített portfólió mégis
  gyengébb lett a sima momentumnál.
- A fejlesztési időszakon jobbnak látszott (42% vs. 32%), utána rosszabb lett
  (23% vs. 33%). A felfedezés optimizmusa tehát itt is megjelent.
- A 2026-os medvepiacon −13%-ot hozott: a stabil nagy érmék is estek, a
  BTC-szűrő pedig késve kapcsolt.
- Az előre rögzített feltételeket nem teljesíti.

A kérésnek megfelelően **ez is bekerült a jövőbeli papírtesztbe**
(`paper/registry.json`, első kitöltés 2026-11-22). Az új adat dönti el, melyik
szabály működik valójában.
