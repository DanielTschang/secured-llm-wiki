# meef_plot（MEEF 圖）

橫軸為光罩 CD 偏移（通常換算到晶圓尺度），縱軸為晶圓 CD 偏移；MEEF 為直線斜率。

- 每條線各讀一個 MEEF。key：`meef_<系列>`；系列為 pitch 時用 `meef_pitch_<nm 數字>`（例如 `meef_pitch_90`），為層別或圖形類型時用其小寫英文（例如 `meef_contact`）。
- 若圖上印有擬合結果（例如 "MEEF = 3.4" 或斜率標註），直接採用，`numbers_from_figure: false`；否則由兩點估算斜率，`numbers_from_figure: true`。
