## Your turn (9)

1. **Prepare and deploy the CMS** — in `../zz-narrowcasting` run `npm run check`, check the migration timestamps `2026_10_28_120000`/`130000` against upstream, then deploy
   → lint passes; a paired screen's menu shows "Beheer-QR (sticker)"
2. **Print a sticker** — open `/schermen/{screen}/beheer-qr` for a paired Pi, print it and stick it on the Pi
   → a 60 mm sticker whose QR opens `https://app.zekerzichtbaar.tv/setup?d=<id4>&s=<secret>`
3. **Status on both phones** — scan the sticker with the installed app on an iPhone and on an Android phone
   → each shows the screen name, network + signal, IP, online/paired and player version
4. **Switch network while online** — on each phone pick another network with the right password, then once with a wrong password
   → Pi joins and stays online in the CMS; the wrong password leaves it on its old network and the app shows the failure
5. **Survive offline + restart** — cut the Pi off the network, restart it, scan the sticker
   → management still opens
6. **Fix a changed wifi password** — change the router's wifi password, scan the sticker, enter the new password
   → the Pi is online again without a reboot
7. **New QR invalidates** — click "Nieuwe QR" with the Pi online, wait one manifest poll, scan the old sticker
   → the old one shows "QR verlopen"; the new one works
8. **Nothing leaks** — connect to `ZZ-<id4>` from nRF Connect and try to read/write the characteristics
   → no SSID, IP or network list readable; every write rejected
9. **Without the app + first setup** — scan the sticker on an iPhone without the app and an Android phone without the app; then set up an unpaired Pi from its TV QR on both phones
   → App Clip opens management / CMS `/setup` page with Play Store link; first-time setup works as before
