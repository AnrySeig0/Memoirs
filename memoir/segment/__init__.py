"""Step 3: Segmentation — cắt theo lượt nói + ngữ cảnh, giữ nguyên offset.

Lỗi âm thầm hay gặp: chunking làm lệch offset → mất truy vết.
Dùng Unicode codepoint nhất quán giữa lưu trữ và hiển thị.
"""
