# Knowledge Center

分 space ingest 的 LLM wiki（以 `docs/llm-wiki.md` 的 LLM Wiki 模式為出發點），部署於 Kubernetes。把內部知識管理平台上各 team 的課程教材（圖配字的 markdown）ingest 成 wiki，並依讀者在平台上的權限提供單一或跨 space 的知識。

- 完整架構：`docs/architecture.md`
- 關鍵決策與理由：`docs/adr/`（與其他文件衝突時，以 ADR 為準）
- 領域用詞：`CONTEXT.md`（用詞以此為準，例如一律說 ingest、SourcePage）
- 開發細節與洩漏模式：`.claude/skills/secure-wiki/`

## 不變式（任何程式碼都不得違反）

1. 標籤是來源 space ID 的集合；任何衍生物的標籤 = 其所有輸入的標籤聯集。標籤由程式依來源計算，絕不由 LLM 或內容判斷。
2. `can_read(user, labels)` ⇔ `labels ⊆ 平台即時回傳的使用者可讀 space`。本系統不儲存任何權限資料；平台無法回應時拒絕存取。
3. ingest 只在單一 space 內執行，唯一允許的外部輸入是公開的標準術語表。跨 space 的合成只存在於 `services/query/` 的 views 與 query 模組，產物只以快取形式或依 ADR-007 規則的 filed answer 存在，不回寫為 space wiki 頁面，也不作為 ingest 的輸入。
4. 檢索、圖遍歷、統計一律先依權限過濾再計算。
5. 「無權限」與「查無資料」對使用者必須無法區分，包括回應內容、錯誤訊息與可觀察的時間差異。
6. 權限只來自經驗證的身分，絕不來自 prompt、請求內容或前端參數。
7. 任何處理過 space 內容的行程都沒有對外出口；log、trace、metrics 只記錄 ID 與數值，不記錄內容或標題。

不確定某個設計是否違反不變式時，停下來說明疑慮並提出 ADR 草稿，不要寫程式繞過它。

## 開發規則

- 只使用 `tests/fixtures/synthetic_litho_testset/` 的合成資料。不得讀取、要求或推測任何真實公司資料。
- 只操作本機開發叢集（kind / k3d）。不得對任何其他 kube context 執行 kubectl 或 helm。
- 先寫測試再寫實作。修改 `packages/kc-labels`、`packages/kc-store`、`packages/kc-graph`、`services/ingest-worker`、`services/query`、`services/sync` 或 `deploy/` 後，必須執行 `make leak`。
- 完成一個元件或里程碑後，交給 `leak-reviewer` subagent 審查，FAIL 的項目修正前不得合併。
- 模型呼叫一律透過 `packages/kc-models` 的介面，不得在其他地方直接呼叫模型 API。

## Repo 地圖

```
packages/                 共用函式庫（uv workspace 成員）
  kc-labels/              標籤與 can_read（最小、最核心，改動需特別謹慎）
  kc-platform/            平台 API client（getPages、可讀 space 查詢）
  kc-store/               SpaceContext、依 space 加密的 MongoDB／LanceDB／MinIO adapter
  kc-graph/               依 space 的 Neo4j adapter
  kc-models/              模型介面（測試用 fake backend，開發用 Ollama，正式環境用地端 VLM）
  kc-obs/                 只允許 ID 與數值的 logger／metrics
  kc-audit/               稽核紀錄
services/                 每個服務一個 image
  mock-platform/          模擬平台 API 與 JWT 簽發（僅開發與測試）
  sync/                   同步服務（CronJob）
  ingest-worker/          ingest 與 lint（單一服務、每任務一子行程，見 ADR-006）
  query/                  views（跨 space 合成與快取）與 query
  model-gateway/          模型服務閘道
schema/glossary/          標準術語表（人工維護，唯讀掛載）
deploy/                   Helm chart（依 values.spaces 產生每個 space 的資源）、kind 設定
tests/leak/               canary 與基礎設施隔離測試（零容忍）
tests/eval/               對照 gold 的品質評估
tests/fixtures/           合成測試集
```

## 指令

```
make test      單元與整合測試
make leak      應用層與基礎設施層洩漏測試
make eval      對照 gold 的品質評估
make kind-up   建立本機開發叢集
```

## 工作方式

每個里程碑一個分支。開始時先用 plan mode 讀相關文件與 ADR、提出計畫與測試清單，確認後再實作。里程碑定義見 `docs/architecture.md` 第 14 節。
