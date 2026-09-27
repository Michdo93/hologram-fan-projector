#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hologram_fan.py — Python-Steuerung fuer 3D-Hologramm-Luefter (LED-Blade / POV Fan)
der Bauart "3D_42CM_..." (Windows-App "电脑软件 V13.0" / "Windows app V13.0").

Reverse-engineered aus dem originalen Windows-Binary. Gesicherter Stand:

  * Transport : TCP, Geraet ist AP-Server auf 192.168.4.1:20320  (Port 0x4F60)
  * Framing   : ASCII-Rahmen HEAD ... FOOT
                  HEAD = b"C0EEB7C9BAA3"   (GBK-Hex von 李飞海 = Autor "Li Feihai")
                  FOOT = b"C0EEBDF9E5B7"   (GBK-Hex von 李靳宸)
                Zweite Kommando-Familie (Datei/Upload) nutzt Praefix
                  b"B2DDDDED"              (GBK-Hex von 草蓓)
  * Handshake : nur HEAD+FOOT-Signatur senden -> Geraet liefert Datei-/Animationsliste.

Der Listen-Parser ist gegen einen echten Geraete-Mitschnitt verifiziert
(siehe test_parse() am Ende).

Autor-Kontext: passt in die Geraete-Repo-Reihe (BenQ-RS232-TCP,
Brother-VC-500W, LogiLink-WC0030A-Python ...).
"""

from __future__ import annotations
import socket
import threading as _threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_IP   = "192.168.4.1"
DEFAULT_PORT = 20320

HEAD = b"C0EEB7C9BAA3"          # 李飞海
FOOT = b"C0EEBDF9E5B7"          # 李靳宸
FILE_PREFIX = b"B2DDDDED"       # 草蓓  (Datei-/Upload-Familie)

RECV_MAX = 1460                 # recv-Puffer der Original-App (0x5B4)


@dataclass
class FanFile:
    """Ein Eintrag der Animations-/Videoliste auf der SD-Karte."""
    name: str                   # GBK-dekodierter Dateiname, z.B. "0鱼", "4TIGER"
    index: Optional[int]        # fuehrende Ziffer im Namen, falls vorhanden (Play-Index)
    raw: bytes                  # Rohbytes (GBK) des Namens

    def __str__(self) -> str:
        idx = "" if self.index is None else f"[{self.index}] "
        return f"{idx}{self.name}"


@dataclass
class FanStatus:
    """Aus dem Status-Trailer der Listenantwort."""
    file_count: int
    flags: bytes = field(default=b"")


class HologramFan:
    """
    Minimaler, blockierender TCP-Client.

    Verwendung:
        fan = HologramFan()          # 192.168.4.1:20320
        fan.connect()
        files, status = fan.get_file_list()
        for f in files:
            print(f)
        fan.close()

    oder als Kontext-Manager:
        with HologramFan() as fan:
            print(fan.get_file_list()[0])
    """

    def __init__(self, ip: str = DEFAULT_IP, port: int = DEFAULT_PORT,
                 timeout: float = 3.0, verbose: bool = False):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self._sock: Optional[socket.socket] = None
        self._lock = _threading.Lock()
        self._running = False
        self._rx_thread: Optional[_threading.Thread] = None
        self._ka_thread: Optional[_threading.Thread] = None
        self._last_rx = b""

    # ---- Verbindung / warme Sitzung ------------------------------------
    # Erkenntnis aus den Einzelmitschnitten: das Geraet sendet die Liste
    # von SELBST periodisch (~alle 10 s) und antwortet NICHT direkt auf
    # jedes Kommando. Die App feuert Kommandos fire-and-forget und haelt
    # die Verbindung mit staendigem Polling warm. Genau das macht open():
    # eine dauerhafte Verbindung + Reader-Thread (draint eingehende Daten)
    # + Keepalive-Thread (pollt regelmaessig mit HEAD+FOOT).
    def connect(self) -> None:
        self.close()
        self._sock = socket.create_connection((self.ip, self.port), self.timeout)

    def open(self) -> "HologramFan":
        """Warme Sitzung starten (Verbindung + Reader + Keepalive)."""
        self.connect()
        self._running = True
        self._rx_thread = _threading.Thread(target=self._rx_loop, daemon=True)
        self._ka_thread = _threading.Thread(target=self._ka_loop, daemon=True)
        self._rx_thread.start()
        self._ka_thread.start()
        self._raw(HEAD + FOOT)          # initialer Handshake
        time.sleep(0.4)
        return self

    def close(self) -> None:
        self._running = False
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def __enter__(self) -> "HologramFan":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _raw(self, data: bytes) -> None:
        with self._lock:
            s = self._sock
            if s is not None:
                s.sendall(data)
        if self.verbose:
            print(f"    TX {data[len(HEAD):data.rfind(FOOT)].hex(' ') if HEAD in data else data.hex(' ')}")

    def _rx_loop(self) -> None:
        while self._running:
            s = self._sock
            if s is None:
                break
            try:
                s.settimeout(0.5)
                d = s.recv(RECV_MAX)
                if not d:
                    break
                self._last_rx = d
                if self.verbose and HEAD in d:
                    inner = d[d.find(HEAD) + len(HEAD):d.rfind(FOOT)]
                    kind = "LISTE" if inner[:4] == b"\x00gpi" else "RX"
                    print(f"    {kind} {inner[:16].hex(' ')}")
            except socket.timeout:
                continue
            except OSError:
                break

    def _ka_loop(self) -> None:
        while self._running:
            time.sleep(1.2)
            try:
                self._raw(HEAD + FOOT)
            except OSError:
                break

    # ---- Kommando ausfuehren -------------------------------------------
    def command(self, payload: bytes, read: bool = True) -> bytes:
        """
        Kommando senden. Laeuft eine warme Sitzung, wird nur gesendet
        (fire-and-forget, wie die App). Sonst wird fuer diesen einen Aufruf
        kurz eine warme Sitzung geoeffnet, das Kommando (mehrfach) gesendet
        und die Verbindung nach kurzem Nachlauf geschlossen.
        """
        frame = self.frame(payload)
        if self._running:
            self._raw(frame)
            time.sleep(0.15)
            return self._last_rx
        # Einzelaufruf: temporaere warme Sitzung
        self.open()
        try:
            for _ in range(3):          # App sendet meist mehrfach
                self._raw(frame)
                time.sleep(0.2)
            time.sleep(1.2)             # Nachlauf, Keepalive haelt Sitzung
        finally:
            self.close()
        return self._last_rx

    def send_raw(self, payload: bytes) -> None:
        self._raw(payload)

    def request(self, payload: bytes, max_bytes: int = RECV_MAX) -> bytes:
        """Kommando in warmer Sitzung senden und letzte Geraeteantwort lesen."""
        if self._running:
            self._raw(payload)
            time.sleep(0.3)
            return self._last_rx
        self.open()
        try:
            self._raw(payload)
            time.sleep(0.6)
            return self._last_rx
        finally:
            self.close()

    # ---- Kommandos ------------------------------------------------------
    def get_file_list(self) -> tuple[List[FanFile], Optional[FanStatus]]:
        """Liste holen. Nutzt warme Sitzung; das Geraet pusht die Liste."""
        resp = self.request(HEAD + FOOT)
        return parse_file_list(resp)

    # ---- Kommando-Framing (vollstaendig aus dem Binary rekonstruiert) ---
    # Jedes Steuerkommando der Original-App hat die Form
    #     HEAD + check3(len) + payload + FOOT
    # check3 haengt NUR von der payload-Laenge ab (bit-genau nachgebildet,
    # siehe _check3). Der Handshake get_file_list() ist der Sonderfall
    # payload="" (HEAD+FOOT).
    #
    # Aus dem Binary extrahiertes Kommando-Vokabular (Semantik teils noch
    # empirisch zu bestaetigen, Mechanik gesichert):
    #   1-Byte  : Buchstaben  a c d e h l g m p q j k r
    #   2-Byte  : {'A'|'B', wert} und {'C', wert}
    #   5-Byte  : 'b' + int32(little-endian)   (Zeit-/Range-Einstellungen)

    @staticmethod
    def _check3(length: int) -> bytes:
        """3 Pruefbytes = f(payload-Laenge). Nachbau der Compiler-Arithmetik."""
        def s32(x):
            x &= 0xFFFFFFFF
            return x - 0x100000000 if x & 0x80000000 else x
        def imul_hi(a, b):
            return s32((s32(a) * s32(b) >> 32) & 0xFFFFFFFF)
        n = s32(length)
        q1 = s32(((imul_hi(n, 0x06572EC3) >> 3) +
                  ((imul_hi(n, 0x06572EC3) >> 3) >> 31 & 1)) & 0xFFFFFFFF)
        q2 = s32(((imul_hi(n, 0x78787879) >> 3) +
                  ((imul_hi(n, 0x78787879) >> 3) >> 31 & 1)) & 0xFFFFFFFF)
        b0 = q1 & 0xFF
        b1 = (int(q2 - int(q2 / 19) * 19) + 0x63) & 0xFF
        rem = s32(n - q1 * 323)
        b2 = (int(rem - int(rem / 17) * 17) + 0x62) & 0xFF
        return bytes([b0, b1, b2])

    def frame(self, payload: bytes) -> bytes:
        """Vollstaendigen Kommando-Rahmen bauen."""
        return HEAD + self._check3(len(payload)) + payload + FOOT

    # --- getippte Kommando-Formen (aus Wireshark-Mitschnitt verifiziert) -
    def button(self, letter: str, read: bool = True) -> bytes:
        """
        1-Byte-Button-Kommando. Verifizierte Buchstaben (Message-Map +
        Einzelmitschnitte): a c d e g h l m p q  (Binary kennt auch j k).
        Antwort wird gelesen, damit die Sitzung gesund bleibt.
        """
        assert len(letter) == 1
        return self.command(letter.encode("ascii"), read)

    def set_duration(self, seconds: int, read: bool = False) -> bytes:
        """
        Videodauer setzen (5..30 s). VERIFIZIERT: 'C' + <sekunden-byte>.
        (Mitschnitt: 'C' 0x14 -> 20 s.)
        """
        s = max(5, min(30, int(seconds)))
        return self.command(b"C" + bytes([s]), read)

    def set_param(self, value: int, sub: int, pid: int,
                  read: bool = False) -> bytes:
        """
        5-Byte-Parameter-Setter: 'b' + [lo, hi, sub, id].
        Aus dem Mitschnitt:
            id=0            -> Zeit/Position-Poll (Heartbeat, lo|hi<<8 = Sekunden)
            id in {1,2,3,4} -> Einstellungen (Uhr/Zeiger/Ziffern/Uhrzeit)
        value wird als 16-bit little-endian in lo/hi geschrieben.
        Exakte Feldsemantik je id noch zu kartieren.
        """
        v = int(value) & 0xFFFF
        payload = bytes([ord("b"), v & 0xFF, (v >> 8) & 0xFF,
                         sub & 0xFF, pid & 0xFF])
        return self.command(payload, read)

    # --- Datei-/BIN-Upload (Familie 草蓓) --------------------------------
    def upload_begin(self, read: bool = True) -> bytes:
        """
        Upload-Handshake der Datei-Familie: PREFIX + FOOT (ohne Nutzdaten).
        Genau das sendet die Original-App zu Beginn eines Uploads
        (im Mitschnitt 2x, danach ohne Datenfluss abgebrochen).
        """
        return self.request(FILE_PREFIX + FOOT)

    def build_file_frame(self, data: bytes) -> bytes:
        """
        Datenrahmen der Upload-Familie: PREFIX + HEAD + check3 + data + FOOT.
        (Datenphase noch nicht mitgeschnitten -> gegen Geraet zu verifizieren.)
        """
        return FILE_PREFIX + self.frame(data)

    # === Benannte Funktionen ============================================
    # Alle folgenden Zuordnungen sind in den Einzel-Mitschnitten byte-genau
    # bestaetigt (je eine .pcapng pro Funktion). Buchstabe -> Aktion aus der
    # MFC-Message-Map des Binaries.
    #
    # Sichere 1-Byte-Buttons (in probe verwendet):
    SAFE_LETTERS = "acdeghlmpq"

    # --- Wiedergabe & Navigation ---
    def on_off(self):          return self.button("a")   # Toggle An/Aus
    def play_pause(self):      return self.button("e")   # Toggle Play/Pause
    def next_one(self):        return self.button("c")   # naechstes Video
    def last_one(self):        return self.button("d")   # vorheriges Video
    def single_loop(self):     return self.button("g")   # Einzelwiederholung
    def list_loop(self):       return self.button("h")   # Listenwiederholung
    def brightness_up(self):   return self.button("m")   # Helligkeit +
    def brightness_down(self): return self.button("l")   # Helligkeit -
    def cw_adjust(self):       return self.button("p")   # Drehung im Uhrzeigersinn
    def ccw_adjust(self):      return self.button("q")   # Drehung gegen Uhrzeigersinn

    # --- GEFAEHRLICH: im Binary vorhanden, NICHT per Mitschnitt getestet ---
    # Loeschen Daten auf der SD. Nur bewusst verwenden.
    def format_disk_DANGER(self):  return self.button("j")   # SD formatieren
    def clear_cache_DANGER(self):  return self.button("k")   # Cache leeren

    # 'b'-Toggles: 'b' + [wert, ctx1, ctx2, id]. wert/id aus den Handlern,
    # in den Einzel-Mitschnitten bestaetigt. ctx1/ctx2 sind Reste des
    # Uptime-Zaehlers der App; das Geraet wertet fuer diese Kommandos nur
    # wert+id aus (ctx2 war stets 0x01). Falls ein Geraet zickt, ctx1 auf
    # den zuletzt empfangenen Uptime-Wert setzen.
    CTX1 = 0x00
    CTX2 = 0x01

    def _btoggle(self, val: int, pid: int, read: bool = True) -> bytes:
        payload = bytes([ord("b"), val & 0xFF, self.CTX1, self.CTX2, pid & 0xFF])
        return self.command(payload, read)

    def clock(self, on: bool):
        """Uhr einschalten (on=True) oder ausschalten (id=2). on=1, off=0."""
        return self._btoggle(1 if on else 0, 2)

    def needle_color(self, white: bool):
        """Zeigerfarbe weiss (True) oder schwarz (id=3). white=1, black=0."""
        return self._btoggle(1 if white else 0, 3)

    DIAL_MODES = {"digital": 0, "symbol": 1, "constellation": 2, "zodiac": 3}

    def dial(self, mode: str):
        """Zifferblatt: digital | symbol | constellation | zodiac (id=4)."""
        return self._btoggle(self.DIAL_MODES[mode.lower()], 4)

    def set_clock_time(self, hour: int, minute: int, second: int = 0):
        """
        Uhrzeit der Uhr setzen. Verifiziert: 'b' + Sekunden-seit-Mitternacht
        (24-bit little-endian) + id=1.
        (Mitschnitt: 07:34:00 -> 68 6a 00, 08:00:00 -> 80 70 00.)
        """
        secs = (int(hour) * 3600 + int(minute) * 60 + int(second)) % 86400
        payload = bytes([ord("b"), secs & 0xFF, (secs >> 8) & 0xFF,
                         (secs >> 16) & 0xFF, 0x01])
        return self.command(payload)

    # set_duration(seconds) = 'C' + n  (oben, verifiziert)

    def probe_letters(self, letters: Optional[str] = None,
                      delay: float = 1.0) -> None:
        """Sichere Button-Buchstaben nacheinander senden (ohne j/k!)."""
        for c in (letters or self.SAFE_LETTERS):
            self.button(c)
            print(f"  gesendet: '{c}'  ({self.frame(c.encode()).hex(' ')})")
            time.sleep(delay)


# ---- Parser (standalone, ohne Socket) ----------------------------------
def parse_file_list(resp: bytes) -> tuple[List[FanFile], Optional[FanStatus]]:
    """
    Zerlegt eine Listenantwort.

    Struktur (verifiziert):
      HEAD
      0x00 'gpi'                      (4-Byte-Tag)
      N x  [len:1][name: len Bytes GBK]   (Dateinamen, evtl. mit fuehrender Ziffer)
      [len:1][0x?? 00 00 00 fl fl fl]     Status-Trailer: erstes Byte = Dateianzahl
      00-Padding
      FOOT
    """
    if not (resp.startswith(HEAD) and FOOT in resp):
        raise ValueError("keine gueltige HEAD/FOOT-Antwort")
    body = resp[len(HEAD):resp.index(FOOT)]

    # 4-Byte-Tag ueberspringen (0x00 'gpi'); defensiv, falls kuenftig anders
    off = 4 if body[:1] == b"\x00" else 0

    files: List[FanFile] = []
    status: Optional[FanStatus] = None
    i = off
    n = len(body)
    while i < n:
        ln = body[i]
        if ln == 0 or i + 1 + ln > n:
            break
        chunk = body[i + 1:i + 1 + ln]
        # Trailer erkennen: Namen sind reine GBK-Zeichen (>=0x20 oder GBK-Highbyte);
        # der Status-Block enthaelt Nullbytes im Innern.
        if b"\x00" in chunk:
            status = FanStatus(file_count=chunk[0], flags=chunk[1:])
            i += 1 + ln
            continue
        try:
            name = chunk.decode("gbk")
        except UnicodeDecodeError:
            break
        index = None
        if name and name[0].isdigit():
            j = 0
            while j < len(name) and name[j].isdigit():
                j += 1
            try:
                index = int(name[:j])
            except ValueError:
                index = None
        files.append(FanFile(name=name, index=index, raw=chunk))
        i += 1 + ln
    return files, status


# ---- Selbsttest gegen echten Mitschnitt --------------------------------
_REAL_CAPTURE = bytes.fromhex(
    "43 30 45 45 42 37 43 39 42 41 41 33 00 67 70 69 03 30 d3 e3 05 31 ba fc"
    " c0 ea 05 32 bf d6 c1 fa 05 33 c9 f1 ca de 06 34 54 49 47 45 52 05 36 b5"
    " c6 c1 fd 03 37 b4 ba 05 39 c6 fb b3 b5 04 b7 bf d7 d3 02 c1 b3 06 c2 ed"
    " c0 ef b0 c2 04 ce f7 b9 cf 07 0c 00 00 00 01 00 01 00 00 00 00 00 00 00"
    " 00 43 30 45 45 42 44 46 39 45 35 42 37".replace(" ", "")
)


def test_parse() -> None:
    files, status = parse_file_list(_REAL_CAPTURE)
    for f in files:
        print(f"  {f}")
    assert len(files) == 12, f"erwartet 12, erhalten {len(files)}"
    assert status is not None and status.file_count == 12
    assert files[0].name == "0鱼" and files[0].index == 0
    assert files[4].name == "4TIGER" and files[4].index == 4
    print(f"OK: {len(files)} Dateien, Status.count={status.file_count}, "
          f"flags={status.flags.hex(' ')}")
    # check3-Arithmetik gegen die aus dem Binary abgeleiteten Werte
    assert HologramFan._check3(1) == bytes.fromhex("006363")
    assert HologramFan._check3(2) == bytes.fromhex("006364")
    assert HologramFan._check3(5) == bytes.fromhex("006367")
    print("OK: check3(1/2/5) =", *(HologramFan._check3(n).hex() for n in (1, 2, 5)))


def _shell(fan: "HologramFan") -> None:
    """Interaktive Sitzung: Verbindung bleibt warm, Kommandos eintippen."""
    cmds = {
        "on-off": fan.on_off, "next": fan.next_one, "prev": fan.last_one,
        "play-pause": fan.play_pause, "list-loop": fan.list_loop,
        "single-loop": fan.single_loop, "bright-up": fan.brightness_up,
        "bright-down": fan.brightness_down, "cw": fan.cw_adjust,
        "ccw": fan.ccw_adjust,
    }
    print("Verbinde (warme Sitzung, Keepalive laeuft)...")
    fan.open()
    files, status = parse_file_list(fan._last_rx) if fan._last_rx else ([], None)
    print(f"Verbunden mit {fan.ip}:{fan.port}."
          + (f" {len(files)} Animationen." if files else ""))
    print("Befehle: " + ", ".join(cmds) + ", clock on|off, needle white|black,")
    print("         dial <modus>, time HH:MM, duration <5-30>, button <x>, raw <hex>, quit")
    try:
        while True:
            try:
                line = input("fan> ").strip()
            except EOFError:
                break
            if not line:
                continue
            parts = line.split()
            c = parts[0].lower()
            try:
                if c in ("quit", "exit", "q"):
                    break
                elif c in cmds:
                    cmds[c]()
                elif c == "clock":
                    fan.clock(parts[1] == "on")
                elif c == "needle":
                    fan.needle_color(parts[1] == "white")
                elif c == "dial":
                    fan.dial(parts[1])
                elif c == "time":
                    hms = [int(x) for x in parts[1].split(":")] + [0, 0]
                    fan.set_clock_time(hms[0], hms[1], hms[2])
                elif c == "duration":
                    fan.set_duration(int(parts[1]))
                elif c == "button":
                    fan.button(parts[1])
                elif c == "raw":
                    fan.command(bytes.fromhex(parts[1]))
                elif c == "list":
                    fl, st = parse_file_list(fan._last_rx) if fan._last_rx else ([], None)
                    for f in fl:
                        print(f"  {f}")
                else:
                    print("unbekannt")
                print("  ok")
            except (IndexError, ValueError) as e:
                print(f"  Eingabefehler: {e}")
    finally:
        fan.close()
        print("Verbindung geschlossen.")


def _cli(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        description="Steuerung fuer 3D-Hologramm-Luefter (3D_42CM..., Port 20320).")
    p.add_argument("--ip", default=DEFAULT_IP)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("-v", "--verbose", action="store_true",
                   help="gesendete/empfangene Frames anzeigen")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("selftest", help="Parser/Framing offline pruefen (ohne Geraet)")
    sub.add_parser("list", help="Animationsliste lesen")
    sub.add_parser("shell", help="Interaktiv: warme Sitzung offen halten, Kommandos eintippen")

    # --- Steuerung (per PCAP verifiziert) ---
    sub.add_parser("on-off",         help="Projektor an/aus (Toggle)")
    sub.add_parser("play-pause",     help="Video Play/Pause (Toggle)")
    sub.add_parser("next",           help="Naechstes Video")
    sub.add_parser("prev",           help="Vorheriges Video")
    sub.add_parser("list-loop",      help="Alle Videos in Schleife")
    sub.add_parser("single-loop",    help="Einzelnes Video in Schleife")
    sub.add_parser("bright-up",      help="Helligkeit +")
    sub.add_parser("bright-down",    help="Helligkeit -")
    sub.add_parser("cw",             help="Drehung im Uhrzeigersinn")
    sub.add_parser("ccw",            help="Drehung gegen Uhrzeigersinn")
    st = sub.add_parser("set-time", help="Uhrzeit der Uhr setzen, z.B. 12:30")
    st.add_argument("time", help="HH:MM oder HH:MM:SS")

    ck = sub.add_parser("clock", help="Uhr an/aus")
    ck.add_argument("state", choices=["on", "off"])
    nd = sub.add_parser("needle", help="Zeigerfarbe")
    nd.add_argument("color", choices=["white", "black"])
    di = sub.add_parser("dial", help="Zifferblatt-Modus")
    di.add_argument("mode", choices=["digital", "symbol", "constellation", "zodiac"])
    d = sub.add_parser("duration", help="Videodauer 5..30 s setzen")
    d.add_argument("seconds", type=int)

    # --- Diagnose / Entwicklung ---
    b = sub.add_parser("button", help="1-Byte-Button roh senden (a c d e g h l m p q; j/k = Format/Clear)")
    b.add_argument("letter")
    b.add_argument("-n", type=int, default=1, help="mehrfach senden")
    r = sub.add_parser("raw", help="Hex-Payload rahmen und senden (ohne HEAD/FOOT)")
    r.add_argument("hex", help='z.B. "64" (Button d) oder "6295020100"')

    args = p.parse_args(argv)

    if args.cmd == "selftest" or args.cmd is None and False:
        test_parse(); return
    if args.cmd is None:
        args.cmd = "list"
    if args.cmd == "selftest":
        test_parse(); return

    try:
        fan = HologramFan(args.ip, args.port, args.timeout,
                          verbose=getattr(args, "verbose", False))
        named = {
            "on-off": fan.on_off, "next": fan.next_one, "prev": fan.last_one,
            "play-pause": fan.play_pause, "list-loop": fan.list_loop,
            "single-loop": fan.single_loop, "bright-up": fan.brightness_up,
            "bright-down": fan.brightness_down, "cw": fan.cw_adjust,
            "ccw": fan.ccw_adjust,
        }
        if args.cmd == "shell":
            _shell(fan); return
        if args.cmd == "list":
            files, status = fan.get_file_list()
            print(f"Verbunden mit {fan.ip}:{fan.port} — {len(files)} Animationen"
                  + (f", count={status.file_count}" if status else ""))
            for f in files:
                print(f"  {f}")
        elif args.cmd in named:
            named[args.cmd]()
            print(f"'{args.cmd}' gesendet.")
        elif args.cmd == "clock":
            fan.clock(args.state == "on");  print(f"Uhr {args.state}.")
        elif args.cmd == "needle":
            fan.needle_color(args.color == "white"); print(f"Zeiger {args.color}.")
        elif args.cmd == "dial":
            fan.dial(args.mode); print(f"Zifferblatt: {args.mode}.")
        elif args.cmd == "set-time":
            parts = [int(x) for x in args.time.split(":")]
            while len(parts) < 3:
                parts.append(0)
            fan.set_clock_time(*parts[:3])
            print(f"Uhrzeit gesetzt: {parts[0]:02d}:{parts[1]:02d}:{parts[2]:02d}")
        elif args.cmd == "button":
            for i in range(args.n):
                fan.button(args.letter)
                if i + 1 < args.n:
                    time.sleep(0.3)
            print(f"gesendet: '{args.letter}' x{args.n} "
                  f"({fan.frame(args.letter.encode()).hex(' ')})")
        elif args.cmd == "duration":
            fan.set_duration(args.seconds)
            print(f"Videodauer -> {max(5, min(30, args.seconds))} s")
        elif args.cmd == "raw":
            payload = bytes.fromhex(args.hex)
            resp = fan.command(payload, read=True)
            print(f"gesendet: {fan.frame(payload).hex(' ')}")
            if resp:
                print(f"Antwort ({len(resp)} B): {resp[:32].hex(' ')}...")
        fan.close()
    except OSError as e:
        print(f"Verbindung fehlgeschlagen ({e}). Mit WLAN 3D_42CM_... verbunden? "
              f"Offline-Test: python hologram_fan.py selftest")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:            # Rueckwaertskompatibel
        test_parse()
    else:
        _cli()
