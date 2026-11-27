# Zeker Zichtbaar Narrowcasting - speler

Speler voor de Raspberry Pi 4 (`pi_player/`), plus de iOS-app met App Clip (`ios/`) en de Android-app (`android/`).
Het CMS staat in `~/Sites/zz-narrowcasting`; protocol in `docs/protocol.md`.

## Tv-schermen en de voorbeelden in het CMS

- De schermen die de Pi zelf tekent (koppelen, wachten, identificeren, update-label, wifi) staan in
  `pi_player/screens.py`. De hotspot-instelpagina voor de telefoon staat in `pi_player/portal.py`. Beide volgen de huisstijl
  D3 van het CMS: GT Walsheim, `ZZ Impact` voor koppen in hoofdletters, Geist Mono voor data en het z-logo met TV-sticker.
- Het CMS toont voorbeelden van deze schermen op `/schermen` ("Zo ziet de tv eruit"). Ze worden gemaakt door
  `scripts/cms_examples.py`. Daar staat ook de lijst met titels en uitleg.
- **Wijzig je `pi_player/screens.py` of komt er een scherm bij? Werk dan ook de voorbeelden in het CMS bij:**
  `python3.13 scripts/cms_examples.py` (Pillow en qrcode nodig). Een nieuw scherm voeg je eerst toe aan `EXAMPLES` in dat
  script. Het script schrijft `public/pi-voorbeelden/*.webp` en `resources/js/lib/pi-screen-examples.ts` in het CMS. Die
  wijzigingen horen in het CMS bij dezelfde aanpassing.
- Self-checks: `python3.13 -m pi_player.<module>`. Voor `screens` zijn Pillow en qrcode nodig, bijvoorbeeld in een venv.
  `python -m pi_player.screens <map>` maakt alle voorbeeld-png's.

## Afspraken

- Nooit committen zonder toestemming van de gebruiker.
- Geen em-dashes of en-dashes, alleen een gewoon streepje.
- Tab size 4.
