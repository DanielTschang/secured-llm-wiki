# wafer_map（晶圓分布圖）

以色階表示量測值在晶圓上的分布，通常附色條。

- `pattern`：只能是 `radial_edge_low`（邊緣偏低）、`radial_edge_high`、`radial_center_low`、`radial_center_high`、`gradient`（單方向漸變）、`uniform`、`random` 其中之一。
- `edge_drop_nm`：中心與邊緣的差值（取正值，nm），依色條換算。
- 數值為目測：`numbers_from_figure: true`。
