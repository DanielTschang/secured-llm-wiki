# Knowledge Center

把內部知識管理平台上各 team 的課程教材 ingest 成 wiki，並依讀者在平台上的權限提供單一或跨 space 的知識。

## Language

### 來源

**Space**:
平台上的權限單位，通常對應一個 team；每個 SourcePage 恰屬於一個 space。
_Avoid_: 部門、分區

**Public space**:
所有系統使用者都可讀的 space（例如 `sp_common`），在標籤模型中沒有特殊地位。
_Avoid_: Common 分區

**SourcePage**:
平台上的一個頁面連同它引用的所有附件，是同步與變更偵測的單位。
_Avoid_: 文件、document

**Slide**:
SourcePage 中以分隔符切出的單元，包含一段文字與其圖片，是最小的判讀單位。
_Avoid_: 段落、chunk

### 權限

**Labels**:
一個產物的來源 space ID 集合；衍生物的 labels 是其所有輸入 labels 的聯集。
_Avoid_: 機密等級、部門標籤

**Effective labels**:
一個 view 或查詢回答實際用到的 space wiki 頁 labels 聯集，而非讀者的全部權限。

**Topic**:
內容在講什麼，只用於組織與排序，永遠不參與權限判斷。

### 產物

**Slide note**:
VLM 判讀一張 Slide 的結構化輸出。

**Claim**:
一筆附 provenance 的可驗證敘述。

**Canonical glossary**:
只從公開來源建立、所有使用者可讀的標準概念 ID 與別名。
_Avoid_: 共用詞庫

**Space wiki page**:
單一 space 內 ingest 出的頁面，labels 恰為該 space；分為 concept page、entity page、course summary page、synthesis page。

**Bundle**:
一個 space 的全部 space wiki page，以 OKF v0.2 格式組成的目錄，是該 space wiki 的正本。
_Avoid_: wiki 資料夾、vault

**Concept page**:
以一個概念（原理、方法、量測指標）為單位、依共用模板撰寫的 space wiki page。

**Entity page**:
以一個專有對象（設備、光罩、材料、軟體工具）為單位的 space wiki page。

**Course summary page**:
一門課程的摘要 space wiki page。
_Avoid_: source page（易與 SourcePage 混淆）

**Synthesis page**:
同一 space 內跨多門課程的整合分析或比較頁面。
_Avoid_: comparison page

**Filed answer**:
使用者主動歸檔的查詢回答，labels 為提問者當下的全部可讀 space，存放在 space wiki 之外。
_Avoid_: 審核專區、共同區域、saved query

### 操作

**Sync**:
從平台抓取 SourcePage、加密存放並發出事件；不做任何 LLM 處理。

**Ingest**:
在單一 space 內，把一個 SourcePage 的變更整合進該 space 的 wiki、索引與圖。
_Avoid_: compile

**Lint**:
在單一 space 內定期檢查 wiki 的矛盾、過時 claim、孤兒頁與缺漏連結。

**View**:
依讀者權限從多個 space wiki page 合成、只以快取形式存在的頁面。
_Avoid_: 跨部門頁、合併頁
