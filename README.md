# AutoTrading — Binance Spot Demo API feasibility

Ez a repository **kizárólag virtuális Binance Spot Demo kereskedési teszthez** készült. Az első lépés egy 25 USDC-s BTC/USDC piaci vétel és a kapott BTC visszaeladása.

## Biztonsági korlátok

- Az API szerver címe a programban rögzített: `https://demo-api.binance.com`. Éles Binance API-n nem tud kereskedni.
- Az alapértelmezett futás csak olvas és a Binance `/order/test` végpontját próbálja.
- Valódi **virtuális** demómegbízás csak `--trade` és külön kézi megerősítés után történik.
- API-kulcs nem kerül a GitHubra: helyi `.env` fájlban tároljuk.
- Egy sikertelen vagy bizonytalan megbízást a script **nem ismétel automatikusan**; a folyamat állapota külön fájlban rögzül.
- A demo kötés valódi piaci áraktól eltérő eredményt adhat, költség és spread miatt veszteséges is lehet.

## Gyors indítás Macen

1. Töltsd le/klónozd a repositoryt a saját gépedre, és lépj a mappába.
2. Másold a `.env.example` fájlt `.env` néven. Egy egyszerű szövegszerkesztővel illeszd be a **Demo API Key** és **Demo Secret Key** értékét. Csak helyben mentsd.
3. Kapcsolat és teszt-megbízás (nincs vétel/eladás):
   ```sh
   python3 demo_smoke_test.py
   ```
4. Virtuális 25 USDC-s BTC/USDC vétel és utána eladás:
   ```sh
   python3 demo_smoke_test.py --trade
   ```
   A program külön megerősítést kér.
5. Ha megszakadt valami, csak ellenőrizd a státuszt; ne indíts új megbízást:
   ```sh
   python3 demo_smoke_test.py --status
   ```

**Ne küldd el a kulcsokat chatben, és ne másold be GitHubra.** Az `.env` és a teszt állapotfájl is szerepel a `.gitignore`-ban. Ha már egyszer használtad a `--trade` módot, a teszt állapotfájlt szándékosan nem töröljük automatikusan: előbb egyeztesd a számla megbízástörténetével.

## Tesztek (nem küldenek kereskedési megbízást)

```sh
python3 -m unittest discover -s tests -v
```

## Dokumentáció

- [Binance Spot Demo](https://developers.binance.info/en/docs/products/spot/demo-mode/general-info)
- [Binance Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api)

Ez *feasibility proof*, nem kereskedési stratégia, nem AI-trader, és nem hoz létre éles pozíciókat.
