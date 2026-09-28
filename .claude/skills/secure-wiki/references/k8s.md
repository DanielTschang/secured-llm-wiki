# Kubernetes 部署

目標：讓應用層的不變式同時由基礎設施強制執行。即使程式有 bug，基礎設施也要讓錯誤的存取失敗。

## ingest 的 space 隔離（ADR-006）

ingest 是單一 `ingest-worker` Deployment，space 隔離在程式與行程層級：

- 每個 ingest 任務在獨立子行程中執行，子行程只持有 Vault 依該 space role 發放的**短時效憑證**（MongoDB database user、MinIO／LanceDB bucket、Neo4j instance、transit 金鑰）；子行程不跨 space 重用。
- 主行程只轉送 ID，不讀取 space 內容，也不把自己的 Vault token 傳給子行程。
- 流程程式碼只透過 `SpaceContext` 存取資料，不得自行建立連線。
- NetworkPolicy 預設拒絕，只允許：儲存、模型服務、Vault、NATS、DNS；**沒有對外出口**。
- 較強的沙盒 runtime（gVisor 或 Kata，dev 預設關閉），因為文件解析本身有風險。
- Pod 安全設定：non-root、唯讀 root filesystem、無特權、drop all capabilities。
- 已知限制：ingest-worker 的 ServiceAccount 可以向 Vault 申請任何 space 的憑證；這層隔離防程式 bug，不防主行程被入侵。

每個 space 的儲存資源（MongoDB database 與 user、bucket、Neo4j instance、Vault role 與金鑰）由 Helm `values.spaces` 樣板化產生，新增 space 不需要手寫設定。

## 共用元件

| 元件 | 風險 | 要求 |
|---|---|---|
| 佇列 | 管理介面可看到所有訊息 | 每 space 一個 topic；ACL 限定消費者；訊息只含 ID |
| 模型服務 | 請求 log 含完整 prompt；prefix cache 計時側通道 | 關閉內容 log；視需要依 space 隔離或關閉 prefix cache |
| 集中式 log／trace | 跨 space 的共用儲存 | 只記錄 ID 與數值；見下節 |
| Redis 等快取 | 多 space 內容集中 | 值加密；key 不含標題或內容 |
| view 與查詢服務 | 同時處理多 space | 依每次請求的讀者權限向 KMS 取得金鑰；無狀態；不落地 |

## 可觀測性

- log、trace span、例外訊息、metric 與 metric label 只能包含 ID（space_id、page_id、claim_id、request_id）與數值。
- 禁止記錄：頁面標題、概念名稱、查詢原文、投影片內容、模型輸入輸出。
- 需要除錯內容時，寫入該 space 的加密儲存，並以 ID 關聯。
- 以自動化測試驗證：跑完合成測試集後，收集所有 log 與 metrics，不得出現任何 canary。

## 金鑰與管理邊界

- 金鑰在叢集外的 KMS，不放 k8s Secret。
- 限制 production namespace 的 `exec`、`port-forward`、`debug`；啟用 k8s audit log。
- 叢集管理者應無法解密任何 space 內容。

## 並行與順序

- 同一 space 同時只有一個同步處理者（lease 或分片）。
- 所有寫入攜帶 `(page_id, version)`，儲存端拒絕過期寫入（fencing）。
- 讓資料更難被看到的事件（刪除、搬到更嚴格的 space）優先處理。
- view 快取以 space 世代號碼作為 key 的一部分，避免跨副本廣播失效。

## 開發環境

- 使用 kind 或 k3d 本機叢集；`deploy/` 中的設定與程式碼一起開發。
- 只對本機開發 context 執行 kubectl／helm。
- 基礎設施 leak 測試：
  - 以 `sp_opc` 任務子行程的憑證讀取 `sp_cd` 的 bucket、MongoDB database、Neo4j → 必須失敗；
  - 從 ingest-worker pod 對外連線 → 必須失敗；
  - 以 `sp_opc` 的身分向 KMS 要求 `sp_cd` 金鑰 → 必須失敗；
  - 全部 log 中不得出現 canary。
