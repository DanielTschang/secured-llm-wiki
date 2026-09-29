# bar_chart（長條圖／殘差圖）

- **先找圖上印出的總結數字**：標題、圖例或角落常印有 RMS、平均等總結值（例如 "RMS = 1.1 nm"）。有的話，這就是要讀的值：key 為 `<指標>_<單位>`（例如 `rms_nm`），數值照抄，`numbers_from_figure: false`。此時不要逐一讀長條。
- 沒有總結數字時，才讀每根長條：key 為 `<類別>_<單位>`，`numbers_from_figure: true`。
