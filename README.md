# GSR Reader

皮膚電気反応（GSR）のリアルタイム計測・同期分析システム

## 🔧 ハードウェア

| 部品 | 詳細 |
|---|---|
| マイコン | Seeedstudio XIAO SAMD21 |
| センサー 1 | Grove GSR → Groveコネクタ (A0) |
| センサー 2 | Grove GSR → ピン直接接続 (A1) |
| ADC | 12bit (0–4095)、サンプリング 100 Hz |
| 通信 | USB シリアル 115200 baud |

### 配線

```
XIAO SAMD21 (上面)

          USB-C
      ┌───────────┐
  A0  │ ●       ● │ 5V        ← CH1: Groveコネクタ (A0)
  A1  │ ●       ● │ GND       ← CH2: SIG → A1
  A2  │ ●       ● │ 3V3            VCC → 3V3, GND → GND
  A3  │ ●       ● │ D10
  A4  │ ●       ● │ D9
  A5  │ ●       ● │ D8
      └───────────┘
```

## 🚀 使い方

### セットアップ

```bash
pio run -t upload           # ファームウェア書き込み
cd pc && pip install -r requirements.txt  # Python依存パッケージ
```

### リアルタイム表示（同期分析付き）

```bash
python pc/plotter.py --list              # ポート確認
python pc/plotter.py -p COM12            # リアルタイム表示
python pc/plotter.py -p COM12 -w 30      # 30秒ウィンドウ
```

画面構成（4パネル）：
1. **CH1** — Grove A0 の生波形
2. **CH2** — ピン A1 の生波形
3. **共通モード** — (CH1+CH2)/2（同期成分）
4. **同期度** — Pearson r（相関係数）＋ PLV（位相同期値）

### データ保存 → 自動分析

```bash
python pc/plotter.py -p COM12 --save             # 保存しながら表示
python pc/plotter.py -p COM12 --process           # 閉じたら自動分析
```

### オフライン分析

```bash
python pc/process_gsr.py data/session.csv --plot         # SCL/SCR 分解
python pc/process_gsr.py data/session.csv --plot --sync   # + 同期分析
```

## 📐 リアルタイム DSP パイプライン

```
生ADC値 (100Hz)
    ↓
EMA 平滑化 (α=2/21, ~200ms窓)  ← 因果的ガウシアン近似
    ↓
┌───────────────────────────────────┐
│ 共通モード = (CH1 + CH2) / 2      │ → 同期波形パネル
│ 差分モード = (CH1 - CH2) / 2      │ → アーティファクト
├───────────────────────────────────┤
│ Rolling Pearson r (10s窓)         │ → 同期度パネル
├───────────────────────────────────┤
│ 因果バンドパス 0.05–0.5 Hz        │
│ → Hilbert変換 → 瞬時位相          │
│ → PLV = |mean(e^(jΔφ))|          │ → 同期度パネル
└───────────────────────────────────┘
```

## ❓ トラブルシューティング

| 症状 | 対処法 |
|---|---|
| UIは出るがプロットされない | ボードをリセット（USB-CDC の `while(!Serial)` 待機） |
| ポートが見つからない | `python pc/plotter.py --list` |
| PLVが `---` のまま | 5秒以上のデータが必要（低周波帯の位相推定に時間がかかる） |

## 📁 ファイル構成

```
GSR/
├── platformio.ini
├── src/main.cpp            # ファームウェア（2ch → CSV）
├── pc/
│   ├── plotter.py          # リアルタイム表示 + 同期分析
│   ├── dsp.py              # DSPモジュール（EMA, バンドパス, PLV）
│   ├── receiver.py         # CSV 保存のみ
│   ├── process_gsr.py      # オフライン SCL/SCR 分解
│   └── requirements.txt
└── README.md
```
