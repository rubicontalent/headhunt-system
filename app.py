import os
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
import pdfplumber
from docx import Document

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'headhunt.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.secret_key = "dev-secret"

db = SQLAlchemy(app)


class Candidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(80))
    industry = db.Column(db.String(120))
    experience = db.Column(db.Float)
    salary = db.Column(db.Float)
    date_of_birth = db.Column(db.String(30))
    cv_filename = db.Column(db.String(255))


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


def extract_text_from_cv(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        with pdfplumber.open(path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    if ext == ".docx":
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    return ""


def parse_cv(text: str) -> dict:
    data = {"name": "", "email": "", "phone": "", "industry": "", "experience": None, "salary": None, "date_of_birth": ""}

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        data["name"] = lines[0][:200]

    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email:
        data["email"] = email.group(0)

    phone = re.search(r"(\+?\d[\d\s\-()]{7,}\d)", text)
    if phone:
        data["phone"] = re.sub(r"\s+", " ", phone.group(0)).strip()

    industry_keywords = ["finance", "banking", "technology", "it", "healthcare", "retail", "manufacturing", "construction"]
    lowered = text.lower()
    for key in industry_keywords:
        if key in lowered:
            data["industry"] = key.title()
            break

    exp = re.search(r"(\d{1,2}(?:\.\d+)?)\+?\s*(?:years|yrs)\s*(?:of)?\s*experience", lowered)
    if exp:
        data["experience"] = float(exp.group(1))

    salary = re.search(r"(?:salary|expected salary)[:\s]*[A-Z$HKD\s]*([\d,]{4,})", text, re.IGNORECASE)
    if salary:
        data["salary"] = float(salary.group(1).replace(",", ""))

    dob = re.search(r"(?:date of birth|dob)[:\s]*([0-3]?\d[/-][01]?\d[/-]\d{2,4})", text, re.IGNORECASE)
    if dob:
        data["date_of_birth"] = dob.group(1)

    return data


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/candidates", methods=["GET", "POST"])
def candidates():
    if request.method == "POST":
        file = request.files.get("cv_file")
        if not file or file.filename == "":
            flash("Please upload a PDF or DOCX CV.")
            return redirect(url_for("candidates"))

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".pdf", ".docx"]:
            flash("Only PDF and DOCX are supported.")
            return redirect(url_for("candidates"))

        file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
        file.save(file_path)

        parsed = parse_cv(extract_text_from_cv(file_path))
        candidate = Candidate(**parsed, cv_filename=file.filename)
        db.session.add(candidate)
        db.session.commit()
        flash("Candidate added and CV parsed successfully.")
        return redirect(url_for("candidates"))

    return render_template("candidates.html", candidates=Candidate.query.order_by(Candidate.id.desc()).all())


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

        fee = 0
        if status.lower() == "successful" and job:
            fee = offered_salary * ((job.client.fee_percentage or 0) / 100)

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
    app.run(debug=True)
