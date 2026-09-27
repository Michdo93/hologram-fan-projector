# Hologram Fan Projector – Python-Steuerung

Python-Client zur Steuerung eines 3D-Hologramm-Lüfters (POV-/LED-Blade-Display)
der Bauart **`3D_42CM_…`**, wie er mit der Windows-App *„电脑软件 V13.0 / Windows
App V13.0"* ausgeliefert wird. Das Protokoll wurde per Reverse-Engineering des
Original-Binaries und aus Wireshark-Mitschnitten rekonstruiert; alle Kommandos
sind byte-genau gegen echten Traffic verifiziert.

Keine externen Abhängigkeiten – nur die Python-Standardbibliothek.

## Voraussetzungen

- Python 3.8 oder neuer
- Ein WLAN-Adapter, um sich mit dem Access Point des Geräts zu verbinden
- Das Skript `hologram_fan.py`

## Verbindung herstellen

Der Lüfter spannt ein eigenes WLAN auf (SSID z. B. `3D_42CM_51ABDC`). Im
AP-Modus ist das Gerät ein TCP-Server:

| Parameter | Wert |
|-----------|------|
| SSID | `3D_42CM_…` (gerätespezifisch) |
| IP | `192.168.4.1` |
| Port | `20320` |

1. Am Rechner mit dem WLAN `3D_42CM_…` verbinden.
2. Prüfen, ob die Verbindung steht:

```
python hologram_fan.py list
```

Erwartete Ausgabe (die 12 Animationen auf der SD-Karte):

```
Verbunden mit 192.168.4.1:20320 — 12 Animationen
  [0] 0鱼
  [1] 1狐狸
  ...
```

Kommt diese Liste, ist alles korrekt verbunden. Ohne Geräteverbindung lässt sich
das Framing offline prüfen mit `python hologram_fan.py selftest`.

## Bedienung

Das Gerät hält im Original eine **dauerhafte, „warme" Verbindung** und pollt
ständig; ein einzeln gesendetes Kommando auf einer sofort wieder geschlossenen
Verbindung wird ignoriert. Der Client bildet dieses Verhalten nach: jede
Kommando-Ausführung öffnet eine kurze warme Sitzung (Handshake + Keepalive) und
sendet das Kommando mehrfach.

### Interaktiver Modus (empfohlen)

Am zuverlässigsten ist die interaktive Shell, weil sie die Verbindung dauerhaft
warm hält:

```
python hologram_fan.py shell
```

Danach Befehle direkt eintippen:

```
fan> on-off
fan> next
fan> clock on
fan> dial zodiac
fan> time 12:30
fan> duration 15
fan> quit
```

### Einzelbefehle

Jeder Befehl kann auch direkt aufgerufen werden:

```
python hologram_fan.py on-off
python hologram_fan.py next
python hologram_fan.py clock on
python hologram_fan.py dial zodiac
python hologram_fan.py set-time 12:30
python hologram_fan.py duration 15
```

### Befehlsübersicht

Alle folgenden Funktionen sind per Mitschnitt verifiziert.

| Befehl | Wirkung | Hinweis |
|--------|---------|---------|
| `on-off` | Projektor an/aus | Toggle (ein Button, kein separates on/off) |
| `play-pause` | Video abspielen/pausieren | Toggle |
| `next` | Nächstes Video | |
| `prev` | Vorheriges Video | |
| `list-loop` | Alle Videos in Schleife | |
| `single-loop` | Einzelnes Video in Schleife | |
| `bright-up` | Helligkeit + | |
| `bright-down` | Helligkeit − | |
| `cw` | Drehung im Uhrzeigersinn | |
| `ccw` | Drehung gegen Uhrzeigersinn | |
| `clock on` / `clock off` | Uhr ein-/ausblenden | |
| `needle white` / `needle black` | Zeigerfarbe | |
| `dial <modus>` | Zifferblatt: `digital`, `symbol`, `constellation`, `zodiac` | |
| `set-time HH:MM` | Uhrzeit der Uhr setzen | auch `HH:MM:SS` |
| `duration <5–30>` | Anzeigedauer je Video in Sekunden | wird auf 5–30 begrenzt |

**Toggle vs. Auswahl:** `on-off` und `play-pause` sind Umschalter – es gibt
bewusst kein getrenntes „on"/„off", weil die App dafür nur einen einzigen Button
sendet. Nur `clock`, `needle` und `dial` haben echte Zustandswerte und erwarten
deshalb ein Argument.

### Diagnose

| Befehl | Zweck |
|--------|-------|
| `list` | Animationsliste lesen |
| `selftest` | Framing/Parser offline prüfen |
| `button <x>` | Einen rohen 1-Byte-Button senden |
| `raw <hex>` | Beliebige Payload rahmen und senden |

