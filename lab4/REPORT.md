# lab4 kutatási jelentés (2026-11-21)

**Állapot: NINCS VALIDÁLT NYERŐ STRATÉGIA.** A 18 tesztelt stratégia közül egyik
sem teljesítette az összes előre rögzített feltételt a fejlesztési
(2018-04 – 2022-12) és a félretett (holdout, 2023-01 – 2026-10) időszakban
együtt. Ez csak historikus szimuláció: demo vagy éles kereskedés, megbízás
nem történt, API-kulcs nem lett betöltve.

Protokoll és döntési napló: [`PROTOCOL.md`](PROTOCOL.md). A kód a holdout
előtt be lett fagyasztva (commit `720fe0a`). Gépi összefoglaló:
[`reports/lab4-latest.json`](../reports/lab4-latest.json).

## Mit teszteltem

- **Adat:** Binance publikus napi és órás gyertyák 16 érmére (a 2019-01-01-i
  top-20 lista, a később kihalt érmékkel együtt), BTC/ETH perpetual árak és
  funding. **S&P 500 = SPY osztalékkal korrigált árfolyam**, a FRED S&P 500
  indexével ellenőrizve (napi korreláció 0,998).
- **Költség:** 0,15% oldalanként (díj + csúszás); stresszteszt: 0,30%.
- **Végrehajtás:** a jel csak lezárt gyertyából számol, a kötés a következő
  napi nyitáson történik. Long-only spot, tőkeáttét nélkül, legfeljebb 100%
  kitettség. Kivétel a carry és a short láb (perpetual, 1x).
- **Nyerő stratégia feltételei** (mindkét időszakban): éves hozam (CAGR) ≥
  S&P + 5 százalékpont, Sharpe ≥ S&P, legnagyobb visszaesés ≤ 40%, a
  gördülő 6 hónapos ablakok ≥ 70%-a pozitív, nincs −10%-nál rosszabb év,
  dupla költséggel is pozitív. A holdoutban ezen felül a bootstrap 5%-os
  alsó becslése is pozitív kell legyen.

## Eredmények (alapköltséggel)

| Stratégia | Fejlesztés CAGR / Sharpe / MDD | Holdout CAGR / Sharpe / MDD | Megjegyzés |
|---|---|---|---|
| **S&P 500 (SPY)** | 10,0% / 0,55 / 34% | 20,9% / 1,36 / 19% | benchmark |
| BTC tartás | 20,1% / 0,62 / 77% | 49,6% / 1,10 / 53% | benchmark |
| **`xs_mom_top5_vt`** | 32,4% / 1,25 / 29% | **33,1% / 1,19 / 33%** | legjobb jelölt, lásd lent |
| `core_blend_lo` | 35,3% / 1,42 / 23% | 23,1% / 0,99 / 27% | holdoutban minden év pozitív |
| `core_blend` | 34,0% / 1,31 / 25% | 24,0% / 0,98 / 29% | holdoutban minden év pozitív |
| `xs_mom_top5` | 73,6% / 1,21 / 46% | 35,7% / 0,86 / 50% | túl nagy visszaesés |
| `tsmom_universe_vt` | 21,5% / 0,72 / 41% | 31,3% / 0,97 / 37% | 2026: −13% |
| `btceth_trend_vt` | 33,5% / 1,28 / 33% | 12,6% / 0,59 / 26% | fejlesztési győztes, holdoutban visszaesett |
| `trend_carry_overlay` | 35,2% / 1,33 / 31% | 10,0% / 0,50 / 28% | ugyanígy |
| `btceth_trend_ls_vt` | 29,6% / 1,00 / 41% | 13,0% / 0,54 / 30% | a short láb vegyes |
| `funding_carry` | 4,9% / 5,42 / 3% | 0,3% / 0,47 / 4% | a carry 2023 után eltűnt |
| `regime_switch` (rögzített) | 22,9% / 1,06 / 38% | 10,2% / 0,50 / 33% | |
| `regime_learned` (tanult) | 99,8%\* / 1,48 / 47% | 20,5% / 0,63 / 55% | \*a fejlesztési szám mintán belüli |
| `xs_ridge_rank` (ML) | 80,2% / 1,31 / 50% | 19,4% / 0,61 / 57% | |
| `vbo_btceth` (napon belüli) | 21,4% / 0,85 / 40% | 8,8% / 0,53 / 21% | dupla költséggel −13,6%/év |
| `hourly_rsi2_btc` (órás) | −22,5% / −1,21 / 72% | **−41,9% / −4,20 / 88%** | a költség megeszi |

## Fő tanulságok

