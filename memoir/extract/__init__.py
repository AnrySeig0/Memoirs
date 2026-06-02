"""Step 4: Extraction → grounded claims.

LLM structured output: mỗi lần 1 mệnh đề + nguồn + confidence.
Ép schema bằng Outlines / Instructor. Thiết kế để DƯỚI-trích và flag,
không đoán. Claim không có ≥1 grounding source → loại/flag.
"""
