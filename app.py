import json
import os
import re
from datetime import datetime

import pdfplumber
from docx import Document
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
from sqlalchemy import or_
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'headhunt.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-prod")

db = SQLAlchemy(app)


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


ALLOWED_EXTS = {".pdf", ".docx"}
INDUSTRIES = [
    "Technology", "Finance", "Banking", "Insurance", "Healthcare", "Pharmaceutical",
    "Retail", "E-commerce", "Manufacturing", "Logistics", "Construction", "Real Estate",
    "Education", "Telecommunications", "Hospitality", "FMCG", "Legal", "Media", "Other",
]
JOB_FUNCTIONS = [
    "Sales", "Engineering", "Finance", "HR", "Marketing", "Operations", "Product", "IT",
    "Customer Service", "Administration", "Legal", "Procurement", "Supply Chain", "Other",
]


class Candidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, default="Unknown")
    email = db.Column(db.String(120))
    phone = db.Column(db.String(80))
    industry = db.Column(db.String(120))
    job_function = db.Column(db.String(120))
    experience = db.Column(db.Float)
    salary = db.Column(db.Float)
    date_of_birth = db.Column(db.String(30))
    cv_filename = db.Column(db.String(255))
    parsed_json = db.Column(db.Text)
    status = db.Column(db.String(50), default="New")
    source = db.Column(db.String(120), default="CV Upload")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), nullable=False)
    hr_contact = db.Column(db.String(200))
    fee_percentage = db.Column(db.Float, default=0)
    contract_notes = db.Column(db.Text)


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    salary_min = db.Column(db.Float)
    salary_max = db.Column(db.Float)
    status = db.Column(db.String(50), default="Open")
    client = db.relationship("Client", backref="jobs")


class Placement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    status = db.Column(db.String(50), default="Submitted")
    offered_salary = db.Column(db.Float)
    recruitment_fee = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    candidate = db.relationship("Candidate", backref="placements")
    job = db.relationship("Job", backref="placements")


def safe_filename(filename: str) -> str:
    base, ext = os.path.splitext(filename)
    return re.sub(r"[^a-zA-Z0-9_-]", "_", base) + ext.lower()


