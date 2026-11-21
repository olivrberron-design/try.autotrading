# Előre rögzített papírteszt (forward test)

A `paper/registry.json`-ban befagyasztott stratégiák **naponta, új piaci adaton**
futnak. Csak hipotetikus kötések: nincs API-kulcs, nincs tőzsdei fiók, nincs megbízás.

- **Ki futtatja:** a `lab4 forward paper test` GitHub Actions workflow minden nap
  00:23 UTC körül.
- **Döntés:** csak a 00:00 UTC-kor lezárult napi gyertyákból.
- **Kitöltés:** az aznapi hivatalos nyitóáron, 0,15%/oldal költséggel. A motor
  pontosan ugyanaz, mint a backtesté (`lab4/engine.py`), és egy ellenőrzés szerint
  egy 143 napos múltbeli ablakon centre azonos eredményt ad.
- **Befagyasztás:** a regisztrációkor a stratégia kódfájljainak SHA-256 lenyomata
  rögzítve van. Ha bármelyik fájl változik, a futás leáll. Stratégiát módosítani
  csak új azonosítóval és új kezdőnappal lehet, a meglévő rekord nem írható át
  utólag.
- **Kimenet:** a `paper/forward.json` tartalma:
  - tőkegörbe;
  - minden napi döntés (súlyok);
  - a BTC tartás és az S&P 500 (SPY) összehasonlítása ugyanarra az időszakra.
  
  A git-történet mutatja, hogy minden napi döntés publikus volt, mielőtt a
  kimenetele kiderült.
- **Kiértékelés:** legalább 3–6 hónap után. Ennél rövidebb idő alatt a
  véletlen dominál.

Regisztrált stratégiák (mindegyik első kitöltési napja 2026-11-22):

- `xs_mom_top5_vt`: a legjobb historikus jelölt;
- `btceth_trend_vt`: referencia;
- `pattern_top5_vt`: a mintázatkeresésből épített stratégia, lásd
  [`lab4/PATTERNS.md`](../lab4/PATTERNS.md).
