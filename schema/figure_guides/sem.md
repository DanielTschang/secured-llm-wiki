# sem（SEM 影像）

一或多張 SEM 影像，常左右或上下並排比較。

- 每張影像依位置各一個 key：`left`、`right`，或 `top`、`bottom`；單張時用 `image`。
- 值只能是 `normal`、`bridging`（相鄰圖形相連）、`pinching`（線寬局部變細）、`line_collapse`、`scumming`、`other` 其中之一。
- 左右或上下的對應關係以投影片文字為準。
- 無數值：`numbers_from_figure: false`。
