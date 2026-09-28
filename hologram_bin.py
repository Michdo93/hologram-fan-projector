#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hologram_bin.py — Encoder/Decoder fuer das BIN-Format des Hologram-Fans
(3D_42CM...), abgeleitet aus einem bekannten Video/BIN-Kalibrierpaar.

Format (verifiziert):
  * Datei = Folge von Frames a 129024 Byte (+ optional 20 Byte Trailer)
  * Frame = 512 Winkel x 252 Byte;  252 = 6 Gruppen x 42 Radius (planar!)
      Byte-Index p im Winkel -> Gruppe = p // 42,  Radius = p % 42
  * Radius invertiert: Index 0 = aussen, Index 41 = Zentrum
  * Framerate: die App nimmt jeden 2. Video-Frame (24 fps -> 12 fps)
  * Farbe (RGB 0..1 -> 6 Gruppenwerte, Einheit ~116):
      Gruppe 0..3 = (R+G+B)   (Luminanz, saturiert)
      Gruppe 4    = (R+G)
      Gruppe 5    = (G+B)
    Grundfarben rot/gruen/blau werden damit byte-genau reproduziert.
  * 16:9-Video wird als Ellipse in den Kreis abgebildet.

Bekannte Naeherungen: Mischfarben saturieren leicht nichtlinear; die App
dithert zusaetzlich (Pixel 0/255) — dieser Encoder schreibt die kontinuier-
lichen Werte. Der Winkel-Nullpunkt (ANGLE_OFFSET) ist einstellbar.

CLI:
  python hologram_bin.py encode IN.mp4 -o OUT.bin
  python hologram_bin.py decode IN.bin -o OUT.mp4      # oder --png DIR
  python hologram_bin.py info  IN.bin
"""
from __future__ import annotations
import argparse, sys
import numpy as np
try:
    import cv2
except ImportError:
    sys.exit("OpenCV benoetigt:  pip install opencv-python-headless")

FRAME = 129024
ANG, GRP, RAD = 512, 6, 42
TRAILER = 20            # manche Dateien haben ihn, viele nicht
FRAME_STEP = 2          # jeder 2. Video-Frame
ANGLE_OFFSET = 0.0      # Winkel-Nullpunkt (rad), bei Bedarf justieren
ANGLE_DIR = 1           # Drehrichtung (+1/-1)

# Farb-Matrix RGB(0..1) -> 6 Gruppenwerte  (aus Kalibrierung)
M = np.array([[116,116,116,116,116,  0],    # R
              [116,116,116,116,116,116],    # G
              [116,116,116,116,  0,116]],   # B
             dtype=np.float32)
M_INV = np.linalg.pinv(M)                    # 6 -> RGB (fuer decode)


# ---------- Encode: Video -> BIN ----------
def encode_frame(vf: np.ndarray) -> np.ndarray:
    """Ein BGR-Videoframe -> 252*512-Byte-Frame."""
    H, W = vf.shape[:2]
    cx, cy, rx, ry = W / 2, H / 2, W / 2, H / 2
    a = np.arange(ANG) * 2 * np.pi / ANG * ANGLE_DIR + ANGLE_OFFSET
    r = (41 - np.arange(RAD)) / 41.0                      # idx0 = aussen
    xs = np.clip(cx + (r[None, :] * rx) * np.cos(a[:, None]), 0, W - 1).astype(int)
    ys = np.clip(cy + (r[None, :] * ry) * np.sin(a[:, None]), 0, H - 1).astype(int)
    rgb = vf[ys, xs, ::-1].astype(np.float32) / 255.0     # (ANG,RAD,3) RGB
    six = np.clip(rgb.reshape(-1, 3) @ M, 0, 255).reshape(ANG, RAD, GRP)
    return six.transpose(0, 2, 1).reshape(ANG, 252).astype(np.uint8).tobytes()


def encode(inp: str, out: str) -> None:
    cap = cv2.VideoCapture(inp)
    if not cap.isOpened():
        sys.exit(f"Kann Video nicht oeffnen: {inp}")
    n_in = n_out = 0
    with open(out, "wb") as f:
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if n_in % FRAME_STEP == 0:
                f.write(encode_frame(fr))
                n_out += 1
            n_in += 1
    cap.release()
    print(f"{n_out} Frames -> {out}  ({n_out*FRAME} Byte)")


# ---------- Decode: BIN -> Bild/Video ----------
def decode_frame(raw: np.ndarray, smooth: bool = True, size: int = 240) -> np.ndarray:
    fr = raw.reshape(ANG, GRP, RAD).astype(np.float32)
    six = fr.transpose(0, 2, 1).reshape(-1, GRP)
    rgb = np.clip(six @ M_INV, 0, 1).reshape(ANG, RAD, 3)
    if smooth:                                            # Dithering rausmitteln
        rgb = cv2.blur(rgb.reshape(ANG, RAD * 3), (1, 9)).reshape(ANG, RAD, 3)
        rgb = np.stack([cv2.blur(rgb[:, :, k], (5, 3)) for k in range(3)], -1)
    N = RAD
    img = np.zeros((2 * N + 2, 2 * N + 2, 3), np.uint8)
    a = np.arange(ANG) * 2 * np.pi / ANG * ANGLE_DIR + ANGLE_OFFSET
    rr = (41 - np.arange(RAD))
    for i in range(ANG):
        x = (N + rr * np.cos(a[i])).astype(int)
        y = (N + rr * np.sin(a[i])).astype(int)
        img[y, x] = (np.clip(rgb[i], 0, 1) * 255)[:, ::-1]
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_CUBIC)


def n_frames(path: str) -> int:
    import os
    return os.path.getsize(path) // FRAME


def decode(inp: str, out: str = None, png_dir: str = None, fps: float = 12.0) -> None:
    data = np.memmap(inp, dtype=np.uint8, mode="r")
    nf = len(data) // FRAME
    writer = None
    if png_dir:
        import os; os.makedirs(png_dir, exist_ok=True)
    for i in range(nf):
        img = decode_frame(np.array(data[i * FRAME:(i + 1) * FRAME]))
        if png_dir:
            cv2.imwrite(f"{png_dir}/frame_{i:05d}.png", img)
        else:
            if writer is None:
                h, w = img.shape[:2]
                writer = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"),
                                         fps, (w, h))
            writer.write(img)
    if writer:
        writer.release()
    print(f"{nf} Frames dekodiert -> {out or png_dir}")


def info(inp: str) -> None:
    import os
    sz = os.path.getsize(inp)
    print(f"{inp}: {sz} Byte")
    print(f"  Frames: {sz // FRAME}  (Rest {sz % FRAME}"
          + (f" = {TRAILER}-Byte-Trailer" if sz % FRAME == TRAILER else "") + ")")
    print(f"  Format: {ANG} Winkel x {GRP} Gruppen x {RAD} Radius")


def main(argv=None):
    p = argparse.ArgumentParser(description="Hologram-Fan BIN Encoder/Decoder")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode"); e.add_argument("input"); e.add_argument("-o", "--out", default="out.bin")
    d = sub.add_parser("decode"); d.add_argument("input"); d.add_argument("-o", "--out", default="out.mp4")
    d.add_argument("--png", dest="png_dir", default=None, help="stattdessen PNGs in Verzeichnis")
    n = sub.add_parser("info"); n.add_argument("input")
    a = p.parse_args(argv)
    if a.cmd == "encode": encode(a.input, a.out)
    elif a.cmd == "decode": decode(a.input, a.out, a.png_dir)
    elif a.cmd == "info": info(a.input)


if __name__ == "__main__":
    main()
