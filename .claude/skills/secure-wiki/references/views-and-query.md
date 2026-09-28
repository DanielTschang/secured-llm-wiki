# 跨 space View 與查詢

這一層是唯一會在同一次運算中處理多個 space 內容的地方，規則最嚴格。

## 權限取得

```python
readable = platform_acl.readable_spaces(user)   # 身分來自 SSO；短 TTL 快取；fail closed
```

- 任何來自請求 body、prompt、header、前端的權限或身分資訊都忽略。
- `readable` 在整次請求中固定，所有後續步驟都用它，不重新推導。
- 模型工具（search、read_page、graph_expand）在程式層固定注入 `readable`，模型無法傳入或修改。

## View 合成

1. 找出讀者可讀 space 中，概念 X 的 space wiki 頁。
2. `effective_labels` = 找到的頁面標籤聯集。只有一個 space 時直接回傳該頁。
3. 快取 key：`(concept_id, sorted(effective_labels), {s: generation[s] for s in effective_labels}, template_version)`。
4. 合成時 context 依 space 分段標示；原理整合去重，實務依 space 分段並標示來源。
5. 多個 space 的敘述或數值不一致時，產生「跨 team 差異」段落，明確寫出各方說法與來源。
6. 合成結果經 grounding 驗證後寫入快取，標籤 = `effective_labels`，設 TTL。
7. 結果永不寫回 space wiki，永不作為任何 ingest 或 view 的輸入。

`views.cross_space_synthesis = false` 時，改為各 space 分段回傳、不做合成。

注意 `effective_labels` 是「實際用到的內容」而非讀者全部權限：讀者多一個與 X 無關的 space 權限，不應產生新的快取項目。

## 查詢流程

1. 取得 `readable`。
2. 只在 `readable` 中的 space 索引檢索。**不要**全域檢索後再過濾——那會讓 top-k 被不可見的結果擠掉，並透露其存在。
3. 圖擴展一到兩跳，每一跳檢查節點與邊的標籤；或先切出授權子圖再遍歷。
4. context 依 space 分段；system prompt 要求回答標明各論點來源 space，且不把不同 space 的資料寫成單一因果結論。
5. 輸出前逐一重新驗證所有引用來源的 `can_read`（縱深防禦）。
6. 圖片以短時效、驗權的簽章網址提供。
7. 寫稽核紀錄。

## 回應一致性

- 「無權限」與「查無資料」回傳相同內容與狀態碼。
- 不顯示被過濾的筆數、總頁數或「還有其他你無法存取的內容」。
- 錯誤訊息不含頁面標題、概念名稱、space 名稱。
- 注意時間差異：若無權限路徑明顯比查無資料路徑快或慢，可被量測。避免在權限判斷前做只有存在時才會做的昂貴操作。

## 快取

所有快取（view、語意快取、檢索結果、模型回應）的 key 都必須包含 `effective_labels` 與相關世代號碼。最常見的洩漏：A 的查詢被快取，權限不同的 B 問了語意相近的問題而命中。

## 回存

查詢回答不回寫 space wiki。使用者可主動歸檔為 filed answer（ADR-007）：標籤為**提問者當下全部可讀 space**（不是 effective labels，因為提問本身可能夾帶其他 space 的內容）；只在查詢時作為檢索來源，永不作為 ingest、view 或 lint 的輸入；不保存提問原文與提問者身分。

## 稽核

- 記錄：使用者、時間、查詢、`readable`、檢索與引用的 ID、effective labels。
- 稽核資料本身是最高敏感度資料，只限資安與稽核人員存取，不得作為 ingest 來源。
- 提供給 space 擁有者的使用紀錄：只含「誰、何時、用到本 space 的哪些 claims」，不含查詢原文，不含同次查詢用到的其他 space 內容。
