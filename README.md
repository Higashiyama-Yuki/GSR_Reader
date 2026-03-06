# GSR Reader

**Galvanic Skin Response** dual-channel data acquisition & analysis system.

## Hardware

| Component | Detail |
|---|---|
| Board | Seeedstudio XIAO SAMD21 |
| Sensor 1 | Grove GSR → Grove connector (A0) |
| Sensor 2 | Grove GSR → ピン直接接続 (A1) |
| ADC | 12-bit (0–4095) |
| Sampling | 100 Hz |
| Interface | USB Serial @ 115200 baud |

### Wiring

```
XIAO SAMD21 (上面から)

        USB-C
    ┌───────────┐
 A0 │ ●       ● │ 5V        ← CH1: Grove connector (A0)
 A1 │ ●       ● │ GND       ← CH2: センサー2 SIG → A1
 A2 │ ●       ● │ 3V3            センサー2 VCC → 3V3
 A3 │ ●       ● │ D10            センサー2 GND → GND
 A4 │ ●       ● │ D9
 A5 │ ●       ● │ D8
    └───────────┘

CH1: Grove GSR → Grove コネクタに差すだけ (A0)
CH2: Grove GSR → ジャンパワイヤで A1, 3V3, GND に接続
```

## Serial Protocol

```
# GSR Dual Sensor Stream
# Format: timestamp_ms,gsr1,gsr2
# START
1042,1523,1480
1052,1518,1475
...
```

## Quick Start

### 1. Flash Firmware

```bash
pio run -t upload
```

### 2. Install Python Dependencies

```bash
cd pc
pip install -r requirements.txt
```

### 3. Real-Time Plot

```bash
python pc/plotter.py --port COM5
python pc/plotter.py --port COM5 --window 30
```

### 4. Record Data

```bash
python pc/receiver.py --port COM5
python pc/receiver.py --port COM5 -d 60
```

### 5. Offline Analysis

```bash
python pc/process_gsr.py data/session.csv --plot
python pc/process_gsr.py data/session.csv --sync --plot
python pc/process_gsr.py data/session.csv --method cvxEDA --plot
```

## Troubleshooting

- **プロットが更新されない**: SAMD21は `while (!Serial)` でPC接続を待つ仕様。plotter.py起動後にボードをリセットすると確実
- **ポート確認**: `python pc/plotter.py --list`
- **生データ確認**: Arduino IDE シリアルモニタ（115200 baud）で CSV 形式を確認
