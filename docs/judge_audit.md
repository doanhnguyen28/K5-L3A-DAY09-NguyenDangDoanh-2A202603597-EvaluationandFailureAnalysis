# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (FAIL = lớp dương: judge có nhiệm vụ *tìm* lỗi).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** — confusion tp 24 · fp 4 · fn 6 · tn 66 | **dùng** (chỉ cho tone, không cho tiền) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** (κ, R) — tp 9 · fp 0 · fn 21 · tn 70 | **loại** |

Rogan-Gladen: tone — tỉ lệ FAIL quan sát 0.28 → hiệu chỉnh 0.30. helpful — quan sát 0.09 → hiệu chỉnh 0.30: judge helpful đếm thiếu lỗi hơn 3 lần.

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã với khách khiếu nại. Rubric thêm: *xưng hô phải là "LogiX" / "anh/chị"; "em/bạn/nha/nhé" ⇒ FAIL*. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ … rồi nha." | PASS | FAIL | Người | Cùng lỗi xưng hô ("bạn ơi"). Thêm 2 ví dụ few-shot FAIL về xưng hô vào prompt judge. |
| 3 | H-029 "Dạ, vui lòng chờ. … không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ bắt từ lịch sự bề mặt ("Dạ, vui lòng"). Rubric: *câu mệnh lệnh / chặn khách liên hệ ⇒ FAIL dù có kính ngữ*. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì "thiếu xin lỗi". Rubric: *xin lỗi không bắt buộc khi đã giải quyết xong; ngắn gọn, đủ ý = PASS*. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ." | FAIL | PASS | Người | Style guide CS LogiX cho phép "refund". Rubric thêm whitelist thuật ngữ tiếng Anh được phép. |
| 6 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn … ạ." | FAIL | PASS | **Judge** | Nhãn người sai (đọc nhầm ticket). Hành động: **sửa nhãn người** thành FAIL, ghi lại trong lịch sử nhãn; cần người thứ hai duyệt nhãn. |
| 7 | H-070 "… đã hoàn 90.000đ cho đơn O-909 ạ 😂😂" | PASS | FAIL | Người | Emoji cười trong khiếu nại. Rubric: *emoji ⇒ FAIL*. |
| 8 | H-094 "Da, LogiX da hoan 40.000đ … xin loi vi su bat tien." | PASS | FAIL | Người | Mất dấu tiếng Việt (dịch máy). Rubric: *tiếng Việt không dấu ⇒ FAIL*. |

Ngoài ra H-063 ("sẽ hoàn thêm" — hứa ngoài kết quả hệ thống) là lỗi **nội dung**, không phải tone: không giao cho judge tone mà để grader code (FM-08 / chính sách) bắt. H-088 ("Dạ, dạ" lặp do gõ nhầm): ranh giới, giữ nhãn người PASS.

## Vì sao judge `helpful` không được dùng làm gate
- Recall 0.30: trong 30 reply người chấm FAIL, judge chỉ bắt được 9; **21/21 lỗi bị bỏ sót đều là judge PASS**.
- Mẫu bỏ sót rất rõ: (a) reply **nói sai số tiền so với ledger** (HL-015 nói 55.000đ, ledger 40.000đ; HL-020 nói 90.000đ, ledger 120.000đ; HL-100 …) — judge không nhìn thấy ledger nên không thể chấm; (b) reply **dài, xin lỗi lặp lại** (HL-016, HL-028, HL-035 …) — judge bị "độ dài/độ lịch sự" đánh lừa (verbosity bias); (c) reply cụt "Ok bạn, đơn … xử lý rồi nhé" (HL-047, HL-091, HL-097).
- Precision 1.0 không cứu được: một gate với recall 0.3 sẽ cho qua ~70% reply có hại; κ 0.375 < 0.6.
- Thay thế: phần "đúng số tiền" của helpful đã được tách thành grader code **`reply_amount_matches_ledger` (FM-08)** — tiền luôn chấm bằng code. Phần còn lại (ngắn gọn, đủ ý) chỉ theo dõi, không chặn, cho tới khi judge mới (có ledger trong context + rubric phạt lặp/dài) đạt FLOORS trên tập held-out mới.

## Kết luận
- **tone_ok: dùng** (κ 0.757 ≥ 0.6, P 0.857 ≥ 0.8, R 0.80 ≥ 0.7), sau khi sửa rubric theo các case 1–5, 7, 8 và sửa nhãn H-081; phải calibrate lại sau khi đổi rubric.
- **helpful: loại** — recall 0.30, κ 0.375 (ma trận: fn 21 vs tp 9).
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật. Judge tone chỉ được dùng cho giọng văn, không bao giờ cho tiền hay approval.
