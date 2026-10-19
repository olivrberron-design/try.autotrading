# AutoTrading — Binance Spot Demo research

A Binance Spot Demo API-kapcsolatot és egy 25 USDC-s virtuális BTC-vételt már sikeresen kipróbáltunk. **A megvásárolt BTC-t nem adjuk el automatikusan.**

## 🧪 Strategy Lab: böngészőből indítható, nincs Terminal

👉 **[Strategy Lab elindítása a GitHubon](https://github.com/olivrberron-design/try.autotrading/actions/workflows/strategy-lab.yml)**

1. Nyisd meg a fenti linket (GitHub belépés szükséges).
2. Jobb oldalt kattints a **Run workflow** gombra.
3. Hagyd az alapbeállításokat: `discover`, `365` nap, `both` piac, `24` véletlenszerű időszak/piac, `42` reprodukálható seed.
4. Kattints a zöld **Run workflow** gombra. A GitHub szerverén fut, nem a Maceden; terminálra és API-kulcsra nincs szükség.
5. A megjelenő futtatásra kattintva a **Summary** oldalon látod a stratégiák rangsorát és a különböző piaci rezsimek szerinti eredményeket. Az oldal alján az **Artifacts** részben elérhetők a részletes CSV/JSON jelentések.

**Az alapértelmezett `discover` módban a legutolsó 25% időszakot szándékosan NEM nézzük meg**, így megmarad egy kronológiai végső teszt. A `validate` választást csak egyszer, a kiválasztott stratégia és paraméterek rögzítése után használd; ez már feltárja a tesztidőszakot.

**Régebbi időszakokhoz** válaszd az `540` (másfél év) vagy `730` (két év) napot. A minták a történelmi szakaszból véletlenszerűen indulnak, nem csak a mostani napból visszafelé készülnek. A tesztablakok hossza vegyesen 14, 30, 45, 60 vagy 90 nap. Azonos seed esetén azonos mintavétel készül ugyanazon az adatkészleten, hogy reprodukálható legyen a vizsgálat.

**Hetente vasárnap automatikusan lefut** az alapértelmezett, csak kutatási módú teszt (GitHub Actions ütemezéssel). Az előző futtatásokat az **Actions** lapon lehet visszanézni. A heti indulás GitHub-szolgáltatási feltételektől és Actions-beállításoktól függ; időnként késhet, inaktív nyilvános repóban leállhat.

### Mit mér a Strategy Lab?

- A három **jelenleg implementált** stratégia (baseline, momentum, mean reversion) teljesítményét egyenként, azonos random ablakokban.
- Medián többlethozam az azonos 20%-os kitettségű buy-and-holdhoz képest; hány ablakban verte a benchmarkot; legrosszabb hozam, maximális drawdown.
- A vizsgált időszak indulásakor *már ismert* piaci környezet: bull, bear vagy sideways az 1 órás EMA50/EMA200 alapján. Ez fontos alap az AI-alapú stratégia-kiválasztáshoz, de maga **még nem AI**.
- A pozitív medián benchmark-felülteljesítés és több mint 50%-os ablak-győzelem minimális kutatási szűrő, **nem igazol profitábilis stratégiát**.
- A véletlen időszakok **átfedhetnek**, így az eredmények nem független megfigyelések. Az átfedést a jelentés mutatja; ne számold az átfedő trade-eket külön bizonyítéknak.

A jelenlegi stratégia-implementációk változatlan paraméterűek: a következő fejlesztés a szélesebb, előre regisztrált stratégia-katalógus és a valóban időrendben végzett walk-forward validáció lesz. A webes futtatás nem igényel helyi `.env` fájlt. **A demós BTC-hez nem nyúl.**

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
