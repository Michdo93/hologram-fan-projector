"""
hologram_fan.py — Python-Schnittstelle für 3D-Hologramm-Ventilator
====================================================================
Protokoll: TCP auf Port 50200 (binäre Befehle)
Voraussetzung: Gleiches WLAN wie das Gerät ODER direkt mit dem
               WLAN-Hotspot des Geräts verbunden (IP: 10.10.10.1)

Drittanbieter-Steuerung muss in den Geräteeinstellungen aktiviert
und das Gerät danach neu gestartet werden!

Dateien hochladen: Per FTP oder HTTP auf Port 8080 (je nach Firmware).
Wenn dein Gerät eine andere Methode nutzt, tracke den Traffic mit
Wireshark während du die offizielle App verwendest.

Unbekannte Befehle? → Wireshark auf deinem PC laufen lassen,
die offizielle App benutzen, und die TCP-Pakete an Port 50200 ablesen.
"""

import socket
import time
import os
import ftplib
import threading
from pathlib import Path


# ─────────────────────────────────────────────
#  Bekannte Befehle (0x5B = Startbyte)
#  Ergänze hier weitere, wenn du sie per
#  Wireshark aus der App herausliest!
# ─────────────────────────────────────────────
COMMANDS = {
    "power_on":     bytes([0x5B, 0x01, 0x00]),
    "power_off":    bytes([0x5B, 0x02, 0x00]),
    "stop":         bytes([0x5B, 0x03, 0x00]),
    "play":         bytes([0x5B, 0x04, 0x00]),
    "pause":        bytes([0x5B, 0x05, 0x00]),
    "next":         bytes([0x5B, 0x06, 0x00]),
    "previous":     bytes([0x5B, 0x07, 0x00]),
    "volume_up":    bytes([0x5B, 0x08, 0x00]),
    "volume_down":  bytes([0x5B, 0x09, 0x00]),
    # Geschwindigkeit — viele Geräte erwarten den Wert im 3. Byte
    # Typischer Bereich: 0x01 (langsam) bis 0x05 (schnell)
    # Diese müssen ggf. per Wireshark verifiziert werden:
    "speed_1":      bytes([0x5B, 0x0A, 0x01]),
    "speed_2":      bytes([0x5B, 0x0A, 0x02]),
    "speed_3":      bytes([0x5B, 0x0A, 0x03]),
    "speed_4":      bytes([0x5B, 0x0A, 0x04]),
    "speed_5":      bytes([0x5B, 0x0A, 0x05]),
    # Helligkeit (falls unterstützt)
    "brightness_low":  bytes([0x5B, 0x0B, 0x01]),
    "brightness_mid":  bytes([0x5B, 0x0B, 0x02]),
    "brightness_high": bytes([0x5B, 0x0B, 0x03]),
}