1. **A gyakori kereskedés nem működik.** Az órás RSI2 skalpolás minden évben
   veszített (holdout: −42%/év). A napon belüli kitörés (VBO) dupla
   költséggel negatív. A legjobb stratégiák hetente kötnek (napi 0,3–0,5
   megbízás).
2. **A fejlesztési időszak győztesei a holdoutban visszaestek.** A BTC/ETH
   trend 33,5%-ról 12,6%-ra, a tanult helyzetváltó 100%-ról 20%-ra, a ridge
   modell 80%-ról 19%-ra. Ez a túlillesztés tipikus jele, ezért kell
   félretett időszak.
3. **Egyetlen stabil jelet találtam: keresztmetszeti momentum
   volatilitás-célzással.** Mindkét időszakban évi kb. 33%, és 8
   paraméterváltozat mindegyike 24–38%/év között maradt (lásd lent).
4. **A funding carry** 2020–21-ben évi 10–17%-ot hozott, 2023 óta nagyjából
   nullát.
5. **Helyzetfelismerés alapú váltás:** az adatból tanult leképezés túltanult
   (55%-os visszaesés). A rögzített leképezés gyenge lett. A piaci helyzetek
   térképe ettől még hasznos diagnosztikának.

## Melyik stratégia milyen piaci környezetben jó

A BTC piaci helyzete a döntés pillanatában (múltbeli adatból):

- **Bika:** az ár az SMA200 felett, és az SMA50 az SMA200 felett.
- **Medve:** mindkettő alatta.
- **Átmeneti:** a kettő között.
- **Magas/alacsony volatilitás:** a 30 napos volatilitás a saját 1 éves mediánja felett/alatt.

Az értékek évesített átlaghozamok.

Fejlesztés (2018-04 – 2022-12; napok: 333 / 290 / 110 / 112 / 293 / 598):

| Stratégia | bika, magas vol. | bika, alacsony vol. | átmeneti, magas vol. | átmeneti, alacsony vol. | medve, magas vol. | medve, alacsony vol. |
|---|---|---|---|---|---|---|
| `xs_mom_top5_vt` | +92% | +69% | −11% | −2% | +18% | −1% |
| `btceth_trend_vt` | +117% | +33% | −18% | +4% | +3% | +13% |
| `btceth_trend_ls_vt` | +117% | +33% | −18% | +4% | +12% | +5% |
| `funding_carry` | +14% | +11% | 0% | +12% | −2% | −1% |
| `vbo_btceth` | +66% | +26% | −83% | −70% | +30% | +31% |
| `hourly_rsi2_btc` | +16% | −33% | −69% | −33% | −39% | −23% |
| BTC tartás | +203% | −20% | +101% | +39% | −32% | +16% |

Holdout (2023-01 – 2026-10; napok: 339 / 477 / 82 / 107 / 207 / 188):

| Stratégia | bika, magas vol. | bika, alacsony vol. | átmeneti, magas vol. | átmeneti, alacsony vol. | medve, magas vol. | medve, alacsony vol. |
|---|---|---|---|---|---|---|
| `xs_mom_top5_vt` | +69% | +19% | +26% | +83% | +2% | +5% |
| `btceth_trend_vt` | +43% | +14% | +2% | 0% | 0% | 0% |
| `btceth_trend_ls_vt` | +58% | +1% | +27% | +1% | −36% | +47% |
| `funding_carry` | +4% | 0% | −2% | −1% | −2% | −1% |
| `vbo_btceth` | −2% | +14% | −82% | +76% | +25% | +10% |
| `hourly_rsi2_btc` | −68% | −52% | −22% | −26% | −78% | −32% |
| BTC tartás | +65% | +16% | +107% | +168% | +55% | +16% |

Mit mutat a térkép:

- **Bikapiac:** itt keletkezik a profit nagy része. A momentum és a trend is
  működik, de a momentum stabilabb mindkét időszakban.
- **Átmeneti szakasz, magas volatilitás:** az oldalazó, ugráló piac. A trend
  és a kitörés itt veszít a legtöbbet.
- **Medvepiac:** a long-only stratégiák nagyrészt készpénzben vannak (~0%).
  A short láb a fejlesztési időszakban rontott, 2026-ban segített. Ez nem
  megbízható.
- **A helyzetfelismerés késik.** A mozgóátlagok miatt a „medve” címke a
  fordulók elején is rajta marad. Ezért mutat a BTC-tartás a holdout „medve”
  napjain is pluszt. Ezért nem lehet a helyzetcímkére vakon stratégiát váltani.

## A legjobb jelölt: `xs_mom_top5_vt`

**Szabály:**

- Hetente rangsorolja a 16 érmét a 28 napos hozamuk alapján.
- A top 5 pozitív hozamú érmét tartja, egyenlő súllyal.
- Csak akkor fektet be, ha a BTC a 100 napos mozgóátlaga felett van.
- A pozíciót 40%-os éves portfólió-volatilitásra skálázza, tőkeáttét nélkül.

