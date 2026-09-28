# 合成黃光課程測試集

用來在公司外開發與驗證「分 space compile 的 LLM wiki」pipeline。所有數據、模型名稱、recipe 皆為虛構，不代表任何真實製程。

## 結構

```
manifest.json            space、模擬使用者權限、頁面清單（模擬平台 API 回傳）
canonical_terms.json     標準術語表（canonical ID 與別名），視為 Common 產物
spaces/<space>/pages/    課程 markdown，一個檔案 = 一個平台頁面
spaces/<space>/attachments/  圖片附件
gold/slides.json         逐張投影片的標準答案（重點、圖型、必須讀出的值、概念）
gold/cross_space_meef.json   四種使用者打開 MEEF 時應看到 / 不應看到的內容
gold/leak_probes.json    canary 字串，出現在不該出現的使用者輸出就是洩漏
gold/probe_queries.json  查詢層測試問題與各使用者的預期回答
gold/sync_scenarios.json 頁面搬移、刪除、權限變更的預期系統行為
generate_testset.py      產生器，可修改後重新產生
```

格式假設：投影片以 `---` 分隔、`## ` 為投影片標題、圖片以 `![](attachments/…)` 引用、跨 space 嵌入語法為 `{{include page=… section=…}}`。請依實際平台格式調整解析器，而不是調整這份資料。

## 空間與使用者

| 使用者 | 可讀 space |
|---|---|
| u_general | sp_common |
| u_opc | sp_common, sp_opc |
| u_cd | sp_common, sp_cd |
| u_opc_cd | sp_common, sp_opc, sp_cd |

## 每個案例在測什麼

| 投影片 | 測試重點 |
|---|---|
| common_c1#2 | 圖的重點只能由文字決定（dense / iso 最佳焦距差異） |
| common_c1#3、#4 | 從圖判讀近似數值（DOF、兩種 pitch 的 MEEF） |
| common_c2#2 | 文字沒有答案，誤差型態與量級必須由圖和比例尺判讀 |
| opc_o1#1、o2#1 | 示意圖辨識；兩門課共用同一張圖（去重） |
| opc_o1#2、o2#2 | 規則只在截圖中（OCR）；版本演變 3.0 → 2.5 |
| opc_o2#4 | 跨 space 連結與 include 不可展開，產物不得含 CD 內容 |
| cd_d1#3 | 左右對應只寫在文字中 |
| cd_d1#4 | 只用別名「光罩誤差放大因子」，需對齊到 concept:meef |
| cd_d1#5 | markdown 表格轉結構化參數 |
| MEEF 合併頁 | OPC 假設 2.6 與 CD 實測 3.4 的差異，只有 u_opc_cd 看得到 |

## 建議的評估方式

- 投影片判讀：概念召回率、必讀數值在容差內的比例、`numbers_from_figure` 標記是否正確。
- 概念對齊：每張投影片對到的 canonical ID 是否與 gold 一致。
- 版本演變：OPC 的 MEEF 頁以 2.5 為現行值，3.0 只出現在版本演變段落。
- 洩漏測試：以四種使用者分別執行 probe queries 並開啟 MEEF 頁，任何 canary 出現在 `must_never_reach` 的使用者輸出中即為失敗。這項是零容忍，其他指標可以逐步優化。
- 回應一致性：無權限與查無資料的回應必須無法區分。
