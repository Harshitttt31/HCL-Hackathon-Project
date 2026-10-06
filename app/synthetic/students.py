"""Seeded generator for courses, students, attendance and results (Annex C schema).

Deterministic: the same seed gives byte-identical CSV files. Ground truth for the evaluation is derived from these files by
independent arithmetic in app/evaluation/cases.py, never by calling the system under test.

Eight designed students (S1001 to S1008) sit on decision boundaries (exactly 75%, exactly 80%, exactly 65%, 74.58%, a CGPA of exactly
6.50 with exactly 1 backlog, three used attempts, and so on). The remaining students are random.
"""
from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Optional

AS_OF_YEAR = 2026  # the reference date of the corpus is 2026-10-06 (an odd semester is running)

COURSE_NAMES = {
    "B.Tech CSE": {1: ["Programming Fundamentals", "Engineering Mathematics I", "Engineering Physics", "Communication Skills"],
                   2: ["Engineering Mathematics II", "Digital Electronics", "Environmental Studies", "Problem Solving with Python"],
                   3: ["Data Structures", "Discrete Mathematics", "Computer Organization", "Object Oriented Programming"],
                   4: ["Design and Analysis of Algorithms", "Database Systems", "Probability and Statistics", "Microprocessors"],
                   5: ["Operating Systems", "Computer Networks", "Theory of Computation", "Software Engineering"],
                   6: ["Compiler Design", "Machine Learning", "Information Security", "Web Technologies"],
                   7: ["Distributed Systems", "Cloud Computing", "Data Mining", "Professional Elective I"],
                   8: ["Professional Elective II", "Open Elective", "Project Work Phase II", "Seminar"]},
    "B.Tech ECE": {1: ["Programming Fundamentals", "Engineering Mathematics I", "Engineering Physics", "Communication Skills"],
                   2: ["Engineering Mathematics II", "Basic Electrical Engineering", "Environmental Studies", "Circuit Theory"],
                   3: ["Analog Electronics", "Signals and Systems", "Electromagnetic Theory", "Digital Circuits"],
                   4: ["Communication Systems", "Linear Integrated Circuits", "Control Systems", "Microcontrollers"],
                   5: ["Digital Signal Processing", "VLSI Design", "Antenna Theory", "Embedded Systems"],
                   6: ["Wireless Communication", "Optical Communication", "Radar Systems", "IoT Systems"],
                   7: ["Satellite Communication", "Network Security", "Professional Elective I", "Open Elective"],
                   8: ["Professional Elective II", "Project Work Phase II", "Seminar", "Industrial Training"]},
    "M.Tech CSE": {1: ["Advanced Algorithms", "Research Methodology", "Advanced Databases", "Parallel Computing"],
                   2: ["Advanced Machine Learning", "Cloud Architecture", "Elective I", "Elective II"],
                   3: ["Elective III", "Dissertation Phase I", "Seminar", "Technical Writing"],
                   4: ["Dissertation Phase II", "Elective IV", "Industrial Project", "Viva Voce"]},
}
DEPT = {"B.Tech CSE": "CS", "B.Tech ECE": "EC", "M.Tech CSE": "MC"}
MAX_SEM = {"B.Tech CSE": 8, "B.Tech ECE": 8, "M.Tech CSE": 4}

FIRST = ["Asha", "Rahul", "Meera", "Karan", "Priya", "Vikram", "Neha", "Arjun", "Divya", "Rohan", "Sneha", "Aditya", "Kavya", "Nikhil", "Pooja", "Siddharth",
         "Ananya", "Harsh", "Ishita", "Manish", "Tanvi", "Varun", "Shruti", "Kunal", "Lakshmi", "Gaurav", "Swati", "Pranav", "Deepa", "Yash", "Nandini", "Tarun",
         "Rekha", "Abhishek", "Bhavna", "Chirag", "Disha", "Ekta", "Farhan", "Gitanjali", "Hemant", "Indira", "Jatin", "Komal", "Lokesh", "Mansi", "Naveen",
         "Ojas", "Payal", "Qadir", "Ritika", "Sameer", "Trisha", "Uday", "Vidya", "Waseem", "Yamini", "Zubin", "Aarti", "Bharat"]
