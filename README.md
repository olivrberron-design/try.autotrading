# AutoTrading — Binance Spot Demo research

A Binance Spot Demo API-kapcsolatot és egy 25 USDC-s virtuális BTC-vételt már sikeresen kipróbáltunk. **A megvásárolt BTC-t nem adjuk el automatikusan.**

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
