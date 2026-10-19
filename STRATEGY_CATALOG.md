# Strategy Lab 2.0 — módszertani katalógus

**Nem „nyereséges gurustratégiákat” másolunk, hanem 15 előre rögzített, tesztelhető HIPOTÉZIST hasonlítunk össze.** A historikus adatokon jó eredmény nem bizonyítja, hogy élesben is működik.

## Katalógus

| ID | Család | Fő jelzés | Fontos eltérés / kockázat |
|---|---|---|---|
| baseline | EMA/RSI/MACD | EMA20 kereszt EMA50 felett, RSI és MACD megerősítés | Alacsony kötésszám |
| momentum | RSI + volumen | RSI14 55 fölé keresztezi; 1h bullish | Zajos, gyors pozíciózárások |
| mean_reversion | RSI14 | Oversold RSI kilépés, 1h bullish | Erős esésben sok hibás jel |
| ema_pullback | trend-pullback | EMA20 visszavétele 15m, 1h bullish | Lehet késői belépés |
| donchian_20 | csatorna-kitörés | 20 darab 15m gyertya csúcsának áttörése | NEM az eredeti 20 NAPOS Turtle |
| donchian_55 | csatorna-kitörés | 55 darab 15m gyertya csúcsának áttörése | NEM az eredeti 55 NAPOS Turtle |
| bollinger_reversal | átlaghoz visszatérés | Alsó Bollinger-band alól visszatérés | A band érintése nem önmagában vételi jel |
| bollinger_breakout | trend-kitörés | Felső Bollinger-band + volumen | Hamis kitörések kockázata |
| macd_crossover | momentum | MACD és signal bullish cross | Lassú, késői jelzések |
| rsi_trend | RSI-trend | RSI14 55 fölé megy emelkedő trendben | Oldalazó piacon fals jel |
| rsi2_dip | RSI(2) | RSI2 10 alól visszalép, bullish trend | Az eredeti Connors-módszertől eltér |
| ema_triple | több mozgóátlag | EMA20 > EMA50 > EMA100 új sorrend | Kevés jel |
| roc_momentum | time-series momentum | 20 darab 15m változás >1,5% | Nem azonos akadémiai idősoros futures-modellel |
| atr_channel | volatilitás-kitörés | 1,5×ATR feletti EMA20 kitörés | Trenden belüli árzaj |
| stochastic_rebound | oszcillátor | Stochastic K 25 fölé ugrik | Gyors és zajos gyertyák |

Mindegyik **long-only Spot** modell, short, tőkeáttétel és valódi megbízás nélkül. A piaci trendet az *addig lezárult* 1h EMA50/EMA200 jelzi. Jelzés csak a már lezárt 15m bar végén; feltételezett kötés a következő 15m bar nyitóján.

## Források, és mit NEM állítunk

- [Richard Dennis és a Turtle-rendszer eredeti szabályai](https://tradingblox.com/originalturtles/originalturtlerules.htm) — az eredeti rendszerek **20/55 NAPOS** csatornával, N/ATR kockázati pozíciómérettel, pyramidinggal és stopokkal dolgoztak. A Strategy Lab jelenlegi 20/55 **GYERTYÁS**, 15 perces *variánsai* nem jelentik az eredeti rendszer reprodukálását.
- [Bollinger Bands hivatalos szabályai](https://www.bollingerbands.com/bollinger-band-rules) — a 20 periódus és ±2 szórás tipikus alapbeállítás, de a band megérintése önmagában nem vételi/eladási jel.
- [Larry Connors RSI(2) elemzés, StockCharts](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2) — eredetileg napi, 200 napos átlaggal szűrt részvénystratégia. A mi 15m, hourly-regime szűrős, 10 alól visszapattanásos változatunk **önálló hipotézis**, nem hű másolat.

A „népszerű” nem jelent profitábilisat. Minden változtatást verzióhoz és dátumhoz kötünk; a már megtekintett 2026-os tesztidőszakot nem nevezhetjük valódi out-of-sample kontrollnak. A 15 módszer rangsorolása növeli a többszörös tesztelés problémáját, amelynek mérséklésére később külön walk-forward és új, valóban előre érkező demó-adatok kellenek.

## Mi hiányzik még, mielőtt AI választ stratégiát?

1. Több évből valóban több különálló bull/bear/sideways korszak (nem csupán egy 1 órás induló EMA-állapot).
2. Piaci rezsim változásainak csak múltbeli adatokból történő, minden döntésnél frissített mérése.
3. Költség- és slippage-stresszteszt, minimum kötés- és esetszámmal.
4. Walk-forward kutatás rögzített tanító/teszt időrendben, majd új, **jövőbeli** felügyelet nélküli demó megfigyelés.
5. Élesítés csak biztonsági, kockázati és üzemeltetési ellenőrzések után.