**Mit mutatnak a számok:**

- Mindkét időszakban jelentősen többet hozott, mint az S&P: a fejlesztési
  időszakban +22, a holdoutban +12 százalékponttal.
- A holdoutban minden éve pozitív (37% / 40% / 46% / 7%).
- A gördülő 6 hónapos ablakok 80%-a pozitív, a bootstrap 5%-os alsó becslése
  +6,7%/év.

**Miért nem nyerő mégsem:**

- A holdoutban a Sharpe 1,19, az S&P-é viszont 1,36. Kockázattal korrigálva
  tehát nem verte az S&P-t.
- A fejlesztési időszakban 2018-ban −26%-os év van, és csak az ablakok 65%-a
  pozitív.

Utólagos stabilitási ellenőrzés: a paraméter-szomszédokat a holdout
befagyasztása után néztem meg, ezért nem választásra, csak diagnosztikára
használom.

| Változat | Fejlesztés CAGR / Sharpe / MDD | Holdout CAGR / Sharpe / MDD |
|---|---|---|
| bejegyzett (28 nap, top 5, BTC>SMA100, heti) | 32,4% / 1,25 / 29% | 33,1% / 1,19 / 33% |
| 14 napos visszatekintés | 36,2% / 1,33 / 26% | 25,5% / 0,93 / 34% |
| 56 napos visszatekintés | 25,6% / 1,06 / 28% | 24,3% / 0,94 / 30% |
| top 3 | 24,7% / 1,02 / 29% | 36,4% / 1,23 / 33% |
| top 7 | 31,6% / 1,22 / 30% | 35,1% / 1,23 / 37% |
| BTC>SMA50 szűrő | 29,0% / 1,12 / 32% | 33,2% / 1,21 / 28% |
| BTC>SMA200 szűrő | 26,6% / 1,14 / 40% | 37,7% / 1,28 / 26% |
| kéthetente | 31,7% / 1,25 / 25% | 30,6% / 1,10 / 31% |

A jel stabil sáv, nem egy szerencsés paraméter. A tőke átlagosan csak
18–35%-a van befektetve.

## Mintázatkeresés (indikátorok, order flow, naptár)

- 41 jellemzőt (MACD, RSI, Bollinger, order flow stb.) teszteltem 268
  tesztben, korrekcióval a sok párhuzamos teszt miatt.
- 18 stabil mintázat maradt. Főleg az alacsony volatilitás, a 90 napos csúcs
  közelsége és a likviditás jelez előre. A klasszikus indikátorok és az order
  flow nem élték túl a szűrést.
- A belőlük épített `pattern_top5_vt` stratégia 2023–2026-ban évi 23%-ot
  hozott, ez gyengébb, mint a sima momentum 33%-a.
- Részletek: [`PATTERNS.md`](PATTERNS.md).

## Korlátok és torzítások

- Összesen 18 stratégiát próbáltam ki. A legjobb holdout-eredmény ezért
  optimistán torzított.
- A BTC/ETH 2023–2026-os árait korábbi kutatások már látták. A 2023–2026-os
  kriptótörténet általánosan ismert.
- A 2019-es univerzum-lista miatt 2018-ban enyhe túlélési torzítás van.
- A szabad készpénz 0%-ot kamatozik (konzervatív). A drawdown napi záróárból
  számolt, ezért a napon belüli visszaesést alulbecsüli.
- A historikus eredmény nem garancia a jövőbeli hozamra.

## Javasolt következő lépés

1. **Ugyanezen a történeten nem érdemes tovább keresgélni.** A holdout el van
   használva. Ha addig próbálkozom, amíg valami átmegy, az biztosan átmegy, de
   hamisan. Ez túlillesztés.
2. **Előre rögzített papírteszt új adaton: elindítva 2026-11-21-én.**
   - Az `xs_mom_top5_vt` és a `btceth_trend_vt` SHA-256-tal befagyasztva fut, az
     első hipotetikus kitöltés a 2026-11-22-i nyitás.
   - Naponta a GitHub Actions `lab4 forward paper test` workflow futtatja, a
     kimenet a [`paper/forward.json`](../paper/forward.json), a leírás a
     [`paper/README.md`](../paper/README.md).
   - A `core_blend_lo` nem került be. A carry lábához napi funding adat kellene,
     a Binance publikus archívuma viszont csak havonta közli, a futures API-t
     pedig a GitHub amerikai szerverei nem érik el.
3. Az élő vagy demo kereskedés csak a papírteszt után, külön döntéssel jöhet
   szóba.
