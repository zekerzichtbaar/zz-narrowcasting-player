Fonts of the setup screens (house style D3, the same as the CMS beheer).

- GT Walsheim Pro Regular, Medium, Bold (`GTWalsheimPro-*.ttf`): Grilli Type, licensed by Zeker
  Zichtbaar. Converted with fonttools from the woff2 webfonts in the CMS (`resources/fonts/`); the
  licence covers embedding them on the Pi (confirmed by the user, 2026-10-30). Not for other use.
- ZZ Impact (`ZZImpact.ttf`): Impact, from the same CMS webfont (`resources/fonts/impact.woff2`),
  converted the same way and under the same confirmation.
- Geist Mono Medium: SIL Open Font License 1.1 (https://openfontlicense.org), Copyright 2024 The Geist
  Project Authors (https://github.com/vercel/geist-font).
  Authors (https://github.com/ateliertriay/bricolage). Only still used by the hotspot setup page
  (`portal.py`), not by the screens.

Convert again (in a throwaway venv with `fonttools brotli`):
`python -c "from fontTools.ttLib import TTFont as F; f=F('in.woff2'); f.flavor=None; f.save('out.ttf')"`
