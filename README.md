# Hologram Fan Projector – Python Tools

Python tools for a 3D hologram fan (POV / LED‑blade display) of the
**`3D_42CM_…`** type, as shipped with the Windows app *"电脑软件 V13.0 /
Windows App V13.0"*. The protocol was reconstructed by reverse‑engineering the
original binary and from Wireshark captures; all control commands are verified
byte‑for‑byte against real traffic.

Two tools are included:

- **`hologram_fan.py`** – control the device over Wi‑Fi (play/pause, next,
  brightness, clock, dial, …). No external dependencies (standard library only).
- **`hologram_bin.py`** – convert between video and the device's `.BIN` clip
  format (encoder / decoder). Requires `numpy` and `opencv-python-headless`.

---

# Part 1 — Device control (`hologram_fan.py`)

## Requirements

- Python 3.8 or newer
- A Wi‑Fi adapter to join the device's access point
- The script `hologram_fan.py`

## Connecting

The fan opens its own Wi‑Fi network (SSID e.g. `3D_42CM_51ABDC`). In AP mode the
device is a TCP server:

| Parameter | Value |
|-----------|-------|
| SSID | `3D_42CM_…` (device‑specific) |
| IP | `192.168.4.1` |
| Port | `20320` |

1. Connect your computer to the `3D_42CM_…` Wi‑Fi.
2. Check the connection:

```
python hologram_fan.py list
```

Expected output (the 12 animations on the SD card):

```
Connected to 192.168.4.1:20320 — 12 animations
  [0] 0鱼
  [1] 1狐狸
  ...
```

If this list appears, everything is connected correctly. Without a device you
can still check the framing offline with `python hologram_fan.py selftest`.

## Usage

In the original app the device holds a **persistent, "warm" connection** and
polls continuously; a single command sent on a connection that is closed again
immediately is ignored. The client mimics this: each command execution opens a
short warm session (handshake + keepalive) and sends the command several times.

### Interactive mode (recommended)

The interactive shell is the most reliable option because it keeps the
connection warm the whole time:

```
python hologram_fan.py shell
```

Then type commands directly:

```
fan> on-off
fan> next
fan> clock on
fan> dial zodiac
fan> time 12:30
fan> duration 15
fan> quit
```

### Single commands

Every command can also be called directly:

```
python hologram_fan.py on-off
python hologram_fan.py next
python hologram_fan.py clock on
python hologram_fan.py dial zodiac
python hologram_fan.py set-time 12:30
python hologram_fan.py duration 15
```

### Command reference

All of the following functions are verified from captures.

| Command | Effect | Note |
|---------|--------|------|
| `on-off` | Projector on/off | Toggle (one button, no separate on/off) |
| `play-pause` | Play / pause video | Toggle |
| `next` | Next video | |
| `prev` | Previous video | |
| `list-loop` | Loop all videos | |
| `single-loop` | Loop a single video | |
| `bright-up` | Brightness + | |
| `bright-down` | Brightness − | |
| `cw` | Rotate clockwise | |
| `ccw` | Rotate counter‑clockwise | |
| `clock on` / `clock off` | Show / hide the clock | |
| `needle white` / `needle black` | Needle colour | |
| `dial <mode>` | Dial face: `digital`, `symbol`, `constellation`, `zodiac` | |
| `set-time HH:MM` | Set the clock time | `HH:MM:SS` also accepted |
| `duration <5–30>` | Display time per video, in seconds | clamped to 5–30 |

**Toggle vs. selection:** `on-off` and `play-pause` are toggles – there is
deliberately no separate "on"/"off", because the app sends only a single button
for them. Only `clock`, `needle` and `dial` have real state values and therefore
take an argument.

### Diagnostics

| Command | Purpose |
|---------|---------|
| `list` | Read the animation list |
| `selftest` | Check framing/parser offline |
| `button <x>` | Send a single raw 1‑byte button |
| `raw <hex>` | Frame and send an arbitrary payload |

The global option `-v` / `--verbose` prints every sent (`TX`) and received
(`LIST`/`RX`) frame – useful when nothing seems to happen:

```
python hologram_fan.py -v shell
```

## Configuration

Global options (before the command):

| Option | Default | Description |
|--------|---------|-------------|
| `--ip` | `192.168.4.1` | Device IP |
| `--port` | `20320` | TCP port |
| `--timeout` | `3.0` | Socket timeout in seconds |
| `-v`, `--verbose` | off | Log frames |

Example:

```
python hologram_fan.py --ip 192.168.4.1 --port 20320 -v duration 20
```

## Using it as a library

```python
from hologram_fan import HologramFan

with HologramFan() as fan:          # opens a warm session
    files, status = fan.get_file_list()
    for f in files:
        print(f)                    # e.g. "[4] 4TIGER"

    fan.on_off()
    fan.next_one()
    fan.clock(True)                 # clock on
    fan.needle_color(white=False)   # needle black
    fan.dial("zodiac")
    fan.set_clock_time(12, 30)      # 12:30:00
    fan.set_duration(15)            # 15 s per video
```

