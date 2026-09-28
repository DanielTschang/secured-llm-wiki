# ADR-003：允許依讀者權限合成跨 space view，但不持久化為知識

- 狀態：已採納
- 日期：2026-09-28

## 背景

同一個概念（例如 MEEF）常同時出現在多個 space 的教材中。對同時擁有多個 space 權限的讀者，最有價值的是一份整合的頁面，以及不同 team 說法之間的差異。

依 noninterference，一份標籤為 `{sp_opc, sp_cd}`、只提供給兩者都可讀的讀者的產物並不違反權限。公司政策也確認：同時擁有兩個 space 權限的人，可以看到系統把兩邊知識合在一起的結果。

但跨 space 的產物若持久化為 wiki 頁或作為 ingest 輸入，會帶來組合數量爆炸、執行 ingest 的行程需要多個 space 的權限，以及來源變動時難以追蹤等問題。

## 決策

1. ingest 只在單一 space 內執行，產出 space wiki 頁（標籤恰為該 space）。
2. 跨 space 的整合只在 `services/query/` 的 views 與 query 模組中（另見 ADR-007 的 filed answer），依當次讀者的權限即時合成。
3. 合成結果的標籤為 effective labels：實際用到的 space wiki 頁標籤聯集，而非讀者的全部權限。
4. 合成結果只以快取形式存在，key 為 `(concept_id, effective_labels, 各 space 世代號碼, 模板版本)`，並設定 TTL。
5. view 與查詢回答永不回寫為 space wiki 頁，也永不作為 ingest 或 view 的輸入。
6. 「跨 team 差異」段落與跨 space 的區域概念對應，只存在於跨 space view 中。
7. 保留設定開關 `views.cross_space_synthesis`（預設 `true`）。若政策改變，設為 `false` 時改為各 space 分段呈現、不做合成。

## 影響

- 讀者看到的是一份整合的頁面，而非多份分開的視角頁。
- 快取數量約等於「實際存在內容交集的 space 組合數」，而非讀者權限組合數。
- 合成需要額外的模型成本；以快取與世代號碼控制。
- 不同權限的讀者看到的同一頁面內容不同，介面上需要標示各段落的來源 space。
- 只有單一 space 權限的讀者，看不到任何跨 space 差異的跡象，包括差異是否存在。

## 考慮過的替代方案

- **完全禁止跨 space 合成**：安全但犧牲系統最大的價值，且與公司政策不符。
- **預先 ingest 所有 space 組合**：組合數隨 space 數量指數成長，且 ingest 行程需持有多個 space 權限。
- **靜態「概念頁 + 各 space 視角頁」**：讀者需自行整合多份頁面，無法呈現跨 team 差異。
