# ADR-006：ingest 由單一共用服務執行，space 隔離降為程式與行程層級

- 狀態：已採納
- 日期：2026-09-29
- 修改：architecture.md §4、§11 的 ingest Job 部署形式

## 背景

原架構讓 ingest（原稱 compile）以「每個 space 一個 namespace 的 Job」執行，每個 space 有獨立的 ServiceAccount、NetworkPolicy、KMS 金鑰與儲存權限，使「ingest 只在單一 space 內」由基礎設施強制。

各 space 的 ingest 邏輯完全相同，差別只在目標儲存（MongoDB database、LanceDB 位置、Neo4j instance、Vault role）。為降低維運成本並符合組織慣用的服務架構，改為單一常駐服務。

## 決策

1. 單一 `ingest-worker` 服務消費所有 space 的 NATS subject；同一 space 內依序處理（`max_ack_pending=1`），不同 space 之間並行。
2. ingest 流程程式碼只有一份，只透過 `SpaceContext` 存取資料。`SpaceContext` 由 space registry（設定）與 storage adapter（`SpaceStore`、`SpaceIndex`、`SpaceGraph`）建構，只綁定單一 space；流程程式碼不接觸任何連線資訊。
3. 每個 ingest 任務在獨立子行程中執行，子行程只持有該 space 的短時效憑證（由 Vault 依 space role 發放）；主行程只轉送 ID，不讀取 space 內容；子行程不跨 space 重用。
4. 流程程式碼不得自行建立 DB、object store 或 HTTP 連線，以 import-linter 與 CI 靜態檢查輔助強制。
5. 所有程式碼位於主 monorepo，不使用外部外掛 repo；可替換的實作以 Protocol 與設定切換。
6. 標籤由 host 在寫入時依 space 計算，流程程式碼不能設定。

## 影響

- **失去的保證**：服務的 ServiceAccount 可以向 Vault 申請任何 space 的憑證，網路可連到所有 space 的儲存。子行程與 `SpaceContext` 防的是程式 bug，防不了主行程被入侵。
- M1 完成標準「從 sp_opc 的 ingest pod 讀 sp_cd 的儲存必須失敗」改為「sp_opc 任務的子行程以其憑證讀 sp_cd 的儲存必須失敗」。
- 文件解析沙盒（gVisor／Kata）只能套用在整個服務。
- 新增 space 只需新增 registry 設定與該 space 的儲存資源，不需新增服務。

## 考慮過的替代方案

- **每 space 一個 namespace 的 Job**（原設計）：隔離最強，每次 ingest 有冷啟動成本，k8s 物件隨 space 數量成長。
- **每 space 一個 ingest-worker Deployment**：保留全部基礎設施隔離；因維運成本與組織慣例而未採用。
- **外掛 repo 動態載入**：各 space 邏輯相同，沒有需要獨立發版的實作。
