# Knowledge Center starter

把這些檔案放進新 repo 的根目錄即可開始 M0。

1. 將合成測試集解壓到 `tests/fixtures/synthetic_litho_testset/`。
2. 若你的 Claude Code 環境已安裝舊的 `secure-llm-wiki` skill 或 plugin，請移除或停用。它禁止跨 space view、使用 Common／部門分區，並包含人工審核步驟，與本專案的 ADR 衝突。
3. 以 `claude` 開啟 repo，第一個 session 請它閱讀 CLAUDE.md、docs/ 與 tests/fixtures 的 README，用 plan mode 規劃 M0。

檔案說明：
- `CLAUDE.md`：每個 session 載入的不變式與開發規則
- `docs/architecture.md`：完整架構
- `docs/adr/`：五個關鍵決策
- `.claude/skills/secure-wiki/`：開發指南與洩漏模式（取代舊 skill）
- `.claude/agents/leak-reviewer.md`：唯讀的安全審查 subagent