Globale Option `-v` / `--verbose` zeigt jeden gesendeten (`TX`) und empfangenen
(`LISTE`/`RX`) Frame – nützlich, wenn etwas nicht reagiert:

```
python hologram_fan.py -v shell
```

## Konfiguration

Globale Optionen (vor dem Befehl):

| Option | Standard | Beschreibung |
|--------|----------|--------------|
| `--ip` | `192.168.4.1` | IP des Geräts |
| `--port` | `20320` | TCP-Port |
| `--timeout` | `3.0` | Socket-Timeout in Sekunden |
| `-v`, `--verbose` | aus | Frames mitschreiben |

Beispiel:

```
python hologram_fan.py --ip 192.168.4.1 --port 20320 -v duration 20
```

## Als Bibliothek verwenden

```python
from hologram_fan import HologramFan

with HologramFan() as fan:          # öffnet eine warme Sitzung
    files, status = fan.get_file_list()
    for f in files:
        print(f)                    # z. B. "[4] 4TIGER"

    fan.on_off()
    fan.next_one()
    fan.clock(True)                 # Uhr an
    fan.needle_color(white=False)   # Zeiger schwarz
    fan.dial("zodiac")
    fan.set_clock_time(12, 30)      # 12:30:00
    fan.set_duration(15)            # 15 s je Video
```

Ohne Kontextmanager öffnet jeder Kommando-Aufruf selbst kurz eine warme Sitzung;
für viele Kommandos in Folge ist der Kontextmanager (oder `fan.open()` …
`fan.close()`) effizienter.

## Protokoll (Kurzreferenz)

- **Transport:** TCP, Gerät ist AP-Server auf `192.168.4.1:20320`.
- **Rahmen:** jedes Kommando ist `HEAD + check3 + payload + FOOT` als
  ASCII-Bytes, mit
  - `HEAD = "C0EEB7C9BAA3"`
  - `FOOT = "C0EEBDF9E5B7"`
  - `check3` = drei Prüfbytes, die nur von der Payload-Länge abhängen
    (`len 1 → 00 63 63`, `2 → 00 63 64`, `5 → 00 63 67`).
- **Handshake:** nur `HEAD+FOOT` senden → Gerät liefert die Datei-/Animationsliste.
  Das Gerät sendet diese Liste außerdem periodisch von selbst.
- **Kommandoformen:**
  - 1-Byte-Buttons: `a` On/Off, `c` Next, `d` Prev, `e` Play/Pause, `g`
    Single-Loop, `h` List-Loop, `l` Helligkeit−, `m` Helligkeit+, `p` CW, `q` CCW.
  - Videodauer: `C` + 1 Byte Sekunden (5–30).
  - Einstellungen: `b` + `[wert, ctx, ctx, id]` mit `id` 2=Uhr, 3=Zeiger,
    4=Zifferblatt.
  - Uhrzeit: `b` + Sekunden-seit-Mitternacht (24-bit little-endian) + `id 1`.

## Bekannte Grenzen

- **BIN-Upload / „Decode video"** sind noch nicht implementiert – dafür fehlt der
  Mitschnitt einer erfolgreichen Übertragung.
- **`Format Disk` (Button `j`) und `Clear Cache` (Button `k`)** sind aus dem
  Binary bekannt, aber **nicht per Mitschnitt verifiziert**. Sie sind bewusst
  nicht als reguläre Befehle eingebunden. `Format Disk` löscht die SD-Karte –
  nur bewusst über `button j` verwenden.
- `set-time` ist für die getesteten Uhrzeiten verifiziert; die 24-Bit-Kodierung
  deckt den vollen Tagesbereich ab.
- Die App tolerierte je nach Firmware nur eine aktive Verbindung. Läuft parallel
  die Original-Windows-App, sollte sie geschlossen sein.

## Fehlerbehebung

- **„Verbindung fehlgeschlagen":** WLAN `3D_42CM_…` verbunden? IP `192.168.4.1`
  erreichbar? Original-App geschlossen?
- **Befehl wird angenommen, aber am Gerät passiert nichts:** mit `-v` prüfen, ob
  `LISTE`-Zeilen ankommen (Sitzung lebt) und `TX`-Zeilen rausgehen. Der
  `shell`-Modus hält die Sitzung am zuverlässigsten warm.
- **`list` zeigt mal 12, mal 13 Einträge:** kosmetisch – der Status-Trailer wird
  je nach Timing als zusätzlicher Eintrag gelesen; die 12 Namen stimmen immer.
