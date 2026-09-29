# screenshot（軟體畫面、設定或規則表的截圖）

截圖中的文字、參數或規則表（規則表即使畫成表格，仍屬截圖）。

- 逐項抄錄參數：key 為參數名稱的小寫 snake_case，帶單位後綴。
  - 門檻規則（例如 "MEEF > 3.0 標為 hotspot"）→ `<指標>_threshold`，例如 `meef_threshold: 3.0`。
  - 尺寸規則（例如 "min space < 40 nm"、"line-end pullback > 8 nm"）→ `min_space_nm: 40`、`pullback_nm: 8`。
  - 軟體或模型名稱與版本 → `model`，值照抄（例如 `ABC-3`）。
- 數值照抄截圖文字（不要換算）：`numbers_from_figure: false`。