def extract_text_from_cv(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        with pdfplumber.open(path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    if ext == ".docx":
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    return ""


def fallback_parse(text: str) -> dict:
    data = {
        "name": "Unknown", "email": "", "phone": "", "industry": "Other", "job_function": "Other",
        "years_of_experience": None, "salary": None, "date_of_birth": "",
    }
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        data["name"] = lines[0][:200]
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        data["email"] = email.group(0)
    phone = re.search(r"(\+?\d[\d\s\-()]{7,}\d)", text)
    if phone:
        data["phone"] = re.sub(r"\s+", " ", phone.group(0)).strip()
    exp = re.search(r"(\d{1,2}(?:\.\d+)?)\+?\s*(?:years|yrs)", text, re.IGNORECASE)
    if exp:
        data["years_of_experience"] = float(exp.group(1))
    salary = re.search(r"(?:salary|expected salary)[:\s]*[A-Z$HKD\s]*([\d,]{4,})", text, re.IGNORECASE)
    if salary:
        data["salary"] = float(salary.group(1).replace(",", ""))
    dob = re.search(r"(?:date of birth|dob)[:\s]*([0-3]?\d[/-][01]?\d[/-]\d{2,4})", text, re.IGNORECASE)
    if dob:
        data["date_of_birth"] = dob.group(1)
    return data


def ai_parse_cv(text: str) -> dict:
    client = get_openai_client()
    if not client:
        return fallback_parse(text)

    prompt = f"""
Extract candidate data from CV text and return strict JSON only with keys:
name, email, phone, industry, job_function, years_of_experience, salary, date_of_birth
Industry must be one of: {INDUSTRIES}
Job function must be one of: {JOB_FUNCTIONS}
If unknown use empty string for strings and null for numbers.
CV text:\n{text[:15000]}
"""

    for _ in range(2):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
                timeout=40,
            )
            payload = json.loads(resp.choices[0].message.content)
            payload.setdefault("industry", "Other")
            payload.setdefault("job_function", "Other")
            if payload.get("industry") not in INDUSTRIES:
                payload["industry"] = "Other"
            if payload.get("job_function") not in JOB_FUNCTIONS:
                payload["job_function"] = "Other"
            return payload
        except Exception:
            continue
    return fallback_parse(text)


def save_candidate_from_cv(file_obj):
    ext = os.path.splitext(file_obj.filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        return False, f"Skipped {file_obj.filename}: unsupported file type"
    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{safe_filename(file_obj.filename)}"
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file_obj.save(file_path)
    text = extract_text_from_cv(file_path)
    parsed = ai_parse_cv(text)

    candidate = Candidate(
        name=(parsed.get("name") or "Unknown")[:200],
        email=(parsed.get("email") or "")[:120],
        phone=(parsed.get("phone") or "")[:80],
        industry=parsed.get("industry") or "Other",
        job_function=parsed.get("job_function") or "Other",
        experience=parsed.get("years_of_experience"),
        salary=parsed.get("salary"),
        date_of_birth=(parsed.get("date_of_birth") or "")[:30],
        cv_filename=filename,
        parsed_json=json.dumps(parsed, ensure_ascii=False),
        status="New",
        source="CV Upload",
    )
    db.session.add(candidate)
    return True, f"Uploaded {file_obj.filename}"






def ai_search_candidates(query_text, candidates):
    if not query_text.strip() or not candidates:
        return []

    client = get_openai_client()
    if not client:
        q = query_text.lower()
        return [c for c in candidates if q in (c.name or '').lower() or q in (c.industry or '').lower() or q in (c.job_function or '').lower()][:50]

    candidate_payload = [
        {"id": c.id, "name": c.name, "industry": c.industry, "job_function": c.job_function, "experience": c.experience, "status": c.status, "notes": c.notes}
        for c in candidates
    ]
    prompt = {
        "query": query_text,
        "instruction": "Find best matching candidates for this recruiter search query. Return JSON {results:[{candidate_id,score,reason}]} max 50.",
        "candidates": candidate_payload,
    }
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            timeout=45,
        )
        data = json.loads(resp.choices[0].message.content)
        c_map = {c.id: c for c in candidates}
        ranked = []
        for row in data.get("results", []):
            cid = row.get("candidate_id")
            if cid in c_map:
                ranked.append((c_map[cid], int(row.get("score", 0)), (row.get("reason") or "AI matched")[:120]))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [r[0] for r in ranked[:50]]
    except Exception:
        return candidates[:50]

def ai_rank_candidates(job, candidates, job_brief=""):
    client = get_openai_client()
    if not candidates:
        return []

    payload_candidates = [
        {"id": c.id, "name": c.name, "industry": c.industry, "job_function": c.job_function, "experience": c.experience, "parsed_json": c.parsed_json}
        for c in candidates
    ]

    if not client:
        scored = []
        for c in candidates:
            score = 0
            if c.job_function and job.title and c.job_function.lower() in job.title.lower():
                score += 40
            if c.industry and job.title and c.industry.lower() in job.title.lower():
                score += 20
            if c.experience:
                score += min(40, int(c.experience * 4))
            scored.append({"candidate": c, "score": min(score, 95), "reason": "Rule-based fallback ranking"})
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:20]

    prompt = {
        "job": {"title": job.title, "salary_min": job.salary_min, "salary_max": job.salary_max, "status": job.status, "brief": job_brief},
        "candidates": payload_candidates,
        "instruction": "Return JSON with key matches as list of up to 20 items. Each item: candidate_id(int), score(0-100), reason(short)."
    }
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            timeout=60,
        )
        data = json.loads(resp.choices[0].message.content)
        mapped = {c.id: c for c in candidates}
        out = []
        for m in data.get("matches", []):
            cid = m.get("candidate_id")
            if cid in mapped:
                out.append({
                    "candidate": mapped[cid],
                    "score": max(0, min(int(m.get("score", 0)), 100)),
                    "reason": (m.get("reason") or "AI matched").strip()[:180],
                })
        out = sorted(out, key=lambda x: x["score"], reverse=True)
        return out[:20]
    except Exception:
        scored = []
        for c in candidates:
            score = 0
            if c.job_function and job.title and c.job_function.lower() in job.title.lower():
                score += 40
            if c.industry and job.title and c.industry.lower() in job.title.lower():
                score += 20
            if c.experience:
                score += min(40, int(c.experience * 4))
            scored.append({"candidate": c, "score": min(score, 95), "reason": "Fallback ranking after AI error"})
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:20]


