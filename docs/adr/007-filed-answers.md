# ADR-007：歸檔答案（Filed answers）

- 狀態：已採納
- 日期：2026-09-29
- 修改：ADR-003 第 4、5 點；CLAUDE.md 不變式 3；architecture.md §9

## 背景

LLM Wiki 模式的價值之一是「好的答案可以回存，讓知識累積」。ADR-003 原本規定查詢回答永不回寫、只以快取形式存在，使跨 space 的查詢成果無法累積。

回存答案有一條洗白路徑：答案不只受檢索到的頁面影響，也受提問影響。能讀 `sp_opc`、`sp_cd`、`sp_x` 的使用者可以把 `sp_x` 的內容寫進提問，若答案只以實際用到的頁面標籤（`{sp_opc, sp_cd}`）存放，只能讀 `sp_opc`、`sp_cd` 的人就會看到 `sp_x` 的內容。

## 決策

1. 使用者可**主動選擇**把一次查詢回答歸檔為 filed answer；系統不自動歸檔。
2. filed answer 的標籤 = **提問者在提問當下平台回傳的全部可讀 space**，而非 effective labels。標籤由程式計算，不由 LLM 判斷。
3. filed answer 存放在與 space wiki 分離的區域，不需要人工審核（維持 ADR-004）。
4. filed answer 只在查詢時作為檢索來源，且只對 `can_read(reader, labels)` 成立的讀者可見；永不作為 ingest、view 或 lint 的輸入。
5. 保存答案本身、provenance 與歸檔當下各來源 space 的世代號碼；不保存提問原文與提問者身分。
6. 任一來源 space 的世代號碼改變後，該 filed answer 標記為過時，不再被檢索，除非重新通過 grounding 驗證。
7. 排在 M5（跨 space view）之後實作。

## 影響

- 不變式 3 改為：跨 space 的合成只存在於 `services/query/` 的 views 與 query 模組，以及依本 ADR 規則存放的 filed answer；兩者皆不作為 ingest 的輸入。
- 只有權限為提問者超集合的讀者能重用 filed answer，重用率低，這是安全的必要代價。
- filed answer 的存在本身可能透露「有人關心這個主題」，僅對擁有全部標籤權限的讀者可見，因此不違反 noninterference。
- leak 測試需新增：提問夾帶第三個 space 的 canary，歸檔後以較少權限的使用者查詢，不得出現。

## 考慮過的替代方案

- **標籤 = effective labels**：重用率高，但存在上述洗白路徑，違反不變式 1。
- **回寫為 space wiki 頁**：提問內容進入 space wiki，所有 space 讀者皆可見，且成為 ingest 輸入。
- **維持完全不回存**（ADR-003 原設計）：最安全，但放棄知識累積。
