# ADR-010：space wiki 以 OKF v0.2 bundle 為正本

- 狀態：已採納
- 日期：2026-09-29
- 修改：ADR-008（頁面格式）；architecture.md §6、§7；Q30 的 `[[wikilink]]` 語法

## 背景

LLM Wiki 模式的 wiki 是一個 markdown 目錄，含 `index.md` 與 `log.md`。Google Cloud 的 Open Knowledge Format（OKF）v0.2 把這個形狀標準化：一個 bundle 是一個 markdown 目錄，每個檔有 YAML frontmatter（唯一必填 `type`），`index.md`、`log.md` 為保留檔名，來源放在 frontmatter 的 `sources[]`，內文以 `[^id]` 註腳逐句引用，信任等級由 `generated`／`verified` 推導，生命週期由 `status` 表示。規格：https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md

## 決策

1. **每個 space 一個 OKF v0.2 bundle，bundle 就是 space wiki 的正本**，存於該 space 的 MinIO bucket `wiki/` 下，以該 space 金鑰加密。MongoDB、LanceDB、Neo4j 中的資料都是從 bundle 解析出的索引，可重建。
2. **目錄與檔名**：`index.md`、`log.md`、`concepts/`、`entities/`、`courses/`、`synthesis/`；檔名一律為不透明 ULID，名稱只在 frontmatter `title`。任何路徑都不含名稱或內容，因為路徑會進入 log。
3. **frontmatter 由 host 程式寫入，LLM 不得產生或修改**：`type`、`sources`、`generated`、`verified`、`status`，以及自訂欄位 `kc_labels`（來源 space 集合）與 `kc_concept`（對應的 canonical 概念 ID）。LLM 只寫內文與 `title`、`description`、`tags`。
4. **引用**：`sources[]` 每張投影片一筆（`id` 如 `opc_o2-s3`，`resource` 為 `kc://<space>/pages/<page_id>?rev=<n>&slide=<k>`，`last_modified` 為平台 `updated_date`）。內文每句附註腳；寫入前檢查所有註腳 id 屬於 `sources[].id`，否則拒絕寫入。grounding 驗證逐一檢查註腳對應的投影片是否支持該句，無依據者刪除或標示為推論。
5. **連結**：頁面間以 OKF 標準連結 `[文字](/concepts/<ulid>.md)`；LLM 以名稱引用，由 host 在同一 bundle 內解析為路徑，解析不到者保留文字、不建立連結。
6. **信任與生命週期**：`generated: {by: "model:<id>", at}`；grounding 通過後 `verified: {by: "kc-grounding/<version>", at}`（machine-confirmed）；永不出現 `human:` actor（ADR-004）。`status` 在驗證前為 `draft`、通過後為 `stable`、來源全數消失時為 `deprecated`；查詢只檢索 `stable` 頁面。
7. **圖片**：內文以 `![說明](kc-figure://<attachment_id>)` 引用；query-service 回傳前驗權並換成短時效簽章網址。bundle 內不放圖片檔或公開網址。
8. **寫入**：以 MinIO 條件式寫入（ETag）加上 revision 做 fencing。
9. **view 與 filed answer**：預設以一般回應格式提供；讀者可選擇以 OKF 單一文件格式取得，frontmatter 帶 effective labels（filed answer 為其 labels）。格式選擇不影響權限判斷。

## 影響

- bundle 可整包匯出，任何 OKF 相容工具可讀；但匯出物帶有 `kc_labels`，匯出本身必須經過 `can_read`。
- MongoDB 從「文件正本」降為索引與 Slide note／Claim 的存放處。
- 「無權限」與「查無資料」一致性同樣適用於以 OKF 格式取得的回應。