@app.route("/ai-match", methods=["GET", "POST"])
def ai_match():
    jobs = Job.query.order_by(Job.id.desc()).all()
    matches = []
    selected_job = None
    job_brief = ""

    if request.method == "POST":
        job_id = int(request.form.get("job_id"))
        job_brief = request.form.get("job_brief", "")
        selected_job = Job.query.get(job_id)
        if selected_job:
            candidates = Candidate.query.order_by(Candidate.id.desc()).limit(500).all()
            matches = ai_rank_candidates(selected_job, candidates, job_brief)
            if not matches:
                flash("AI matching did not return results, please refine job brief.")

    return render_template("ai_match.html", jobs=jobs, matches=matches, selected_job=selected_job, job_brief=job_brief)

@app.route("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/candidates", methods=["GET", "POST"])
def candidates():
    if request.method == "POST":
        files = request.files.getlist("cv_files")
        if not files:
            flash("Please upload CV files.")
            return redirect(url_for("candidates"))

        count_success = 0
        count_failed = 0
        for f in files[:100]:
            if not f or f.filename == "":
                continue
            ok, msg = save_candidate_from_cv(f)
            if ok:
                count_success += 1
            else:
                count_failed += 1
                flash(msg)
        db.session.commit()
        flash(f"Bulk upload completed: {count_success} success, {count_failed} failed.")
        return redirect(url_for("candidates"))

    q = request.args.get("q", "").strip()
    industry = request.args.get("industry", "").strip()
    job_function = request.args.get("job_function", "").strip()
    status = request.args.get("status", "").strip()
    ai_q = request.args.get("ai_q", "").strip()

    query = Candidate.query
    if q:
        like_q = f"%{q}%"
        query = query.filter(or_(Candidate.name.ilike(like_q), Candidate.email.ilike(like_q), Candidate.phone.ilike(like_q)))
    if industry:
        query = query.filter(Candidate.industry == industry)
    if job_function:
        query = query.filter(Candidate.job_function == job_function)
    if status:
        query = query.filter(Candidate.status == status)

    result_candidates = query.order_by(Candidate.id.desc()).limit(500).all()
    if ai_q:
        result_candidates = ai_search_candidates(ai_q, result_candidates)

    return render_template(
        "candidates.html",
        candidates=result_candidates,
        industries=INDUSTRIES,
        job_functions=JOB_FUNCTIONS,
        q=q,
        selected_industry=industry,
        selected_job_function=job_function,
        selected_status=status,
        statuses=["New","Screening","Interview","Shortlisted","Placed","Rejected"],
        ai_q=ai_q,
    )


