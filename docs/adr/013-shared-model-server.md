# ADR-013：共用模型服務的跨 space 隔離

- 狀態：已採納
- 日期：2026-09-29
- 相關：ADR-006、ADR-011（任務被入侵的威脅模型）、architecture §11「模型服務：依 space 隔離 prefix cache」

## 背景

M2 起 ingest 任務經 `model-gateway` 呼叫地端模型服務（開發環境為 host 上的 Ollama，OpenAI 相容 API），所有 space 共用同一個 gateway 與同一個模型服務。

M2 的 leak review 指出兩條跨 space 路徑，前提都是「某個 space 的任務被入侵」（ADR-011 的威脅模型：任務會解析 space 成員撰寫的文件與圖片）：

1. **prefix／KV cache**：gateway 原本原樣轉回上游回應，其中 `usage.prompt_tokens_details.cached_tokens` 精確透露「這個 prompt 與模型服務剛處理過的 prompt 共用多少前綴 token」。被入侵的 sp_cd 任務可藉此逐 token 猜出 sp_opc 的課名與投影片內容，再用自己合法的 sp_cd 憑證寫進 sp_cd。即使不回傳 usage，快取命中也會縮短回應時間，形成計時側通道。
2. **排隊延遲**：共用模型服務時，一個任務量測自己呼叫的延遲，可推知其他 space 此刻是否有 ingest 在跑、量有多大。

## 決策

1. **gateway 只回傳文字**：只轉回 `choices[0].message.content`；`usage`（含 cached token 數）、id、fingerprint 等描述模型服務狀態的欄位一律丟棄；上游非 200 或無法解析時回 502，不轉送內容。
2. **每個 space 的 prompt 以不同的秘密前綴開始**：每次模型呼叫的第一段是該 space 的 cache salt（以該 space 的 Vault transit HMAC 金鑰對固定標籤計算，取 32 個 hex）。子 token 只能用自己 space 的 HMAC 金鑰，所以 sp_cd 的任務算不出 sp_opc 的 salt。不同 space 的 prompt 從第一個 token 就不同，模型服務的 prefix cache 不可能跨 space 命中，快取命中的計時差異也就不跨 space。同一 space 內仍可共用快取。
3. **排隊延遲的剩餘風險明文接受（開發環境與 M2–M5）**：只在「任務被入侵」時成立，且只能得知其他 space 的 ingest 活動量，不能取得內容。正式環境依下列要求處理。
4. **正式環境模型服務的要求**（上線前必須滿足）：
   - 無對外出口；只接受 model-gateway 連入；
   - 不記錄 prompt 與回應內容；
   - prefix cache 依 space 隔離（本 ADR 的 salt，或伺服器端的 `cache_salt` 等等價機制），或關閉；
   - 若要消除排隊延遲側通道，每個 space 使用獨立的模型服務 instance 或保留容量；否則在上線審查時明文接受。

## 影響

- gateway 的回應格式固定為 `{"choices": [{"message": {"content": "..."}}]}`；需要 token 計數等資訊時只能在 gateway 內以數值記錄，不回傳給呼叫者。
- 每次模型呼叫多一小段前綴；同一 space 內的快取效果不受影響。
- 開發環境的 Ollama 在 host 上執行，不受 `deploy/` 控制；上述正式環境要求目前沒有機制強制，已列於 architecture §16。
- **其他模型呼叫者**：M4／M5 的查詢與 view 也會呼叫模型，且 prompt 含多個 space 的內容。每個呼叫者都必須在 prompt 開頭加上 salt，並由有效標籤集合推導（例如依序以每個 space 的 HMAC 金鑰串接計算），使只持有部分 space 金鑰的人算不出來。
- **圖片 embedding 快取**：開發用的 Ollama 實測沒有跨請求的圖片 embedding 快取；正式環境的推論服務若有，也必須依 space 隔離或關閉。

## 剩餘風險

- 排隊延遲（見決策 3）。
- **salt 固定不輪替**：被入侵的任務理論上可用逐 token 的計時差累積猜測其他 space 的 salt。實測每個 token 的訊號只有數毫秒、請求耗時雜訊數百毫秒，對 128 bit 的 salt 實務上不可行；需要時可把標籤版本（`kc/model-cache-salt/v1`）改為可輪替。
- **與附件 ID 共用 HMAC 金鑰、未做 domain separation**：若某附件的 bytes 恰好等於 salt 的標籤，其附件 ID 會等於 salt 的前半段。附件 ID 只在該 space 範圍與管理者可見，不構成跨 space 洩漏；日後若要改為每種用途加前綴，需一併遷移既有附件 ID。

## 考慮過的替代方案

- **每個 space 一個模型服務 instance**：同時消除快取與排隊延遲兩條路徑，但 GPU 成本隨 space 數量成長；列為正式環境的選項。
- **伺服器端 `cache_salt`（例如 vLLM）由 gateway 注入**：gateway 必須能在網路層分辨呼叫者的 space，而目前各 space 的 runner 在同一個 Pod；改成每 space 一個 Deployment 後可行。
- **關閉 prefix cache**：最簡單，但同一 space 內重複的系統提示（指引、術語表）會失去快取效益。
