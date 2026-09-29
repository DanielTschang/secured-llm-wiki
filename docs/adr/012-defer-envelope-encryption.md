# ADR-012：暫時移除應用層 envelope 加密

- 狀態：已採納（暫時性，M6 前必須由後續 ADR 恢復或取代）
- 日期：2026-09-29
- 暫時取代：ADR-009「MongoDB 內容欄位於應用層加密」、ADR-010 第 1 點「以該 space 金鑰加密」；architecture.md §11、§12 中 per-space 加密的描述

## 背景

M1 在 `SpaceStore` 內實作了 envelope 加密：每個物件以 Vault transit 發出的資料金鑰（DEK）做 AES-256-GCM 加密，AAD 綁定 `(space, kind, object, revision)`，DEK 由該 space 的 transit 金鑰包裝。

PM 要求 M2 起先把完整資料流（sync → ingest 各步驟 → 查詢）串起來，再回頭補強。加密目前對 ingest 程式透明，但每個物件都要多一次 Vault 往返，也讓除錯與資料檢視變複雜。團隊決定先移除。

## 決策

1. 移除 envelope 加密：MinIO 物件與 MongoDB 欄位以明文存放；刪除 `kc_store.envelope`，Vault policy 移除 `transit/datakey`、`transit/decrypt`。
2. **保留**所有跨 space 隔離控制，它們不依賴加密：
   - per-space 憑證：Vault policy、MinIO user 與 bucket policy、MongoDB 動態 user（只能 readWrite 自己的 database）、每 space 一個 Neo4j 與其密碼；
   - ingest 子 token 單一 space、短時效、任務結束即撤銷（ADR-006、ADR-011）；
   - 所有寫入的 revision fencing 與標籤檢查。
3. **保留** per-space HMAC 產生的附件 ID 與 content hash：它們防的是不變式 7 的存在性洩漏（ID 會進入 log、object key），與內容加密無關。
4. 恢復條件：上線前必須恢復 per-space 內容加密（可沿用 M1 的設計，見 git 歷史 `packages/kc-store/src/kc_store/envelope.py`），最晚在 M6 完成，否則不得上線。

## 影響

- **失去的保證**：能讀取儲存層的人（MinIO root、MongoDB 管理者、備份、節點磁碟）可以直接讀到內容。architecture §12「管理者無法解密」在恢復前不成立。
- **不變的保證**：一般服務與 ingest 任務仍只能以自己 space 的憑證存取自己 space 的資料；讀者看到的內容仍由 `can_read` 決定。
- 測試從「儲存內容全是密文」改為「每個 space 的儲存不含其他 space 的內容」，這才是跨 space 的不變式；log 與 metrics 仍不得含任何內容（不變式 7）。

## 考慮過的替代方案

- **保留加密**：不影響資料流串接，但團隊選擇先降低複雜度。
- **以設定開關停用**：會留下一條「正式環境忘了開」的路徑，比明確移除更危險。
