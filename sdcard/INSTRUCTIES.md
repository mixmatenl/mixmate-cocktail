# MIXMATE Cocktailmachine — SD-kaart installatie

## Wat je nodig hebt
- SD-kaart (min. 8 GB)
- Raspberry Pi Imager (gratis: https://www.raspberrypi.com/software/)
- Deze map (`sdcard/`)

---

## Stap 1 — Pi OS Lite flashen

1. Open **Raspberry Pi Imager**
2. Kies apparaat: **Raspberry Pi 4** of **Raspberry Pi 5**
3. Kies besturingssysteem: **Raspberry Pi OS Lite (64-bit)**  
   _(via "Overige" → "Raspberry Pi OS (overige)")_
4. Kies je SD-kaart
5. Klik op **Volgende**
6. Klik op **Instellingen bewerken** (of het tandwieltje)
   - Gebruikersnaam: `pi`
   - Wachtwoord: kies zelf (noteer dit!)
   - **Stel GEEN WiFi in** — dat doet het installatiescript
   - Sla op
7. Klik op **Schrijven** en wacht tot het klaar is
8. **Verwijder de SD-kaart NIET** — ga naar stap 2

---

## Stap 2 — Bestanden op SD-kaart zetten

Na het flashen verschijnt de SD-kaart als schijf genaamd **`bootfs`** op je computer.

Kopieer de volgende bestanden naar de **root** van die schijf:

| Wat | Waar naartoe |
|-----|-------------|
| `firstrun.sh` | `/` (root van bootfs) |
| `mixmate-cocktail/` (map) | `/` (root van bootfs) |

Zodat de structuur op de SD-kaart er zo uitziet:
```
bootfs/
├── firstrun.sh           ← dit bestand
├── mixmate-cocktail/
│   ├── loadcell_sender.py
│   ├── requirements.txt
│   └── mixmate-cocktail.service
├── cmdline.txt           ← al aanwezig, pas aan (zie stap 3)
└── config.txt            ← al aanwezig, niet aanpassen
```

---

## Stap 3 — cmdline.txt aanpassen

Open `cmdline.txt` op de SD-kaart in een teksteditor (Kladblok, TextEdit).

Voeg **aan het einde van de regel** (alles op één regel!) toe:
```
 systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
```

Voorbeeld van hoe de regel er daarna uitziet:
```
console=serial0,115200 console=tty1 root=PARTUUID=... rootfstype=ext4 fsck.repair=yes rootwait systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
```

Sla op en gooi de SD-kaart veilig uit.

---

## Stap 4 — Installeren

1. Zorg dat de **Pompmodule aan is** (die maakt de MIXMATE-SETUP hotspot aan)
2. Doe de SD-kaart in de Cocktailmachine Pi
3. Sluit stroom aan
4. **Wacht 3-5 minuten** — het installatiescript draait automatisch
5. De Pi herstart zichzelf als de installatie klaar is

**Klaar!** De Cocktailmachine verbindt automatisch met de Pompmodule.

---

## Installatie controleren

Het installatiescript schrijft een logbestand naar de SD-kaart:  
`bootfs/mixmate-install.log`

Open dit bestand op je computer om te zien of de installatie is geslaagd.  
Het eindigt met: `MIXMATE Installatie geslaagd`

---

## Problemen?

- **Hotspot niet gevonden**: Zorg dat de Pompmodule aan is en in `factory` state staat
- **Packages mislukken**: Controleer of de Pompmodule internetverbinding heeft (ethernet)
- **Pi start niet**: Controleer of `cmdline.txt` nog op één regel staat (geen enters!)