class HologramFan:
    """
    Steuert einen WiFi-Hologramm-Ventilator über TCP.

    Schnellstart:
        fan = HologramFan("10.10.10.1")   # Hotspot-Modus
        # oder
        fan = HologramFan("192.168.1.42") # Router-Modus (IP aus Router-DHCP ablesen)

        fan.connect()
        fan.play()
        fan.set_speed(3)
        fan.stop()
        fan.disconnect()

    Als Context-Manager:
        with HologramFan("10.10.10.1") as fan:
            fan.play()
            time.sleep(10)
            fan.next_video()
    """

    CONTROL_PORT = 50200
    FTP_PORT = 21          # Falls FTP-Upload unterstützt wird
    HTTP_PORT = 8080       # Falls HTTP-Upload unterstützt wird

    def __init__(self, host: str, port: int = CONTROL_PORT, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._connected = False

    # ── Verbindung ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Verbindet zum Gerät. Gibt True zurück bei Erfolg."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))
            self._connected = True
            print(f"✅  Verbunden mit {self.host}:{self.port}")
            return True
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f"❌  Verbindung fehlgeschlagen: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Trennt die Verbindung."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False
        print("🔌  Verbindung getrennt.")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ── Rohe Befehlsübertragung ─────────────────────────────────────

    def send_raw(self, data: bytes) -> bytes | None:
        """Sendet rohe Bytes und gibt die Antwort zurück (falls vorhanden)."""
        if not self._connected or not self._sock:
            print("⚠️   Nicht verbunden. Bitte zuerst connect() aufrufen.")
            return None
        try:
            self._sock.sendall(data)
            # Kurz auf Antwort warten (nicht alle Geräte antworten)
            try:
                response = self._sock.recv(256)
                return response
            except socket.timeout:
                return b""  # Kein Fehler — Gerät sendet nichts zurück
        except OSError as e:
            print(f"❌  Sendefehler: {e}")
            self._connected = False
            return None

    def send_command(self, name: str) -> bool:
        """Sendet einen benannten Befehl aus der COMMANDS-Tabelle."""
        cmd = COMMANDS.get(name)
        if cmd is None:
            print(f"⚠️   Unbekannter Befehl: '{name}'")
            print(f"     Verfügbare Befehle: {list(COMMANDS.keys())}")
            return False
        resp = self.send_raw(cmd)
        if resp is not None:
            print(f"▶   {name}: gesendet {cmd.hex(' ')} → Antwort: {resp.hex(' ') or '(keine)'}")
            return True
        return False

    # ── Komfortmethoden ─────────────────────────────────────────────

    def power_on(self):
        return self.send_command("power_on")

    def power_off(self):
        return self.send_command("power_off")

    def play(self):
        return self.send_command("play")

    def stop(self):
        return self.send_command("stop")

    def pause(self):
        return self.send_command("pause")

    def next_video(self):
        return self.send_command("next")

    def previous_video(self):
        return self.send_command("previous")

    def set_speed(self, level: int):
        """Setzt die Rotationsgeschwindigkeit. level: 1 (langsam) bis 5 (schnell)."""
        if not 1 <= level <= 5:
            print("⚠️   Geschwindigkeit muss zwischen 1 und 5 liegen.")
            return False
        return self.send_command(f"speed_{level}")

    def set_brightness(self, level: str):
        """Setzt die Helligkeit. level: 'low', 'mid', 'high'."""
        key = f"brightness_{level}"
        if key not in COMMANDS:
            print(f"⚠️   Ungültige Helligkeit: '{level}'. Wähle 'low', 'mid' oder 'high'.")
            return False
        return self.send_command(key)

    def play_file_by_index(self, index: int):
        """
        Spielt eine Datei anhand ihres Index auf der SD-Karte ab.
        Das 3. Byte ist der Dateiindex (0-basiert).
        ⚠️  Diese Methode muss ggf. per Wireshark verifiziert werden!
        """
        cmd = bytes([0x5B, 0x10, index & 0xFF])
        resp = self.send_raw(cmd)
        print(f"▶   play_index({index}): {cmd.hex(' ')} → {resp.hex(' ') if resp else '(keine Antwort)'}")

    # ── Datei-Upload (FTP-basiert) ───────────────────────────────────

    def upload_file_ftp(self, local_path: str, remote_name: str | None = None) -> bool:
        """
        Lädt eine Datei per FTP auf das Gerät hoch.
        Funktioniert nur, wenn dein Gerät FTP unterstützt.
        remote_name: Dateiname auf dem Gerät (Standard: gleicher Name wie lokal)

        Tipp: Dateiformat .bin wird oft benötigt — konvertiere MP4/AVI
              zuerst mit der mitgelieferten PC-Software in .bin.
        """
        path = Path(local_path)
        if not path.exists():
            print(f"❌  Datei nicht gefunden: {local_path}")
            return False

        remote = remote_name or path.name
        print(f"📤  FTP-Upload: {path.name} → {self.host}/{remote} ...")

        try:
            with ftplib.FTP() as ftp:
                ftp.connect(self.host, self.FTP_PORT, timeout=int(self.timeout))
                ftp.login()  # Anonym; ggf. user/passwort ergänzen
                with open(path, "rb") as f:
                    ftp.storbinary(f"STOR {remote}", f)
            print(f"✅  Upload abgeschlossen: {remote}")
            return True
        except ftplib.all_errors as e:
            print(f"❌  FTP-Fehler: {e}")
            print("    → Prüfe ob dein Gerät FTP unterstützt oder nutze die offizielle App.")
            return False

    def list_files_ftp(self) -> list[str]:
        """Listet alle Dateien auf dem Gerät (FTP)."""
        try:
            with ftplib.FTP() as ftp:
                ftp.connect(self.host, self.FTP_PORT, timeout=int(self.timeout))
                ftp.login()
                files = ftp.nlst()
                print("📂  Dateien auf dem Gerät:")
                for f in files:
                    print(f"    {f}")
                return files
        except ftplib.all_errors as e:
            print(f"❌  FTP-Fehler: {e}")
            return []

    # ── Geräte-Discovery ────────────────────────────────────────────

    @staticmethod
    def discover(subnet: str = "192.168.1", port: int = CONTROL_PORT,
                 timeout: float = 0.5) -> list[str]:
        """
        Sucht Hologramm-Ventilatoren im lokalen Netzwerk.
        Probiert alle 254 Adressen im Subnetz (z.B. '192.168.1').

        Beispiel:
            gefundene = HologramFan.discover("192.168.178")
        """
        print(f"🔍  Suche Geräte in {subnet}.0/24 auf Port {port} ...")
        found = []
        lock = threading.Lock()

        def try_connect(ip: str):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
                result = s.connect_ex((ip, port))
                s.close()
                if result == 0:
                    with lock:
                        found.append(ip)
                        print(f"    ✅  Gerät gefunden: {ip}")
            except OSError:
                pass

        threads = [threading.Thread(target=try_connect, args=(f"{subnet}.{i}",))
                   for i in range(1, 255)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if not found:
            print("    ❌  Kein Gerät gefunden. Prüfe WLAN-Verbindung und IP-Subnetz.")
        return found


# ─────────────────────────────────────────────────────────────────
#  Interaktive Demo / Kommandozeile
# ─────────────────────────────────────────────────────────────────

def interactive_cli(fan: HologramFan):
    """Einfaches interaktives Terminal zur Steuerung."""
    print("\n" + "="*50)
    print("  Hologramm-Ventilator Steuerung")
    print("="*50)
    print("Befehle: on, off, play, stop, pause, next, prev,")
    print("         speed <1-5>, bright <low/mid/high>,")
    print("         index <n>, raw <hex z.B. 5B 01 00>,")
    print("         upload <dateipfad>, list, quit")
    print("="*50)

    while True:
        try:
            cmd = input("\n> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "on":
            fan.power_on()
        elif cmd == "off":
            fan.power_off()
        elif cmd == "play":
            fan.play()
        elif cmd == "stop":
            fan.stop()
        elif cmd == "pause":
            fan.pause()
        elif cmd == "next":
            fan.next_video()
        elif cmd in ("prev", "previous"):
            fan.previous_video()
        elif cmd.startswith("speed "):
            try:
                fan.set_speed(int(cmd.split()[1]))
            except (ValueError, IndexError):
                print("⚠️   Syntax: speed <1-5>")
        elif cmd.startswith("bright "):
            fan.set_brightness(cmd.split()[1])
        elif cmd.startswith("index "):
            try:
                fan.play_file_by_index(int(cmd.split()[1]))
            except (ValueError, IndexError):
                print("⚠️   Syntax: index <nummer>")
        elif cmd.startswith("raw "):
            try:
                raw = bytes.fromhex(cmd[4:].replace(" ", ""))
                fan.send_raw(raw)
            except ValueError:
                print("⚠️   Ungültiger Hex-String. Beispiel: raw 5B 01 00")
        elif cmd.startswith("upload "):
            fan.upload_file_ftp(cmd[7:].strip())
        elif cmd == "list":
            fan.list_files_ftp()
        elif cmd == "":
            pass
        else:
            print(f"⚠️   Unbekannter Befehl: '{cmd}'")


# ─────────────────────────────────────────────────────────────────
#  Hauptprogramm
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Hologramm-Ventilator Python-Steuerung"
    )
    parser.add_argument(
        "--host",
        default="10.10.10.1",
        help="IP-Adresse des Geräts (Standard: 10.10.10.1 im Hotspot-Modus)"
    )
    parser.add_argument(
        "--discover",
        metavar="SUBNETZ",
        help="Geräte suchen, z.B. --discover 192.168.178"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=50200,
        help="TCP-Port (Standard: 50200)"
    )
    args = parser.parse_args()

    if args.discover:
        found = HologramFan.discover(args.discover)
        if found:
            print(f"\nGefundene Geräte: {found}")
            print(f"Tipp: Starte mit --host {found[0]}")
    else:
        fan = HologramFan(args.host, args.port)
        if fan.connect():
            interactive_cli(fan)
            fan.disconnect()
        else:
            print("\n💡 Tipps:")
            print("  1. Verbinde deinen PC mit dem WLAN-Hotspot des Geräts.")
            print("     Dann ist die IP automatisch 10.10.10.1")
            print("  2. Oder verbinde Gerät + PC im selben Heimnetz und")
            print("     lies die IP aus deinem Router-DHCP ab.")
            print("  3. Aktiviere 'Third-Party Control' in den Geräteeinstellungen!")
            print(f"  4. Suche mit: python hologram_fan.py --discover 192.168.178")