Without the context manager, each command call opens a short warm session
itself; for many commands in a row the context manager (or `fan.open()` …
`fan.close()`) is more efficient.

## Protocol (quick reference)

- **Transport:** TCP, the device is an AP server on `192.168.4.1:20320`.
- **Frame:** every command is `HEAD + check3 + payload + FOOT` as ASCII bytes:
  - `HEAD = "C0EEB7C9BAA3"`
  - `FOOT = "C0EEBDF9E5B7"`
  - `check3` = three check bytes that depend only on the payload length
    (`len 1 → 00 63 63`, `2 → 00 63 64`, `5 → 00 63 67`).
- **Handshake:** send only `HEAD+FOOT` → the device returns the file/animation
  list. The device also sends this list periodically on its own.
- **Command forms:**
  - 1‑byte buttons: `a` on/off, `c` next, `d` prev, `e` play/pause, `g`
    single‑loop, `h` list‑loop, `l` brightness−, `m` brightness+, `p` CW, `q` CCW.
  - Video duration: `C` + 1 byte seconds (5–30).
  - Settings: `b` + `[value, ctx, ctx, id]` with `id` 2 = clock, 3 = needle,
    4 = dial.
  - Clock time: `b` + seconds‑since‑midnight (24‑bit little‑endian) + `id 1`.

## Known limitations

- **BIN upload / "Decode video"** are not implemented here – a capture of a
  successful transfer is missing.
- **`Format Disk` (button `j`) and `Clear Cache` (button `k`)** are known from
  the binary but **not verified from a capture**. They are deliberately not wired
  in as regular commands. `Format Disk` erases the SD card – use it only
  intentionally via `button j`.
- `set-time` is verified for the times tested; the 24‑bit encoding covers the
  full day range.
- Depending on firmware, the device tolerated only one active connection. If the
  original Windows app is running in parallel, close it.

## Troubleshooting

- **"Connection failed":** Connected to the `3D_42CM_…` Wi‑Fi? Is `192.168.4.1`
  reachable? Is the original app closed?
- **Command accepted but nothing happens on the device:** use `-v` to check that
  `LIST` lines arrive (session is alive) and `TX` lines go out. The `shell` mode
  keeps the session warm most reliably.
- **`list` shows 12 or 13 entries at times:** cosmetic – the status trailer is
  sometimes read as an extra entry depending on timing; the 12 names are always
  correct.

---

# Part 2 — Video / BIN conversion (`hologram_bin.py`)

Converts between ordinary video and the device's `.BIN` clip format, so you can
put your own clips on the fan (e.g. a downloaded clip, or a red/cyan **anaglyph**
video for glasses‑based 3D). The format was reconstructed from a known
video → BIN calibration pair produced with the original app.

## Requirements

```
pip install numpy opencv-python-headless
```

## Usage

```
python hologram_bin.py encode video.mp4 -o out.bin     # video  -> BIN
python hologram_bin.py decode clip.bin  -o out.mp4      # BIN    -> preview video
python hologram_bin.py decode clip.bin  --png frames/   # BIN    -> individual PNGs
python hologram_bin.py info  clip.bin                   # show frame count / layout
```

`decode` is handy for inspecting what is stored on the SD card; `encode`
produces a `.BIN` you can copy to the SD card (or upload with the original app).

## The BIN format (reverse‑engineered)

| Property | Value |
|----------|-------|
| File | sequence of frames of **129024 bytes** (some files add a 20‑byte trailer) |
| Frame | **512 angles × 252 bytes** |
| 252 bytes | **6 groups × 42 radius** (planar); byte index `p` → group `p // 42`, radius `p % 42` |
| Radius | inverted: index 0 = outer edge, index 41 = centre |
| Frame rate | the app samples **every 2nd** video frame (24 fps → 12 fps) |
| Colour | groups 0–3 = luminance (R+G+B), group 4 = R+G, group 5 = G+B (a YUV‑like coding) |
| Geometry | a 16:9 video is mapped as an ellipse into the circle |

Base colours (pure red / green / blue) are reproduced byte‑exactly; the decoder
recovers shapes faithfully (the animations are clearly recognisable).

## Known limitations

The encoder produces structurally correct, playable BINs, but is **not yet
byte‑identical** to the original app for full images, because two details are not
fully reproduced:

- a **non‑linear saturation / white‑balance curve** the app applies to mixed and
  low‑saturation colours (this can give a slight colour cast in flat areas), and
- the app's **dithering** (it spreads colour over 0/255 pixels; this encoder
  writes continuous values).

Both affect exact colour fidelity, not the basic function. The **angle origin**
(`ANGLE_OFFSET` in the script) can be adjusted if the image appears rotated.

## Recommended workflows

- **Simplest, already working:** create your clip (e.g. an anaglyph video) and
  convert it with the **original Windows app** (`Decode video`), which is proven
  to produce correct BINs. Use `hologram_bin.py decode` to preview any BIN.
- **Fully in Python (experimental):** `hologram_bin.py encode video.mp4 -o
  out.bin`, copy `out.bin` to the SD card, and test on the device. If it plays
  (even with a colour shift), saturation and dithering can be refined next.
