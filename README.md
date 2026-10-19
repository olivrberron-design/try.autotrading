# AutoTrading — Binance Spot Demo research

A Binance Spot Demo API-kapcsolatot és egy 25 USDC-s virtuális BTC-vételt már sikeresen kipróbáltunk. **A megvásárolt BTC-t nem adjuk el automatikusan.**

## Strategy Lab 2.0 — egy kattintással, terminál nélkül

**[▶ Stratégiai tesztek indítása a GitHubon](https://github.com/olivrberron-design/try.autotrading/actions/workflows/strategy-lab.yml)**

Válaszd a **Run workflow** gombot, állítsd be a 365/540/730 napot, a BTC/ETH piacokat és a 12/24/36 véletlen időablakot, majd indítsd el. A GitHub szerverén fut, **nem kell Terminal vagy API-kulcs**, és nem ér hozzá a virtuális BTC-hez.

A **Summary** oldal mutatja mind a 15 stratégiát, külön az **abszolút medián hozamot**, a benchmarkhoz képesti medián előnyt, a drawdownt, a kötéseket és a piaci rezsimeket. Az **Artifacts** szekcióból két külön csomag tölthető le:
- `strategy-lab-dashboard-...`: egy `index.html`, amelyre duplán kattintva a Maceden a Chrome megnyitja az interaktív dashboardot. Van sorrendezhető rangsor, időszakonkénti grafikon, piaci rezsimek és kiválasztott stratégia részletei.
- `strategy-lab-...`: CSV és JSON fájlok a részletes kutatási eredményekhez.

### Opcionális állandó weboldal — GitHub Pages

Ha minden futás után egy állandó webcímen szeretnéd látni a legutolsó dashboardot, nyisd meg a GitHubon:
**Settings → Pages → Build and deployment → Source: GitHub Actions**.

Ezzel engedélyezed, hogy egy sikeres Strategy Lab-futtatás után a workflow automatikusan frissítse a **[Strategy Lab dashboard weboldalt](https://olivrberron-design.github.io/try.autotrading/)**. A beállítás előtt a link még nem biztos, hogy működik; GitHub Actions-ből az offline HTML mindenképpen letölthető. Ez **publikus oldal** egy publikus repóhoz, de csak historikus kutatási eredményeket tartalmaz, semmilyen API-kulcsot vagy számlaadatot nem.

### Milyen 15 stratégiát tesztel?

1. Baseline (EMA + RSI + MACD + volume)
2. RSI momentum + volume
3. RSI mean reversion
4. EMA pullback
5. Donchian breakout 20/10
6. Donchian breakout 55/20
7. Bollinger lower-band reversal
8. Bollinger upper-band breakout
9. MACD crossover
10. RSI strength cross
11. RSI(2) oversold recovery
12. Triple EMA alignment
13. 20-bar rate-of-change momentum
14. EMA + ATR channel breakout
15. Stochastic rebound

A szabályok a `autotrading/catalog.py` fájlban vannak dokumentálva. Ezek **saját tesztelhető implementációk közismert módszercsaládokból**, nem másolt, hitelesített nyereségreceptjei konkrét tradereknek.

### Miben szigorúbb a 2.0?

- A mintavételezett időablakok **nem fedik egymást**, de a hozamok ettől még lehetnek korreláltak. A 0% átfedést program és tesztkód ellenőrzi.
- **Minden stratégia ugyanazokat** a véletlenszerű szakaszokat kapja, azonos fee/slippage feltételekkel.
- Egyéves adaton 36 db különálló 30 napos teszt **nem fér el**. A rendszer automatikusan rövidebb (például 5/7/10/14 napos) tesztablakokat használ; másfél-két évnyi adaton hosszabb szakaszok is választhatók. Ha nem fér bele a vizsgálat, a rendszer hibával leáll, nem hamisítja a függetlenséget.
- A `discover` mód csak a múltbeli adatok első 75%-án kísérletezik. A jelenlegi 25%-os „fenntartott” időszak **nem tekinthető teljesen érintetlennek**, mert a korábbi, 2026-ig futó tesztek egy része már felfedte. A `validate` mód csak külön kérésre értékeli, és az eredményt utólag nem használhatjuk a szabályok hangolására. Valóban friss bizonyítékhoz **jövőbeli felügyelet nélküli demós forward teszt** kell.
- A stratégia csak előzetes kutatási szűrőn lép át, ha van elég kötése, pozitív az abszolút medián hozama, pozitív a benchmarkhoz képesti medián előnye, és az ablakok több mint felében felülmúlja a benchmarkot. **Ez önmagában nem profitgarancia.**
- Piaci rezsim: `bull`/`bear`/`sideways`, kizárólag az ablak elején **már lezárt** 1 órás adatok alapján. A rezsim az ablak során változhat; AI-modell még nincs.
- A kutatási futtatás a GitHubon alapértelmezés szerint **vasárnaponként automatikusan is elindul**, amennyiben a GitHub Actions engedélyezi.

A végső cél nem a múlt legjobb paramétereinek hajszolása, hanem új, időben később felvett piaci adatokon fennmaradó, költségek után is mérhető előny keresése.

---

## Historikus backtesting (BTC és ETH, 3 stratégia)

A backtest **csak adatot olvas** a Binance hivatalos nyilvános Spot Market Data API-jából: `https://data-api.binance.vision`. Nem használ `.env` kulcsokat, nem küld kereskedési megbízást, és **nem nyúl a demós BTC-hez**. Az összes kötés helyi matematikai szimuláció.

**GitHub Desktop:** Fetch origin → Pull origin → Repository → Open in Terminal, majd:

```bash
python3 -m unittest discover -s tests -v
python3 backtest.py
```

Első futáskor akár 1–3 percig is tarthat, mert a legutóbbi 180 nap + 16 nap indikátor-warmup **valódi 15 perces és órás BTCUSDC/ETHUSDC adatait** tölti le. A letöltött adatokat a `data/history` alatt gyorsítótárazza, a jelentéseket a `data/backtests` alá írja. Ezek nem kerülnek GitHubra.

Rövidebb első próba (30 nap):

```bash
python3 backtest.py --days 30 --symbol BTCUSDC
```

Teljes egyéves teszt:

```bash
python3 backtest.py --days 365
```

Külön költségfeltételekkel:

```bash
python3 backtest.py --days 180 --fee-bps 10 --slippage-bps 5 --allocation 0.2
```

**A modellezési feltételek és korlátok:**
- Három kutatási stratégia: korábbi **EMA+RSI+MACD+volumen** baseline, momentum, RSI mean-reversion.
- Minden stratégia **külön**, 5000 USDC indulótőkével szimulál; nincs valódi tőkeáttétel, short vagy több párból összerakott portfólió.
- Alapesetben egyszerre a szabad cash **20%-át** teszi virtuális pozícióba; az egyes stratégiák **nem kezelik a tényleges Binance Demo-egyenleget**.
- Jelzés kizárólag **lezárt 15m gyertyából** és addig lezárult 1h adatokból; végrehajtás a **következő 15m gyertya nyitóján**.
- Mindkét oldalon **10 bázispont** fee és **5 bázispont** kedvezőtlen slippage az alapbeállítás. Ezek feltételezések, nem élő díjak vagy garantált végrehajtási árak.
- **70% train / 30% holdout:** a kiválasztott stratégia kizárólag a train teljesítménye alapján dől el, a holdout csak a későbbi értékelésre szolgál. A holdout kezdetén minden szimulált portfólió újra 5000 USDC-vel indul, a train-ből nyitott pozíció nem megy át.
- A táblázatban hozam, maximum drawdown, lezárt kötések száma, win rate és **azonos 20%-os allokációjú buy-and-hold** szerepel. A részletes JSON a full buy-and-holdot, profit factort, kötésnaplót és a nyitott pozíció becsült felszámolási értékét is tartalmazza.
- **Nem modellezünk stop-loss megbízást**, könyvlikviditást, finanszírozási költséget, változó fee tier-eket vagy order-size filtereket; egy gyertyán belüli mozgás nem garantál teljesíthető árat.
- Ha nincs lezárt kötés, a win rate és profit factor **N/A**, nem 0% vagy 100%. A szimuláció teljesítménye önmagában nem bizonyít jövőbeli előnyt.
- A train/test ismételt válogatása idővel szintén túlillesztést okozhat; később walk-forward validációra és több független időszakra lesz szükség.

A régebbi `demo_smoke_test.py --trade` továbbra is **külön, kifejezett demós megbízási próba**. A backtesthez ezt **ne futtasd**.

Forrás: [Binance Spot Market Data Only](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md).

---

## Technikai elemzés — új, csak olvasó mód

A technikai elemző modul **nem használ API-kulcsot, nem helyez el megbízást, és nem módosítja a számládon lévő BTC-t**. A Binance demó piaci adataiból (lezárt gyertyákból) dolgozik.

GitHub Desktopban: **Fetch origin**, majd **Pull origin**. A projektmappában Terminal:

```sh
python3 analyze.py
```

Kizárólag BTC/USDC:

```sh
python3 analyze.py --symbol BTCUSDC
```

Csak kiírás, helyi napló nélkül:

```sh
python3 analyze.py --no-log
```

A program BTC/USDC és ETH/USDC párokról lekér **15 perces és 1 órás** gyertyákat. EMA(20/50) keresztet, 1 órás EMA(50/200) trendet, RSI(14)-et, MACD(12/26/9)-et, ATR(14)-et és volumenfeltételt vizsgál. Az elemzést helyben a `data/signals.jsonl` fájlba naplózza, ez Gitből kizárt.

Az `ENTRY_CANDIDATE`, `EXIT_CANDIDATE` és `HOLD` értékek **kutatási jelzések, nem megbízások vagy befektetési ajánlások**. Egy belépési jel sem jelent bizonyítottan nyereséges stratégiát. Ez az első, magyarázható baseline. Következő fejlesztések: backtest (fee/slippage), papírtrading, majd előre meghatározott risk engine és külön jóváhagyott, hosszú távú Demo végrehajtás.

## Korábbi Binance Demo API smoke test

A `demo_smoke_test.py` a különálló API-megvalósíthatósági próba, nem a technikai elemző. A `--trade` mód megbízást küld a **virtuális DEMO** számlára, ezért ne indítsd újra, ha már vásároltál! Az állapotot ellenőrizheted:

```sh
python3 demo_smoke_test.py --status
```

A `--status` nem helyez el megbízást. A virtuális BTC-t változatlanul hagyja.

## Biztonság és hozzáférés

- Mindkét modul csak a `https://demo-api.binance.com` címet használja.
- Csak a smoke test igényel DEMO API-kulcsot, helyi `.env` fájlban.
- A `.env`, az állapotfájl és a helyi adatnapló kizárt a Gitből; a kulcsokat soha ne küldd el chaten vagy commitban.
- A piaci adatok és tesztpozíciók nem garantálják az éles kötési árakat, likviditást vagy stratégiateljesítményt.

## Tesztek

```sh
python3 -m unittest discover -s tests -v
```

A tesztek nem küldenek megbízást, és nem kérnek API-kulcsot.

## Dokumentáció

- [Binance Spot Demo](https://developers.binance.info/en/docs/products/spot/demo-mode/general-info)
- [Binance Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api)