LAST = ["Verma", "Menon", "Iyer", "Shah", "Nair", "Rao", "Kulkarni", "Das", "Mehta", "Joshi", "Bose", "Reddy", "Pillai", "Chopra", "Desai", "Banerjee",
        "Kapoor", "Naidu", "Saxena", "Trivedi", "Bhatt", "Sinha", "Malhotra", "Gill", "Hegde", "Patil", "Yadav", "Thakur", "Ghosh", "Krishnan", "Sharma",
        "Agarwal", "Jain", "Rastogi", "Mishra", "Pandey", "Dubey", "Tiwari", "Chauhan", "Rathore", "Solanki", "Parekh", "Vyas", "Goswami", "Sethi", "Anand",
        "Bajaj", "Khanna", "Lal", "Mathur", "Oberoi", "Puri", "Sodhi", "Tandon", "Uppal", "Walia", "Zaveri", "Biswas", "Chandra", "Dutta"]

GRADE_BANDS = [(90, "O", 10), (80, "A", 9), (70, "B", 8), (60, "C", 7), (50, "D", 6), (40, "E", 5), (0, "F", 0)]


@dataclass
class Course:
    course_code: str
    course_name: str
    programme: str
    semester: int
    credits: int


@dataclass
class Student:
    student_id: str
    full_name: str
    programme: str
    batch_year: int
    current_semester: int
    cgpa: Optional[float] = None
    active_backlogs: int = 0


@dataclass
class Dataset:
    courses: list[Course] = field(default_factory=list)
    students: list[Student] = field(default_factory=list)
    attendance: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    designed: dict[str, str] = field(default_factory=dict)  # student_id -> scenario description
    cgpa_record_only: set[str] = field(default_factory=set)  # stored CGPA intentionally not derivable from the results table


def code_for(programme: str, semester: int, j: int) -> str:
    """CS201 = third semester, first course. Semesters 3 and 4 share the hundreds digit 2."""
    level = (semester + 1) // 2
    k = ((semester - 1) % 2) * 4 + j
    return f"{DEPT[programme]}{level}{k:02d}"


def build_courses() -> list[Course]:
    out = []
    for prog, sems in COURSE_NAMES.items():
        for sem, names in sems.items():
            for j, name in enumerate(names, 1):
                out.append(Course(code_for(prog, sem, j), name, prog, sem, 4 if j <= 2 else 3))
    return out


def grade_points(total: int, max_marks: int = 100) -> int:
    pct = Fraction(total * 100, max_marks)
    for lo, _, gp in GRADE_BANDS:
        if pct >= lo:
            return gp
    return 0


def session_regular(batch: int, sem: int) -> str:
    year = batch + sem // 2  # sem1 -> batch-DEC, sem2 -> batch+1-MAY, sem3 -> batch+1-DEC ...
    return f"{year}-{'DEC' if sem % 2 == 1 else 'MAY'}"


def session_supp(batch: int, sem: int, n: int) -> str:
    """n-th supplementary session after the regular one: the January or July window of the following cycle."""
    year = batch + sem // 2
    if sem % 2 == 1:  # DEC regular -> JAN of next year, then JUL
        return [f"{year + 1}-JAN", f"{year + 1}-JUL", f"{year + 2}-JAN"][n - 1]
    return [f"{year}-JUL", f"{year + 1}-JAN", f"{year + 1}-JUL"][n - 1]


