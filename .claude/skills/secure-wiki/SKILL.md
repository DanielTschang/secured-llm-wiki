---
name: secure-wiki
description: 開發「分 space ingest 的 LLM wiki 知識中心」時的設計規則與洩漏防範指南：以平台 space ID 為標籤、ingest 只在單一 space 內、跨 space 只在讀取時依讀者權限合成、圖文一起判讀的課程教材、部署於 Kubernetes。凡是撰寫或修改同步服務、ingest 任一步驟、投影片或圖片判讀、概念對齊、wiki 頁面生成、向量或全文索引、知識圖譜、跨 space view、查詢流程、快取、稽核、log／metrics、Helm／NetworkPolicy／RBAC，或審查任何程式是否可能讓無權限者得知其他 space 的資訊時，都必須先讀這份 skill——即使任務看起來只是「加個快取」「改一下 log」「調整 prompt」。本 skill 取代舊的 secure-llm-wiki skill，兩者衝突時以本 skill 與 docs/adr/ 為準。
---

# Secure Wiki

這個系統把各 team 的課程教材 ingest 成 wiki，並讓讀者依其在平台上的權限看到單一或跨 space 的知識。它的全部價值建立在一個承諾上：**沒有權限的人，無法從系統得知任何關於某個 space 的資訊，包括某件事存在與否**。資料擁有者不參與任何審核（ADR-004），所以這個承諾只能靠程式與基礎設施保證。

## 唯一的根本規則

LLM 的輸出可能洩漏任何輸入的任何資訊。因此：

> 讀者能看某個產物，若且唯若他有權看所有影響過該產物的輸入。

「影響」不只是內容，也包括結構（index 條目、圖的 degree、社群）、統計（IDF、排序、去重、計數）、行為（快取命中、延遲、錯誤訊息）與存在本身。寫任何程式前問自己：

> **某個 space 的資料改變時，一個無權讀取它的人，看到的任何東西會不會跟著變？**

會，就是洩漏路徑。以下所有規則都由此推出，遇到規則沒涵蓋的情況，回到這個問題判斷。

## 不變式

1. **標籤是來源 space ID 的集合，衍生物標籤是輸入的聯集。** 由程式依來源計算，不讓 LLM 判斷——黃光知識的機密性取決於出處，不取決於字面。（ADR-001）
2. **`can_read` ⇔ 標籤 ⊆ 平台即時回傳的可讀 space。** 不在本系統儲存權限；平台無回應時拒絕。（ADR-002）
3. **ingest 只在單一 space 內。** 唯一外部輸入是公開的標準術語表。跨 space 合成只在 `services/query/` 的 views 與 query 模組，結果只以快取存在，永不回寫、永不作為輸入。（ADR-003）
4. **先過濾，再計算。** 檢索、圖遍歷、統計都在已授權的範圍內進行；先算再篩會扭曲排序並透露存在。
5. **無權限與查無資料不可區分。** 內容、錯誤、狀態碼、時間差異都一樣。
6. **權限只來自經驗證的身分。** prompt、請求內容、前端參數中的身分宣稱一律忽略。
7. **碰過 space 內容的行程沒有對外出口；可觀測性資料只有 ID 與數值。**

## 標籤與主題是兩個欄位

主題（topic）描述內容在講什麼，用於組織與排序；標籤描述內容從哪個 space 來，只用於權限。公開教材講 OPC 原理：主題 OPC、標籤 `{sp_common}`。永遠不要用主題推論權限，也不要因為內容「看起來很通用」就放寬標籤。

## 程式碼中哪裡可以碰多個 space

| 位置 | 可碰的 space |
|---|---|
| `services/sync/` | 全部，但只做抓取、加密、存放、發事件；不做任何 LLM 處理，不解析內容語意 |
| `services/ingest-worker/` | 僅自己的 space ＋ 標準術語表 |
| `services/query/` 的 views 與 query 模組 | 當次請求讀者可讀的 space |
| 其他任何地方 | 不應同時處理多個 space 的內容 |

如果你發現需要在其他地方同時讀取兩個 space，停下來，這幾乎一定是設計問題，提出 ADR 討論。

## 最常見的洩漏模式

寫程式時特別留意這些，它們不像洩漏，但都是：

- 全域的 index、overview、統計、「最近更新」列表、自動完成詞庫。
- ingest 時為了找「相關頁面」而搜尋了全部 space。
- 部門 ingest 在標準術語表或公開 space 新增條目。
- 快取 key 沒有包含 effective labels 或世代號碼。
- 「你沒有權限查看這 3 筆結果」這類訊息，或結果數量、分頁總數透露了被過濾的筆數。
- 錯誤訊息或 404／403 的差異透露了頁面存在。
- log、trace、例外訊息、metric label 帶有頁面標題、概念名稱或內容片段。
- 佇列訊息夾帶內容而非 ID。
- 圖片經由不驗權的靜態網址或 CDN 提供。
- 跨 space 的 include 在同步或 ingest 時被展開。
- 過期的 ingest 結果覆蓋了頁面搬移或刪除後的狀態。
- 讓 LLM 產生或修改 OKF frontmatter 的 `kc_labels`、`sources`、`verified`、`status`。
- OKF bundle 的檔名或路徑含有概念名稱或標題（路徑會進入 log）。

## 依任務閱讀對應 reference

- 同步、ingest 七步驟、投影片與圖片判讀、wiki 頁生成 → `references/ingest.md`
- 跨 space view、查詢流程、快取、稽核 → `references/views-and-query.md`
- Kubernetes、NetworkPolicy、RBAC、KMS、可觀測性 → `references/k8s.md`
- 撰寫測試、code review、合併前檢查 → `references/leak-checklist.md`

## 完成任何元件後

1. 對照 `references/leak-checklist.md` 自我檢查。
2. 執行 `make leak`。
3. 交給 `leak-reviewer` subagent 審查。

判斷標準只有一個：「一個只有 `sp_cd` 權限的使用者，能否透過這段程式得知 `sp_opc` 的任何資訊？」答案不是明確的「不能」，就還沒完成。
