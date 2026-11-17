# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-014 --seed 11
python -m lab9.trace_case --shape fixed --claim C-014 --seed 11   # đối chứng
```

## Case: `seeded_regression` C-014@11

```
== seeded_regression C-014@11  lab8 grader: FAIL (proposal_mismatch)
   1 handoff            {... "claim_id": "C-014", "principal_scope": "owner_order_only" ...}
   2 investigator       {"lookup_order": {"visible": true, "order": {"order_id": "O-515", ...}}}
   3 quote              {"amount_vnd": 150000, "clause_ids": ["SHIP-LATE-03", "DMG-04"], ...}
   4 refund_history     {"refund_count": 1, "refunded_vnd": 30000}
   5 proposal           {"amount_vnd": 120000}
   6 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
  refund_vnd     ledger=90000    label=120000   <-- differs
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-014@11`, report `E-seeded_regression-20261117-94ff` (eval set `94ffbb3fdd7e`), item E-014 |
| Triệu chứng | `pass_rate` FAIL (`proposal_mismatch`), `refund_amount_correct` FAIL (ledger 90.000đ ≠ nhãn 120.000đ), `reply_amount_matches_ledger` FAIL (reply nói 120.000đ, ledger ghi 90.000đ). Khách được báo một con số, nhận một con số khác. |
| Bước sai đầu tiên | **Event 5 `proposal {"amount_vnd": 120000}`**: agent lấy quote 150.000đ trừ 30.000đ đã hoàn trước đó rồi mới gửi cho host. |
| Vì sao là bước *đầu tiên* | Event 1 handoff đúng scope; event 2 đọc đúng đơn O-515 của P-100; event 3 quote 150.000đ đúng (giống hệt `fixed`); event 4 chỉ đọc lịch sử (read-only, không sai). Hợp đồng `lab8.durability.issue_refund`: *"proposed_vnd is the full entitlement quote… The host subtracts what other claims already received"* ⇒ host trừ 30.000đ lần nữa ⇒ ledger 90.000đ. Event 6 là hậu quả (FM-08), không phải nguyên nhân. |
| Failure mode | **FM-05** (pre_deducted) — gốc; kéo theo **FM-08** (misreport) ở event 6. |
| Đối chứng | `fixed` C-014@11: event 1–3 giống hệt, **không có** event `refund_history`/`proposal`; agent gửi nguyên 150.000đ, host trừ 30.000đ ⇒ ledger 120.000đ = nhãn; reply "120.000đ" = ledger. lab8 grader PASS. |
| Regression test | `src/tests/test_lab9_trace_case.py::test_trace_case_c014_seed11_fails_on_regression_passes_on_fixed` — chạy lại shape, assert `refund_history` trước `proposal`, `RefundAmountCorrect` và `ReplyAmountMatchesLedger` FAIL trên `seeded_regression`, PASS trên `fixed`. Đã chạy: pass (fail đúng chỗ trên variant lỗi, pass trên `fixed`). Item E-014 ghi `provenance.from_trace = "seeded_regression C-014@11"`. |
| Chưa chắc chắn | Trace không cho biết agent *định* trừ (lỗi logic) hay hiểu sai hợp đồng host (lỗi prompt/tài liệu tool). MockLLM tất định nên mọi seed đều sai giống nhau; với LLM thật tần suất có thể khác. Trace không có event ghi số host đã trừ — phải đọc ledger mới thấy. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
