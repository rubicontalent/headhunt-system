# Headhunt CRM (Simple Flask Version)

一個簡單本地可運行的 headhunt CRM web app，包含：
- Candidate 管理（CV 上傳 + PDF/DOCX 解析 + 自動填充）
- Client 管理
- Job 管理
- Placement tracking（成功後自動計 fee）

## Tech Stack
- Python + Flask
- SQLite (本地 DB)
- Flask-SQLAlchemy
- pdfplumber + python-docx (CV 文字提取)

## 本地運行
```bash
python -m venv .venv
source .venv/bin/activate  # Windows 用 .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

然後打開：`http://127.0.0.1:5000`

## 功能說明

### 1) Candidate 管理
- `/candidates` 頁可上傳 PDF/DOCX CV
- 系統會自動解析並提取：
  - name
  - email
  - phone
  - industry
  - experience
  - salary
  - date of birth
- 解析後直接存入 SQLite

### 2) Client 管理
- company name
- HR contact
- fee percentage
- contract notes

### 3) Job 管理
- 對應 client
- job title
- salary range
- status

### 4) Placement tracking
- 記錄 candidate 對應 job
- 如果 status = `Successful`，會根據 client fee % 自動計 recruitment fee：
  - `offered_salary * (fee_percentage / 100)`

## 注意
- CV parsing 係 rule-based regex，屬簡化版，方便你之後再接 OpenAI/LLM 做更準確 extraction。
