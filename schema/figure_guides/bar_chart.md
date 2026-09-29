# bar_chart（長條圖）

- 若圖上印有總結指標（例如 RMS、平均），以 `<指標>_<單位>` 為 key（例如 `rms_nm`），直接採用印出的數值，`numbers_from_figure: false`。
- 否則讀出每根長條的值，key 為 `<類別>_<單位>`，`numbers_from_figure: true`。
