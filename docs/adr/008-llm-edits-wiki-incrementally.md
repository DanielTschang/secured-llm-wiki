# ADR-008：ingest 以 LLM 直接修改既有 wiki 頁面，版本更新由 LLM 判斷

- 狀態：已採納
- 日期：2026-09-29
- 修改：architecture.md §7「ingest 是增量的」段落

## 背景

增量整合有兩種方式：一是 LLM Wiki 模式，新 SourcePage 進來時由 LLM 讀取既有頁面並直接修改；二是以 Slide note 與 Claim 為累積層，頁面每次從全部相關 Claim 重建。本專案以 LLM Wiki 模式為出發點。

## 決策

1. ingest 時，LLM 讀取該 space 的 index 與相關頁面，直接修改既有的 space wiki 頁面或新增頁面。
2. SourcePage 更新（`updated_date` 改變）時，由 LLM 判斷新舊內容的取捨，舊說法寫入「版本演變」段落。
3. Slide note 與 Claim 仍然產生並保存 provenance，供 grounding 驗證與稽核使用，但不作為重建頁面的依據。
4. 原始 SourcePage 的每個 revision 保留不變，grounding 驗證可對照任一 revision。
5. 同一 space 內 ingest 依序執行（見 ADR-006），避免並行修改同一頁面。

## 影響

- 頁面內容與 ingest 順序有關，無法由來源重現。
- 來源刪除或內容移除時，無法由 LLM 可靠地把它的影響從頁面中撤出。M1–M5 不處理刪除（已知缺口）；M6 以「刪除時重跑 grounding」處理：依 provenance 找出受影響頁面，排除已刪除來源的所有 revision 後重新驗證，無剩餘依據的句子刪除。
- 不影響標籤正確性：ingest 只在單一 space 內，所有輸入與輸出的標籤恰為該 space。
- M3 完成標準「OPC 頁以 2.5 為現行值」取決於 LLM 的版本判斷品質，需列入 eval。

## 考慮過的替代方案

- **重建式（Claim 為累積層）**：可重現、支援刪除與版本回溯；代價是每次更新重寫所有受影響頁面，且與 LLM Wiki 模式不同。
