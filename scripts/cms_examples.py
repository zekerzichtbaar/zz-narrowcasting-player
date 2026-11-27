"""Exports examples of the Pi screens to the CMS: /schermen > "Zo ziet de tv eruit".

Run after every change to pi_player/screens.py (and commit the result in the CMS repo too):

    python3.13 scripts/cms_examples.py [path to the CMS repo, default ../zz-narrowcasting]

Needs Pillow and qrcode, like the screens self-check. Renders every screen with
`python -m pi_player.screens`, then writes a webp per example to `public/pi-voorbeelden/` and the
list with titles to `resources/js/lib/pi-screen-examples.ts` in the CMS. This list is the one
place for the examples: add or change a screen here, never by hand in the CMS.
"""
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image

# (file from pi_player.screens, title, when the tv shows it, orientation)
EXAMPLES = [
    ("pairing-connecting.png", "Opstarten", "Net aangezet: de speler zoekt verbinding met Zeker Zichtbaar.", "landscape"),
    ("pairing.png", "Koppelen", "Een nieuwe of ontkoppelde speler: koppel hem met deze code of QR op Schermen.", "landscape"),
    ("waiting.png", "Wacht op een playlist", "Gekoppeld, maar er is nog geen playlist gekozen of gepubliceerd.", "landscape"),
    ("identify.png", "Identificeren", "Na \"Scherm identificeren\" toont het scherm een minuut zijn naam.", "landscape"),
    ("update", "Bijwerken", "Tijdens een update staat linksonder dit label; de video speelt gewoon door.", "landscape"),
    ("wifi-clip.png", "Wifi instellen", "Geen netwerk na het opstarten, of \"Wifi opnieuw instellen\": scan de QR met je telefoon.", "landscape"),
    ("wifi-busy.png", "Verbinden met wifi", "Het scherm probeert het gekozen netwerk.", "landscape"),
    ("wifi-clip-error.png", "Wifi mislukt", "Verbinden lukte niet, bijvoorbeeld door een verkeerd wachtwoord.", "landscape"),
    ("wifi-clip-retry.png", "Opgeslagen netwerken proberen", "Elke minuut probeert het scherm de netwerken die het al kent.", "landscape"),
    ("wifi-clip-resume.png", "Speelt zo verder", "Heeft het scherm al iets om te spelen, dan speelt het na de countdown verder.", "landscape"),
    ("wifi-clip-noap.png", "Instelnetwerk start niet", "Het scherm kan zijn eigen instelnetwerk niet maken: sluit een netwerkkabel aan.", "landscape"),
    ("wifi-none.png", "Geen netwerk", "Zonder wifi-instelling: sluit een netwerkkabel aan.", "landscape"),
    # Portrait: the same screens on a panel hung at 90 degrees, same titles and text.
    ("pairing-connecting-90.png", "Opstarten", "Net aangezet: de speler zoekt verbinding met Zeker Zichtbaar.", "portrait"),
    ("pairing-90.png", "Koppelen", "Een nieuwe of ontkoppelde speler: koppel hem met deze code of QR op Schermen.", "portrait"),
    ("waiting-90.png", "Wacht op een playlist", "Gekoppeld, maar er is nog geen playlist gekozen of gepubliceerd.", "portrait"),
    ("identify-90.png", "Identificeren", "Na \"Scherm identificeren\" toont het scherm een minuut zijn naam.", "portrait"),
    ("update", "Bijwerken", "Tijdens een update staat linksonder dit label; de video speelt gewoon door.", "portrait"),
    ("wifi-clip-90.png", "Wifi instellen", "Geen netwerk na het opstarten, of \"Wifi opnieuw instellen\": scan de QR met je telefoon.", "portrait"),
    ("wifi-busy-90.png", "Verbinden met wifi", "Het scherm probeert het gekozen netwerk.", "portrait"),
    ("wifi-clip-error-90.png", "Wifi mislukt", "Verbinden lukte niet, bijvoorbeeld door een verkeerd wachtwoord.", "portrait"),
    ("wifi-clip-retry-90.png", "Opgeslagen netwerken proberen", "Elke minuut probeert het scherm de netwerken die het al kent.", "portrait"),
    ("wifi-clip-resume-90.png", "Speelt zo verder", "Heeft het scherm al iets om te spelen, dan speelt het na de countdown verder.", "portrait"),
    ("wifi-clip-noap-90.png", "Instelnetwerk start niet", "Het scherm kan zijn eigen instelnetwerk niet maken: sluit een netwerkkabel aan.", "portrait"),
    ("wifi-none-90.png", "Geen netwerk", "Zonder wifi-instelling: sluit een netwerkkabel aan.", "portrait"),
]

WIDTH = 1280  # long side of the webp: sharp in the dialog, small in the repo


def _update_frame(src, size=(1920, 1080)):
    """The update label sits bottom left over the playing video: put it on a dim stand-in frame.
    `src` is the upright label (rotation 0), `size` the upright frame (1080x1920 for portrait)."""
    w, h = size
    frame = Image.new("RGB", size)
    for y in range(h):  # a dark blue-grey gradient as "the video"
        v = int(70 - 40 * y / h)
        frame.paste((v, v + 8, v + 24), (0, y, w, y + 1))
    label = Image.open(src).convert("RGBA")
    frame.paste(label, (48, h - 48 - label.height), label)
    return frame


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cms = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "zz-narrowcasting"))
    out = os.path.join(cms, "public", "pi-voorbeelden")
    if not os.path.isdir(os.path.join(cms, "resources", "js")):
        sys.exit(f"Not a CMS repo: {cms}")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith(".webp"):
            os.remove(os.path.join(out, f))

    items = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "pi_player.screens", tmp], cwd=here, check=True,
                       stdout=subprocess.DEVNULL)
        for i, (src, title, text, orientation) in enumerate(EXAMPLES):
            if src == "update":  # built upright, no turning
                size = (1080, 1920) if orientation == "portrait" else (1920, 1080)
                img = _update_frame(os.path.join(tmp, "update-label-0.png"), size)
            else:
                img = Image.open(os.path.join(tmp, src)).convert("RGB")
            if orientation == "portrait" and src != "update":
                img = img.rotate(-90, expand=True)  # rendered for a panel hung 90 degrees clockwise: upright
            img.thumbnail((WIDTH, WIDTH))
            name = f"{i + 1:02d}-{os.path.splitext(src)[0]}{'-90' if src == 'update' and orientation == 'portrait' else ''}.webp"
            img.save(os.path.join(out, name), "WEBP", quality=82, method=6)
            items.append({"src": f"/pi-voorbeelden/{name}", "title": title, "text": text,
                          "orientation": orientation})

    ts = os.path.join(cms, "resources", "js", "lib", "pi-screen-examples.ts")
    with open(ts, "w") as f:
        f.write("// Generated by scripts/cms_examples.py in the player repo (zz-narrowcasting-player).\n")
        f.write("// Do not edit by hand: change the list there and run it again.\n\n")
        f.write("export type PiScreenExample = {\n\tsrc: string;\n\ttitle: string;\n\ttext: string;\n"
                "\torientation: 'landscape' | 'portrait';\n};\n\n")
        body = json.dumps(items, ensure_ascii=False, indent="\t")
        f.write(f"export const piScreenExamples: PiScreenExample[] = {body};\n")
    print(f"{len(items)} examples -> {out}")


if __name__ == "__main__":
    main()
