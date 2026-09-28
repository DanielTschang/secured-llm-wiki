# 同步與 Ingest

## 同步服務

同步服務是唯一能讀所有 space 的元件，因此它的職責要刻意保持狹窄：抓取、加密、存放、發事件。

- 以 webhook 或輪詢觸發，依 space 列出頁面（`page_id, space_id, updated_date`）。`updated_date` 與 content hash 未變則跳過；每次變更由同步服務遞增該頁的 `revision`。
- 抓取**原始 markdown**，不抓渲染結果——渲染結果可能已把其他 space 的 include 展開。
- 附件與頁面同一個 space、同一把金鑰。
- 若頁面帶有頁面層級權限限制，放入隔離區，不 ingest（ADR-001）。
- 頁面換了 space：先發「舊 space 移除」事件並優先處理，再發「新 space 新增」事件。
- 每日做一次完整清單比對，找出刪除或封存的頁面。
- 佇列訊息只放 `(space_id, page_id, revision)`，不放內容。
- 同一個 space 同時只有一個同步處理者，避免重複處理與順序錯亂。

## Ingest 的邊界

ingest 以 space 為單位執行（單一 ingest-worker 服務，每個任務一個只持有該 space 憑證的子行程，見 ADR-006），流程程式碼只透過 `SpaceContext` 存取資料，輸入只有：

1. 該 space 的原始資料與既有產物；
2. 公開的標準術語表（唯讀）。

`build_context` 類的函式在組裝 LLM 輸入時必須檢查所有素材的標籤：

```python
def assert_single_space(space_id: str, items: Iterable[Labeled]) -> None:
    for it in items:
        if it.labels != frozenset({space_id}):
            raise CrossSpaceInputError(it.id)   # 中止並告警，不要靜默略過
```

寫入前再驗一次產物標籤等於 `{space_id}`。超出就代表組裝邏輯有 bug，應中止而非修正後寫入。

所有寫入攜帶 `(page_id, revision)`；儲存端若發現版本已過期、或頁面已不屬於此 space，拒絕寫入。

## 七個步驟

### 1. 解析投影片
- 依原始順序切出文字與 `![](…)` 引用；分隔規則集中在一個可設定的 parser，平台格式未定。
- 跨 space include（例如 `{{include page=… }}`）只保留為引用記號，不展開、不抓取。
- 跨 space 連結保留連結文字，不跟隨。
- 依平台父子頁面或命名規則把頁面分組成課程。

### 2. 逐張判讀
- 文字與圖片以原始順序交錯送入 VLM，附課名、前一張投影片重點、累積術語表。
- 先分類圖型，再套用 `schema/figure_guides/<type>.md` 的判讀指引。
- 輸出結構化投影片筆記：`point`、`figures[{type, reads, numbers_from_figure, confidence}]`、`claims`、`concepts`。
- 目測自圖的數值 `numbers_from_figure: true`；文字、表格、截圖中明寫的數值為 `false`。
- 模型呼叫只經由 `packages/kc-models`。

### 3. 課程層級整合
- 依序讀完整門課的投影片筆記，補足縮寫與前後文，萃取概念、claims、關係。
- 每筆 claim 的 provenance 精確到 `(page_id, version, slide_no, attachment_id?)`。

### 4. 概念對齊
- 對應到標準術語表的 canonical ID（含別名比對）。
- 對不上者建立 `local:<space_id>:<slug>`。**不得**新增或修改術語表——那會讓所有人看到「某個 space 出現了新詞」。

### 5. 編譯 space wiki 頁
- 以概念為單位，彙整本 space 所有課程；依共用模板：定義、原理、本 team 實務、常見問題、版本演變、相關概念、來源。
- 多版本衝突：最新版本為現行內容，舊版本寫入「版本演變」。
- 嵌入一到兩張最具代表性的原圖，標註來源投影片。
- 相關概念連結只指向本 space 頁面或 canonical 概念；不得指向其他 space 的頁面。
- space 層級的 overview、index 只涵蓋本 space。

### 6. Grounding 驗證
- 每句敘述與其引用的原始投影片（文字＋圖片）一起交給驗證模型。
- 無依據者刪除；合理推論保留但標示。
- 驗證未通過率列入 `make eval` 指標。

### 7. 建立索引
- 文字 embedding、圖片 embedding、BM25 統計都寫入本 space 自己的 collection／索引。
- 不跨 space 共用 IDF、去重、reranker 或 embedding 微調資料。

## 增量與失效

- 產物記錄依賴的 `(page_id, version)`；來源變動時只重跑受影響的投影片、概念與頁面。
- 每次 space 內容變動遞增該 space 的世代號碼，供 view 快取失效使用。
- 刪除只依賴某來源的產物；多來源產物重新 ingest。