def cgpa_from(entries: list[tuple[int, int]]) -> Optional[float]:
    """entries: (credits, grade_points) of each course's latest attempt. Half-up rounding to 2 decimals."""
    total = sum(c for c, _ in entries)
    if total == 0:
        return None
    val = Fraction(sum(c * g for c, g in entries), total)
    return float((Decimal(val.numerator) / Decimal(val.denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _marks(rng: random.Random, fail: bool) -> tuple[int, int, int]:
    if fail:
        total = rng.randint(18, 39)
        internal = min(40, max(5, int(total * rng.uniform(0.3, 0.45))))
        external = total - internal
        if external > 60:
            external = 60
            internal = total - external
        return internal, external, total
    internal = int(min(40, max(14, rng.gauss(29, 5))))
    external = int(min(60, max(max(0, 40 - internal), rng.gauss(41, 9))))
    return internal, external, internal + external


def _attendance_pair(rng: random.Random, low: float, high: float) -> tuple[int, int]:
    held = rng.randint(36, 60)
    attended = max(0, min(held, round(held * rng.uniform(low, high))))
    return held, attended


# designed scenarios: id -> (name, programme, batch, attendance overrides {course: (held, attended)}, extra results, record cgpa, note)
def designed_specs() -> list[dict]:
    return [
        dict(id="S1001", name="Asha Verma", prog="B.Tech CSE", batch=2024,
             att={"CS301": (40, 31), "CS302": (50, 40), "CS303": (40, 33), "CS304": (45, 40)},
             note="CS301: 31/40 = 77.5% (below 80, above 65: condonation band); CS302: 40/50 = exactly 80%."),
        dict(id="S1002", name="Rahul Menon", prog="B.Tech CSE", batch=2024,
             att={"CS301": (40, 33), "CS302": (40, 26), "CS303": (48, 40), "CS304": (40, 34)}, cgpa=6.5, fail=[("CS105", 1, 1)],
             note="CS302: 26/40 = exactly 65% (condonation limit); stored CGPA exactly 6.50 with exactly 1 active backlog (CS105): placement boundary."),
        dict(id="S1003", name="Meera Iyer", prog="B.Tech ECE", batch=2023,
             att={"EC401": (80, 60), "EC402": (50, 45), "EC403": (40, 36), "EC404": (45, 40)},
             note="EC401: 60/80 = exactly 75% (meets the old 75% rule, fails the new 80% B.Tech rule, inside the condonation band)."),
        dict(id="S1004", name="Karan Shah", prog="B.Tech CSE", batch=2025,
             att={"CS201": (59, 44), "CS202": (50, 45), "CS203": (40, 36), "CS204": (45, 40)},
             note="CS201: 44/59 = 74.576%: displayed as 74.58, fails 75 and 80 and sits in the condonation band."),
        dict(id="S1005", name="Priya Nair", prog="M.Tech CSE", batch=2025,
             att={"MC201": (60, 45), "MC202": (50, 45), "MC203": (40, 36), "MC204": (45, 40)},
             note="M.Tech is outside the scope of the 80% circular: 45/60 = exactly 75% is eligible under ACAD-REG-2024 clause 7.2."),
        dict(id="S1006", name="Vikram Rao", prog="B.Tech CSE", batch=2023,
             att={"CS401": (40, 36), "CS402": (50, 44), "CS403": (40, 35), "CS404": (45, 40)}, fail=[("CS205", 4, 1), ("CS306", 6, 1)],
             note="Two active backlogs (CS205, CS306): not placement eligible; clearing one makes backlogs 1 (eligible if CGPA allows)."),
        dict(id="S1007", name="Neha Kulkarni", prog="B.Tech CSE", batch=2023,
             att={"CS401": (40, 36), "CS402": (50, 44), "CS403": (40, 35), "CS404": (45, 40)}, fail=[("CS204", 3, 3)],
             note="CS204: FAIL in the regular exam and in two supplementary exams: 3 attempts used, a 4th exceeds the limit of 3."),
        dict(id="S1008", name="Arjun Das", prog="B.Tech CSE", batch=2024,
             att={"CS301": (40, 24), "CS302": (50, 41), "CS303": (40, 33), "CS304": (45, 40)},
             note="CS301: 24/40 = 60%, below the 65% condonation limit: not eligible, even with condonation."),
        dict(id="S1010", name="Divya Mehta", prog="B.Tech CSE", batch=2024,
             att={"CS301": (40, 34), "CS302": (50, 42), "CS303": (40, 35), "CS304": (45, 40)}, cgpa=8.4, record_only=True,
             note="Stored CGPA 8.40 is the official record; the results table is deliberately incomplete, so a recomputation differs and must be reported as such."),
    ]


def _gen_student_results(rng: random.Random, s: Student, extra_fail: list[tuple[str, int, int]], ds: Dataset,
                         catalogue: dict[tuple[str, int], list[Course]]) -> list[tuple[int, int]]:
    """Create past-semester results/attendance; return (credits, grade_points) per course from the latest attempt."""
    entries: list[tuple[int, int]] = []
    forced = {code: (sem, attempts) for code, sem, attempts in extra_fail}
    for sem in range(1, s.current_semester):
        for course in catalogue[(s.programme, sem)]:
            attempts_forced = forced.get(course.course_code)
            held, attended = _attendance_pair(rng, 0.84, 0.97)
            ds.attendance.append(dict(student_id=s.student_id, course_code=course.course_code, classes_held=held, classes_attended=attended))
            roll = rng.random()
            session = session_regular(s.batch_year, sem)
            if attempts_forced is not None or roll < 0.07:
                n_attempts = attempts_forced[1] if attempts_forced else 1
                internal, external, total = _marks(rng, True)
                ds.results.append(dict(student_id=s.student_id, course_code=course.course_code, exam_session=session, exam_type="REGULAR",
                                       internal_marks=internal, external_marks=external, total_marks=total, max_marks=100, result="FAIL"))
                last_total, last_result = total, "FAIL"
                cleared = False
                if attempts_forced is None:
                    supp_n = 1 if rng.random() < 0.6 else 0
                    for n in range(1, supp_n + 1):
                        passed = rng.random() < 0.7
                        i2, e2, t2 = _marks(rng, not passed)
                        if passed and t2 < 40:
                            i2, e2, t2 = 20, 25, 45
                        ds.results.append(dict(student_id=s.student_id, course_code=course.course_code, exam_session=session_supp(s.batch_year, sem, n),
                                               exam_type="SUPPLEMENTARY", internal_marks=i2, external_marks=e2, total_marks=t2, max_marks=100,
                                               result="PASS" if passed else "FAIL"))
                        last_total, last_result = t2, "PASS" if passed else "FAIL"
                else:
                    for n in range(1, n_attempts):
                        i2, e2, t2 = _marks(rng, True)
                        ds.results.append(dict(student_id=s.student_id, course_code=course.course_code, exam_session=session_supp(s.batch_year, sem, n),
                                               exam_type="SUPPLEMENTARY", internal_marks=i2, external_marks=e2, total_marks=t2, max_marks=100, result="FAIL"))
                        last_total, last_result = t2, "FAIL"
                entries.append((course.credits, grade_points(last_total) if last_result == "PASS" else 0))
            else:
                internal, external, total = _marks(rng, False)
                ds.results.append(dict(student_id=s.student_id, course_code=course.course_code, exam_session=session, exam_type="REGULAR",
                                       internal_marks=internal, external_marks=external, total_marks=total, max_marks=100, result="PASS"))
                entries.append((course.credits, grade_points(total)))
    return entries


def generate(seed: int = 20261006, n_students: int = 60) -> Dataset:
    rng = random.Random(seed)
    ds = Dataset(courses=build_courses())
    catalogue: dict[tuple[str, int], list[Course]] = {}
    for c in ds.courses:
        catalogue.setdefault((c.programme, c.semester), []).append(c)

    names_used: set[str] = set()
    firsts, lasts = FIRST[:], LAST[:]
    rng.shuffle(firsts)
    rng.shuffle(lasts)

    def fresh_name() -> str:
        for f, l in zip(firsts, lasts):
            full = f"{f} {l}"
            if full not in names_used and f not in {n.split()[0] for n in names_used} and l not in {n.split()[1] for n in names_used}:
                names_used.add(full)
                return full
        raise RuntimeError("name pool exhausted")

    specs = {d["id"]: d for d in designed_specs()}
    for d in specs.values():
        names_used.add(d["name"])
    # make sure random names never reuse a first or last name of a designed student
    firsts[:] = [f for f in firsts if f not in {d["name"].split()[0] for d in specs.values()}]
    lasts[:] = [l for l in lasts if l not in {d["name"].split()[1] for d in specs.values()}]

    prog_plan = ["B.Tech CSE"] * 4 + ["B.Tech ECE"] * 2 + ["M.Tech CSE"]
    for i in range(1, n_students + 1):
        sid = f"S{1000 + i}"
        spec = specs.get(sid)
        if spec:
            prog, batch, name = spec["prog"], spec["batch"], spec["name"]
        else:
            prog = prog_plan[rng.randrange(len(prog_plan))]
            batch = rng.choice([2023, 2024, 2025, 2026]) if prog != "M.Tech CSE" else rng.choice([2025, 2026])
            name = fresh_name()
        sem = min(MAX_SEM[prog], 2 * (AS_OF_YEAR - batch) + 1)
        st = Student(sid, name, prog, batch, sem)
        extra_fail = spec.get("fail", []) if spec else []
        entries = _gen_student_results(rng, st, extra_fail, ds, catalogue)
        # current-semester attendance
        for j, course in enumerate(catalogue[(prog, sem)]):
            if spec and course.course_code in spec["att"]:
                held, attended = spec["att"][course.course_code]
            else:
                held, attended = _attendance_pair(rng, 0.62, 0.98)
            ds.attendance.append(dict(student_id=sid, course_code=course.course_code, classes_held=held, classes_attended=attended))
        # derived fields
        latest: dict[str, str] = {}
        for r in sorted([r for r in ds.results if r["student_id"] == sid], key=lambda r: (r["exam_session"][:4], r["exam_type"] == "SUPPLEMENTARY")):
            latest[r["course_code"]] = r["result"]
        st.active_backlogs = sum(1 for v in latest.values() if v != "PASS")
        st.cgpa = cgpa_from(entries)
        if spec and spec.get("cgpa") is not None:
            st.cgpa = spec["cgpa"]
            ds.cgpa_record_only.add(sid)
        if spec and spec.get("record_only"):
            ds.cgpa_record_only.add(sid)
        if spec:
            ds.designed[sid] = spec["note"]
        ds.students.append(st)
    # session ordering uses (year, month): keep the CSV tidy
    ds.results.sort(key=lambda r: (r["student_id"], r["course_code"], r["exam_session"], r["exam_type"]))
    ds.attendance.sort(key=lambda r: (r["student_id"], r["course_code"]))
    return ds


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})
    return buf.getvalue()


def dataset_csvs(ds: Dataset) -> dict[str, str]:
    from dataclasses import asdict

    return {
        "courses.csv": to_csv([asdict(c) for c in ds.courses], ["course_code", "course_name", "programme", "semester", "credits"]),
        "students.csv": to_csv([asdict(s) for s in ds.students], ["student_id", "full_name", "programme", "batch_year", "current_semester", "cgpa", "active_backlogs"]),
        "attendance.csv": to_csv(ds.attendance, ["student_id", "course_code", "classes_held", "classes_attended"]),
        "results.csv": to_csv(ds.results, ["student_id", "course_code", "exam_session", "exam_type", "internal_marks", "external_marks", "total_marks", "max_marks", "result"]),
    }
