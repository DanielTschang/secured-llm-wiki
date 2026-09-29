# overlay_vector_map（對位誤差向量圖）

晶圓或場內各點畫出誤差向量，通常附比例尺。

- `pattern`：向量的整體型態，只能是 `translation`（全部同向同長）、`rotation`（繞中心切線方向）、`magnification`（由中心放射向外或向內、越外越長）、`orthogonality`、`random` 其中之一。
- `max_nm`：最長向量的長度，依比例尺換算（nm）。
- 數值為目測：`numbers_from_figure: true`。
