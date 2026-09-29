# ADR-011：ingest 任務之間的隔離（M2 前置條件）

- 狀態：已採納（方案 2：每個 space 一個 runner）
- 日期：2026-09-29
- 相關：ADR-006（第 3 點：子行程只持有該 space 的短時效憑證）

## 背景

M1 依 ADR-006 實作 ingest-worker：broker container 持有 ServiceAccount token，為每個任務發出只限單一 space、短時效（M2 起 30 分鐘、上限 45 分鐘，因為一頁的模型呼叫需數分鐘，且資料庫 lease 隨 token 失效）、任務結束即撤銷的子 token；runner container 沒有 ServiceAccount token，經 unix socket 接收（ID、子 token）並為每個任務啟動新的子行程。子行程先設為 non-dumpable 並回報 ready，runner 才從 stdin 交付 token。

M1 的 leak review 指出，在「任務子行程被入侵」的威脅模型下（M2 起子行程會解析 space 成員撰寫的文件與圖片，解析器 RCE 是現實風險），仍有一條跨 space 的路徑：

- runner 與所有任務子行程同 UID、同 PID namespace，並共用可寫的 socket 目錄。被入侵的 sp_cd 任務可以 unlink `runner.sock` 並在同一路徑 bind 自己的 socket，此後 broker 會把每個 space 的子 token 送給它。
- 拿到 sp_opc 的 token 後，它可以讀取 sp_opc 內容，再用自己的 sp_cd 憑證寫進 sp_cd，讓 sp_cd 的讀者看到 sp_opc 的資訊。

ADR-006 只宣稱防程式 bug、不防主行程被入侵；它沒有處理「子行程被入侵」。同 UID 的行程無法以檔案權限區分 runner 與任務，所以這不是修一行設定就能解決的問題。

## 決策

**採納方案 2。** ingest-worker Pod 由一個 broker 與每個 space 一個 `runner-<space>` container 組成；每個 runner 有自己的 socket 目錄，只與 broker 共用，並拒絕其他 space 的任務。被入侵的任務最多只能影響自己 space 的 runner 與 token。

評估過的選項：

1. **每個任務一個 Pod（或 Job）**：由 broker 以 k8s API 建立短命 Pod，只掛該任務的子 token（經 projected volume 或 init 交付），任務之間沒有共用的檔案系統、PID 或 socket。隔離最強；每任務有冷啟動成本，broker 需要建立 Pod 的 RBAC。
2. **每個 space 一個 runner container（或 Deployment）**：runner 只接收自己 space 的任務，socket 目錄只在 broker 與該 runner 之間共用。同 space 的任務仍同 UID，但跨 space 的路徑消失。資源隨 space 數量成長。
3. **沙盒 runtime（gVisor／Kata）＋ 每任務不同 UID**：runner 以 setuid 啟動任務需要額外 capability，與 PSA restricted 衝突；需評估 runtimeClass 與 user namespace。
4. **明文接受風險**：記錄「任務子行程被入侵 ⇒ 等同所有 space」，並以文件解析沙盒（gVisor）降低 RCE 機率。不建議，因為這正是 ADR-006 第 3 點想擋的情境。

## 影響

- 同一 space 的任務之間仍同 UID、共用該 space 的 runner；一個任務被入侵可影響同 space 的其他任務，但不跨 space（已列於 architecture §16）。
- runner container 數量隨 space 數量成長；space 很多時再評估改為每 space 一個 Deployment 或方案 1。
- 不論選哪一項，broker 發出子 token、任務 stdin 交付、non-dumpable、任務結束撤銷的機制都保留。
- 所有 space 共用的模型服務是另一條跨 space 路徑（prefix cache、排隊延遲），由 ADR-013 處理。

## 考慮過的替代方案

- **socket 由 broker 監聽、runner 唯讀掛載並維持單一長連線**：可擋掉 unlink／rebind，但同 UID 的任務仍可 ptrace 或讀取尚未強化前的 runner 狀態，屬部分緩解，不單獨採用。