@app.route("/candidates/<int:candidate_id>/edit", methods=["GET", "POST"])
def edit_candidate(candidate_id):
    c = Candidate.query.get_or_404(candidate_id)
    if request.method == "POST":
        c.name = request.form.get("name", "Unknown")[:200]
        c.email = request.form.get("email", "")[:120]
        c.phone = request.form.get("phone", "")[:80]
        c.industry = request.form.get("industry", "Other")
        c.job_function = request.form.get("job_function", "Other")
        c.experience = float(request.form.get("experience") or 0) if request.form.get("experience") else None
        c.salary = float(request.form.get("salary") or 0) if request.form.get("salary") else None
        c.date_of_birth = request.form.get("date_of_birth", "")[:30]
        c.status = request.form.get("status", "New")
        c.source = request.form.get("source", "CV Upload")[:120]
        c.notes = request.form.get("notes", "")
        c.parsed_json = json.dumps({
            "name": c.name, "email": c.email, "phone": c.phone, "industry": c.industry,
            "job_function": c.job_function, "years_of_experience": c.experience,
            "salary": c.salary, "date_of_birth": c.date_of_birth,
        }, ensure_ascii=False)
        db.session.commit()
        flash("Candidate updated.")
        return redirect(url_for("candidates"))
    return render_template("edit_candidate.html", c=c, industries=INDUSTRIES, job_functions=JOB_FUNCTIONS, statuses=["New","Screening","Interview","Shortlisted","Placed","Rejected"])


@app.route("/clients", methods=["GET", "POST"])
def clients():
    if request.method == "POST":
        client = Client(
            company_name=request.form["company_name"],
            hr_contact=request.form.get("hr_contact"),
            fee_percentage=float(request.form.get("fee_percentage") or 0),
            contract_notes=request.form.get("contract_notes"),
        )
        db.session.add(client)
        db.session.commit()
        return redirect(url_for("clients"))
    return render_template("clients.html", clients=Client.query.order_by(Client.id.desc()).all())


@app.route("/jobs", methods=["GET", "POST"])
def jobs():
    clients = Client.query.order_by(Client.company_name.asc()).all()
    if request.method == "POST":
        job = Job(
            client_id=int(request.form["client_id"]),
            title=request.form["title"],
            salary_min=float(request.form.get("salary_min") or 0),
            salary_max=float(request.form.get("salary_max") or 0),
            status=request.form["status"],
        )
        db.session.add(job)
        db.session.commit()
        return redirect(url_for("jobs"))
    jobs_list = Job.query.order_by(Job.id.desc()).all()
    return render_template("jobs.html", jobs=jobs_list, clients=clients)


@app.route("/placements", methods=["GET", "POST"])
def placements():
    candidates = Candidate.query.order_by(Candidate.name.asc()).all()
    jobs_list = Job.query.order_by(Job.title.asc()).all()

    if request.method == "POST":
        job = Job.query.get(int(request.form["job_id"]))
        offered_salary = float(request.form.get("offered_salary") or 0)
        status = request.form["status"]
        fee = offered_salary * ((job.client.fee_percentage or 0) / 100) if status.lower() == "successful" and job else 0

        placement = Placement(
            candidate_id=int(request.form["candidate_id"]),
            job_id=int(request.form["job_id"]),
            status=status,
            offered_salary=offered_salary,
            recruitment_fee=fee,
        )
        db.session.add(placement)
        db.session.commit()
        return redirect(url_for("placements"))

    placement_list = Placement.query.order_by(Placement.id.desc()).all()
    return render_template("placements.html", placements=placement_list, candidates=candidates, jobs=jobs_list)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        cols = {c[1] for c in db.session.execute(db.text("PRAGMA table_info(candidate)")).fetchall()}
        if "job_function" not in cols:
            db.session.execute(db.text("ALTER TABLE candidate ADD COLUMN job_function VARCHAR(120)"))
        if "parsed_json" not in cols:
            db.session.execute(db.text("ALTER TABLE candidate ADD COLUMN parsed_json TEXT"))
        if "status" not in cols:
            db.session.execute(db.text("ALTER TABLE candidate ADD COLUMN status VARCHAR(50) DEFAULT 'New'"))
        if "source" not in cols:
            db.session.execute(db.text("ALTER TABLE candidate ADD COLUMN source VARCHAR(120) DEFAULT 'CV Upload'"))
        if "notes" not in cols:
            db.session.execute(db.text("ALTER TABLE candidate ADD COLUMN notes TEXT"))
        if "created_at" not in cols:
            db.session.execute(db.text("ALTER TABLE candidate ADD COLUMN created_at DATETIME"))
        db.session.commit()
    app.run(debug=True)
