# ADR-009：Monorepo 與技術選型

- 狀態：已採納
- 日期：2026-09-29

## 背景

系統以 Python microservice 部署於 Kubernetes。需要決定 repo 結構、服務間溝通、儲存與金鑰管理，且每項選擇都必須能以 space 為單位做隔離。

## 決策

| 項目 | 選擇 | 理由 |
|---|---|---|
| repo | monorepo，uv workspace（`packages/`、`services/`） | 服務獨立部署；共用的 `kc-labels` 不會因版本漂移造成安全問題；依賴方向（ingest 不得依賴 query）以 workspace 宣告與 import-linter 強制；`make leak` 可跨服務執行 |
| 語言 | Python 3.14（套件不支援時退回 3.13）；ruff、pyright strict、pytest、hypothesis | |
| 服務間呼叫 | FastAPI／HTTP | |
| 佇列 | NATS JetStream，每 space 一個 subject | subject 權限可直接落實「每 space 限定消費者」；比 Kafka 輕 |
| 文件與 metadata | MongoDB，每 space 一個 database 與 user | database 層級權限隔離；Community 無 encryption at rest，內容欄位於應用層加密 |
| 向量與全文索引 | LanceDB，存於 object store，每 space 一個 bucket | 每 space 一個 table，BM25 的 IDF 天然只在單一 space 內計算 |
| 圖 | Neo4j Community，每 space 一個 instance | Community 只有單一 database 且無細粒度 RBAC；共用 instance 以屬性過濾會先算後篩 |
| object store | MinIO（固定版本） | |
| 金鑰與憑證 | Vault（transit、per-space role），dev mode 模擬叢集外 KMS | policy 可綁 k8s ServiceAccount；可發放 per-space 短時效憑證 |
| 本機叢集 | kind + Cilium + Helm | Cilium 確實 enforce NetworkPolicy；Helm 以 `values.spaces` 樣板化每 space 資源 |
| 開發用身分 | mock-platform 簽 JWT，服務以 JWKS 驗簽 | 之後換成真正的 OIDC 只需改設定 |
| 模型 | 測試用 fake backend；eval 用 Ollama 上的 VLM | 測試具決定性；全程無對外出口 |
| CI | 先在本機執行；之後遷移至內部 GitLab | |

## 考慮過的替代方案

- **polyrepo**：共用函式庫需發佈與版本管理，舊版 `kc-labels` 會成為安全問題；跨服務的 leak 測試無處安放。
- **Postgres + pgvector**：以 DB／role 隔離也可行；改用 MongoDB + LanceDB 以符合團隊偏好與 LLM Wiki 參考實作（nashsu/llm_wiki 使用 LanceDB）。
- **單一 Neo4j 以 `space_id` 屬性過濾**：degree、社群、路徑會在過濾前計算，違反不變式 4。
