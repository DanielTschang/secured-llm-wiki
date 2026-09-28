# OPC 實務入門（2025 版）

## 1. 為什麼需要 OPC

![](attachments/opc_schematic.png)

光學鄰近效應會讓線端縮短、轉角變圓，所以要在光罩上預先補償。

---

## 2. Hotspot 檢查規則（更新）

![](attachments/o2_rules_2025.png)

今年起 MEEF 門檻收緊，其餘規則不變。

---

## 3. 模型校正

本版模型校正 RMS 目標 < 1.0 nm。

![](attachments/o2_residual.png)

---

## 4. Contact layer 的模型假設

OPC 模型在 contact layer 採用的 MEEF 假設值為 2.6。

實際量測可參考 [CD 團隊的 contact 量測頁](platform://pages/cd_d1)。

{{include page="cd_d1" section="4"}}

---

## 5. 小結

- MEEF hotspot 門檻由 3.0 收緊為 2.5
- 模型更新為 KESTREL-7，RMS 目標 < 1.0 nm
