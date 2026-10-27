"""Simulate a persona's life timeline and the files it leaves behind (ADR-0015 §1).

Every file gets true per-event times (authored / arrived_here / last_user_edit), its activity
(the ground-truth work session), and labelled date mentions (meaningful vs incidental) **from the
simulation itself**, never labelled afterwards. All randomness flows from one seeded
``random.Random``, so (archetype, seed, scale, family) reproduces the same persona exactly.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from contextfs.benchgen.model import Activity, DateMention, FilePlan, Persona, TimeTruth

__all__ = ["simulate", "ARCHETYPES"]

ARCHETYPES = ("student", "professional", "researcher")

FIRST_NAMES = [
    "Aarav",
    "Diya",
    "Kabir",
    "Meera",
    "Rohan",
    "Ananya",
    "Vikram",
    "Sneha",
    "Arjun",
    "Priya",
    "Nikhil",
    "Kavya",
    "Siddharth",
    "Ishita",
    "Rahul",
    "Tara",
    "Farhan",
    "Neha",
    "Joel",
    "Lakshmi",
    "Daniel",
    "Sara",
    "Omar",
    "Elena",
    "Kenji",
    "Amara",
    "Lucas",
    "Fatima",
    "Noah",
    "Zara",
]
LAST_NAMES = [
    "Iyer",
    "Sharma",
    "Menon",
    "Reddy",
    "Khan",
    "Das",
    "Pillai",
    "Gupta",
    "Nair",
    "Joseph",
    "Fernandes",
    "Rao",
    "Banerjee",
    "Kulkarni",
    "Thomas",
    "Shah",
    "Varghese",
    "Bose",
]
CITIES = ["Chennai", "Bengaluru", "Kochi", "Hyderabad", "Pune", "Mumbai", "Delhi", "Coimbatore"]
TRIP_PLACES = [
    "Ooty",
    "Munnar",
    "Goa",
    "Pondicherry",
    "Mysuru",
    "Hampi",
    "Shimla",
    "Jaipur",
    "Varkala",
    "Coorg",
    "Rishikesh",
    "Udaipur",
]
COMPANIES = [
    "Zoho",
    "Freshworks",
    "Infosys",
    "Razorpay",
    "Postman",
    "Chargebee",
    "TCS",
    "Swiggy",
    "Atlassian",
    "Thoughtworks",
    "Siemens",
    "Bosch",
    "Accenture",
    "Flipkart",
]

COURSES = {
    "Database Systems": [
        "normalization",
        "BCNF",
        "transactions",
        "indexing",
        "SQL joins",
        "query optimisation",
    ],
    "Machine Learning": [
        "regression",
        "SVM",
        "kernel trick",
        "ensembles",
        "bagging",
        "boosting",
        "cross-validation",
    ],
    "Operating Systems": [
        "scheduling",
        "paging",
        "deadlock",
        "semaphores",
        "virtual memory",
        "file systems",
    ],
    "Computer Networks": ["TCP congestion", "routing", "subnetting", "DNS", "HTTP", "sockets"],
    "Compiler Design": [
        "lexing",
        "parsing",
        "LR tables",
        "syntax trees",
        "code generation",
        "register allocation",
    ],
    "Cryptography": [
        "RSA",
        "AES",
        "hash functions",
        "digital signatures",
        "key exchange",
        "elliptic curves",
    ],
    "Cloud Computing": [
        "virtualisation",
        "containers",
        "load balancing",
        "autoscaling",
        "object storage",
    ],
    "Data Mining": ["association rules", "apriori", "clustering", "k-means", "outlier detection"],
    "Software Engineering": [
        "requirements",
        "UML",
        "testing",
        "agile",
        "code review",
        "design patterns",
    ],
    "Computer Vision": [
        "convolution",
        "edge detection",
        "feature matching",
        "object detection",
        "segmentation",
    ],
}
PROJECT_TOPICS = {
    "traffic prediction": ["sensor data", "LSTM", "congestion", "dashboard"],
    "campus food ordering app": ["Flutter", "Firebase", "menu", "payments"],
    "crop disease detection": ["leaf images", "CNN", "dataset", "accuracy"],
    "resume parser": ["NER", "PDF extraction", "skills", "ranking"],
    "smart attendance": ["face recognition", "camera", "roll call", "database"],
    "expense splitter": ["groups", "balances", "settle up", "React"],
}
CLIENT_TOPICS = {
    "inventory migration": ["ERP", "data mapping", "cutover", "warehouse"],
    "mobile banking revamp": ["UX", "KYC", "OTP", "release plan"],
    "analytics dashboard": ["KPIs", "Power BI", "data model", "refresh"],
    "payroll automation": ["payslips", "tax slabs", "approvals", "audit"],
}
RESEARCH_TOPICS = {
    "dense retrieval": ["embeddings", "contrastive loss", "hard negatives", "BEIR"],
    "temporal information retrieval": ["time expressions", "recency", "timelines", "normalisation"],
    "graph neural networks": ["message passing", "node classification", "over-smoothing", "GCN"],
    "speech recognition": ["acoustic model", "WER", "CTC", "data augmentation"],
}
VENUES = ["SIGIR", "CIKM", "ECIR", "ACL", "EMNLP", "WSDM", "KDD", "NeurIPS"]


class _Sim:
    def __init__(self, persona: Persona, scale: float, rng: random.Random) -> None:
        self.p = persona
        self.scale = scale
        self.rng = rng
        self.activities: list[Activity] = []
        self.files: list[FilePlan] = []
        self._paths: set[str] = set()
        self._n = 0

    # -- helpers -------------------------------------------------------------

    def aid(self, kind: str) -> str:
        self._n += 1
        return f"{self.p.persona_id}:{kind}:{self._n}"

    def person(self) -> str:
        return f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"

    def at(self, day: datetime, early: int = 9, late: int = 23) -> datetime:
        """A plausible working time on ``day``."""
        hour = self.rng.randint(early, late)
        return day.replace(
            hour=hour, minute=self.rng.randint(0, 59), second=self.rng.randint(0, 59), microsecond=0
        )

    def session_days(self, start: datetime, end: datetime, k: int) -> list[datetime]:
        """Pick k work days in [start, end], weighted toward the end (people work near deadlines)."""
        span = max(1, (end - start).days)
        offsets = sorted({min(span, int(span * (1 - self.rng.random() ** 2))) for _ in range(k)})
        return [start + timedelta(days=o) for o in offsets] or [end]

    def unique(self, path: str) -> str:
        if path not in self._paths:
            self._paths.add(path)
            return path
        stem, dot, ext = path.rpartition(".")
        n = 2
        while f"{stem}_{n}.{ext}" in self._paths:
            n += 1
        out = f"{stem}_{n}.{ext}"
        self._paths.add(out)
        return out

    def add(self, plan: FilePlan) -> FilePlan:
        plan.rel_path = self.unique(plan.rel_path)
        self.files.append(plan)
        return plan

    def authored_file(self, path, kind, act, sessions, title="", dates=(), keywords=None):
        first, last = self.at(sessions[0]), self.at(sessions[-1])
        last = max(last, first + timedelta(minutes=20))
        return self.add(
            FilePlan(
                rel_path=path,
                kind=kind,
                origin="authored",
                activity_id=act.activity_id,
                truth=TimeTruth(authored=first, arrived_here=first, last_user_edit=last),
                title=title or act.title,
                topic=act.topic,
                keywords=list(keywords or act.keywords),
                dates=list(dates),
                infile_author=self.p.name,
                people=list(act.people),
            )
        )

    def downloaded_file(self, path, kind, act, arrived, authored, title="", dates=(), author=""):
        return self.add(
            FilePlan(
                rel_path=path,
                kind=kind,
                origin="downloaded",
                activity_id=act.activity_id if act else None,
                truth=TimeTruth(authored=authored, arrived_here=arrived, last_user_edit=None),
                title=title or (act.title if act else ""),
                topic=act.topic if act else "",
                keywords=list(act.keywords) if act else [],
                dates=list(dates),
                infile_author=author or self.person(),
                # A lecturer or supervisor who wrote a handout is named in it.
                people=[author] if author.startswith(("Prof.", "Dr.")) else [],
            )
        )

    def mention(self, day: datetime, meaningful: bool, why: str) -> DateMention:
        style = self.rng.randrange(5)
        surface = [
            day.strftime("%d %B %Y"),
            day.strftime("%d-%m-%Y"),
            day.strftime("%Y-%m-%d"),
            day.strftime("%d %b"),
            day.strftime("%A, %d %B"),
        ][style]
        return DateMention(day.date().isoformat(), surface, meaningful, why)

    # -- archetype: student ----------------------------------------------------

    def student(self) -> None:
        p, rng = self.p, self.rng
        semesters = []
        cursor = datetime(p.start.year, 7, 15)
        while cursor < p.end:
            semesters.append((cursor, cursor + timedelta(days=125)))
            cursor = cursor.replace(
                year=cursor.year + (1 if cursor.month == 12 or cursor.month >= 7 else 0),
                month=1 if cursor.month >= 7 else 7,
                day=15 if cursor.month >= 7 else 15,
            )
        course_names = list(COURSES)
        rng.shuffle(course_names)
        sem_no = 5
        for s_start, s_end in semesters:
            if s_start >= p.end:
                break
            s_end = min(s_end, p.end - timedelta(days=1))
            n_courses = max(2, round(3 * self.scale))
            for cname in [
                course_names.pop() if course_names else rng.choice(list(COURSES))
                for _ in range(n_courses)
            ]:
                self.course(cname, s_start, s_end, sem_no)
            sem_no += 1
        n_projects = max(1, round(2 * self.scale))
        for _ in range(n_projects):
            self.hackathon()
        self.job_search()
        self.capstone()

    def course(self, cname: str, s_start: datetime, s_end: datetime, sem_no: int) -> None:
        rng = self.rng
        short = "".join(w[0] for w in cname.split()).upper()
        folder = f"College/Sem{sem_no}/{cname.replace(' ', '')}"
        kw = COURSES[cname]
        course = Activity(
            self.aid("course"),
            "course",
            cname,
            cname,
            s_start,
            s_end,
            people=[f"Prof. {self.person()}"],
            keywords=kw,
            folder=folder,
        )
        self.activities.append(course)
        lecturer = course.people[0]
        # Lecture PDFs: authored years ago by the lecturer, arrived weekly.
        for unit, topic in enumerate(kw[: max(2, round(len(kw) * self.scale))], start=1):
            arrived = self.at(s_start + timedelta(days=7 * unit), 10, 18)
            authored = arrived - timedelta(days=rng.randint(300, 1500))
            self.downloaded_file(
                f"{folder}/Unit{unit}_{topic.title().replace(' ', '_')}.pdf",
                "pdf",
                course,
                arrived,
                authored,
                title=f"{cname} — Unit {unit}: {topic}",
                author=lecturer,
            )
        # Assignment: a brief arrives, the persona writes draft and final before the deadline.
        a_start = s_start + timedelta(days=rng.randint(20, 50))
        deadline = self.at(a_start + timedelta(days=rng.randint(10, 20)), 17, 23)
        assign = Activity(
            self.aid("assignment"),
            "assignment",
            f"{cname} assignment",
            rng.choice(kw),
            a_start,
            deadline,
            deadline,
            folder=folder,
            keywords=kw,
        )
        self.activities.append(assign)
        brief_due = self.mention(deadline, True, "assignment deadline in the brief")
        self.downloaded_file(
            f"{folder}/{short}_assignment_brief.pdf",
            "pdf",
            assign,
            self.at(a_start, 10, 17),
            self.at(a_start - timedelta(days=1)),
            title=f"{cname} assignment brief",
            dates=[brief_due],
            author=lecturer,
        )
        days = self.session_days(a_start, deadline - timedelta(hours=2), rng.randint(3, 6))
        self.authored_file(
            f"{folder}/{short}_assignment_draft.docx",
            "docx",
            assign,
            days[: max(1, len(days) // 2)],
            title=f"{cname} assignment (draft)",
            dates=[self.mention(deadline, True, "own deadline note")],
        )
        self.authored_file(
            f"{folder}/{short}_assignment_final.docx",
            "docx",
            assign,
            days[len(days) // 2 :] or days,
            title=f"{cname} assignment",
            dates=[self.mention(deadline, True, "submission date")],
        )
        # Exam: a timetable arrives, revision files are made in the days before the exam.
        exam_day = self.at(s_end - timedelta(days=rng.randint(2, 10)), 9, 10)
        prep = Activity(
            self.aid("exam_prep"),
            "exam_prep",
            f"{cname} exam revision",
            cname,
            exam_day - timedelta(days=12),
            exam_day,
            exam_day,
            folder=folder,
            keywords=kw,
        )
        self.activities.append(prep)
        tt_folder = f"College/Sem{sem_no}"
        tt_path = f"{tt_folder}/Exam_Timetable_Sem{sem_no}.xlsx"
        if tt_path not in self._paths:
            self.downloaded_file(
                tt_path,
                "xlsx",
                prep,
                self.at(exam_day - timedelta(days=20), 10, 17),
                self.at(exam_day - timedelta(days=21)),
                title=f"Semester {sem_no} exam timetable",
                dates=[self.mention(exam_day, True, "exam date in timetable")],
                author="Exam Cell",
            )
        else:
            for f in self.files:
                if f.rel_path == tt_path:
                    f.dates.append(self.mention(exam_day, True, "exam date in timetable"))
        rdays = self.session_days(prep.start, exam_day - timedelta(hours=10), rng.randint(2, 5))
        self.authored_file(
            f"{folder}/{short}_revision_notes.md",
            "md",
            prep,
            rdays,
            title=f"{cname} revision notes",
            dates=[self.mention(exam_day, True, "exam date")],
        )
        if rng.random() < 0.6:
            self.authored_file(
                f"{folder}/{short}_formula_sheet.txt",
                "txt",
                prep,
                rdays[-2:] or rdays,
                title=f"{cname} formula sheet",
            )

    def hackathon(self) -> None:
        p, rng = self.p, self.rng
        topic = rng.choice(list(PROJECT_TOPICS))
        kw = PROJECT_TOPICS[topic]
        friday = p.start + timedelta(days=rng.randint(30, max(31, (p.end - p.start).days - 30)))
        friday -= timedelta(days=(friday.weekday() - 4) % 7)
        end = friday + timedelta(days=2)
        name = topic.title().replace(" ", "")
        folder = f"Projects/{name}"
        act = Activity(
            self.aid("hackathon"),
            "hackathon",
            f"{topic} hackathon",
            topic,
            friday,
            end,
            self.at(end, 14, 18),
            people=[self.person(), self.person()],
            keywords=kw,
            folder=folder,
        )
        self.activities.append(act)
        sub = self.mention(act.deadline, True, "hackathon submission deadline")
        days = [friday, friday + timedelta(days=1), end]
        self.authored_file(f"{folder}/README.md", "md", act, days, dates=[sub])
        self.authored_file(f"{folder}/app.py", "code", act, days[:2])
        self.authored_file(f"{folder}/model.py", "code", act, days[1:])
        self.authored_file(f"{folder}/pitch_deck.pptx", "pptx", act, days[-1:], dates=[sub])
        self.authored_file(f"{folder}/team_notes.md", "md", act, days, dates=[sub])

    def job_search(self) -> None:
        p, rng = self.p, self.rng
        start = p.start + timedelta(days=rng.randint(20, 120))
        end = start + timedelta(days=rng.randint(20, 40))
        act = Activity(
            self.aid("job_search"),
            "job_search",
            "internship applications",
            "internships",
            start,
            end,
            keywords=["resume", "cover letter", "interview", "internship"],
            folder="Personal/Career",
        )
        self.activities.append(act)
        days = self.session_days(start, end, 6)
        self.authored_file(
            "Personal/Career/Resume.docx", "docx", act, days[:3], title=f"{p.name} — Resume"
        )
        companies = rng.sample(COMPANIES, k=max(2, round(3 * self.scale)))
        tracker_dates = []
        for c in companies:
            d = self.at(end + timedelta(days=rng.randint(-10, 15)), 10, 17)
            tracker_dates.append(self.mention(d, True, f"{c} application deadline"))
            self.authored_file(
                f"Personal/Career/cover_letter_{c.lower()}.docx",
                "docx",
                act,
                [rng.choice(days)],
                title=f"Cover letter — {c}",
                keywords=[c, "internship"],
            )
        self.authored_file(
            "Personal/Career/application_tracker.xlsx",
            "xlsx",
            act,
            days,
            dates=tracker_dates,
            keywords=companies,
        )

    def capstone(self) -> None:
        p, rng = self.p, self.rng
        start = p.end - timedelta(days=rng.randint(120, 200))
        end = p.end - timedelta(days=rng.randint(5, 30))
        topic = rng.choice(list(PROJECT_TOPICS))
        guide = f"Dr. {self.person()}"
        act = Activity(
            self.aid("capstone"),
            "capstone",
            f"final year project: {topic}",
            topic,
            start,
            end,
            people=[guide],
            keywords=PROJECT_TOPICS[topic],
            folder="College/Capstone",
        )
        self.activities.append(act)
        meetings = sorted(
            self.at(start + timedelta(days=rng.randint(5, (end - start).days)), 10, 16)
            for _ in range(4)
        )
        review = self.at(start + timedelta(days=(end - start).days // 2), 10, 13)
        days = self.session_days(start, end, 8)
        self.authored_file(
            "College/Capstone/project_proposal.docx",
            "docx",
            act,
            days[:2],
            title=f"Project proposal: {topic}",
        )
        self.authored_file(
            "College/Capstone/supervisor_meeting_notes.md",
            "md",
            act,
            meetings,
            dates=[self.mention(m, True, "supervisor meeting") for m in meetings],
        )
        self.authored_file(
            "College/Capstone/literature_survey.md",
            "md",
            act,
            days[1:4],
            dates=[
                self.mention(datetime(y, 1, 1), False, "publication year")
                for y in rng.sample(range(2008, 2024), 3)
            ],
        )
        self.authored_file(
            "College/Capstone/review1_slides.pptx",
            "pptx",
            act,
            [review - timedelta(days=2), review - timedelta(hours=14)],
            dates=[self.mention(review, True, "review date")],
        )

    # -- archetype: professional ----------------------------------------------

    def professional(self) -> None:
        p, rng = self.p, self.rng
        n_projects = max(2, round(3 * self.scale))
        for _ in range(n_projects):
            topic = rng.choice(list(CLIENT_TOPICS))
            client = rng.choice(COMPANIES)
            start = p.start + timedelta(days=rng.randint(0, max(1, (p.end - p.start).days - 120)))
            end = start + timedelta(days=rng.randint(60, 120))
            folder = f"Work/{client}/{topic.title().replace(' ', '')}"
            act = Activity(
                self.aid("client_project"),
                "client_project",
                f"{client} {topic}",
                topic,
                start,
                min(end, p.end),
                people=[self.person(), self.person()],
                keywords=CLIENT_TOPICS[topic] + [client],
                folder=folder,
            )
            self.activities.append(act)
            golive = self.at(act.end - timedelta(days=3), 9, 12)
            days = self.session_days(start, act.end, 6)
            self.authored_file(
                f"{folder}/proposal.docx",
                "docx",
                act,
                days[:2],
                dates=[self.mention(golive, True, "planned go-live")],
            )
            for wk, d in enumerate(days[2:], start=1):
                self.authored_file(f"{folder}/status_report_week{wk}.docx", "docx", act, [d])
            self.authored_file(
                f"{folder}/steering_review.pptx",
                "pptx",
                act,
                days[-2:],
                dates=[self.mention(golive, True, "go-live date")],
            )
            due = self.at(act.end + timedelta(days=30), 10, 12)
            self.authored_file(
                f"{folder}/invoice_{client.lower()}.xlsx",
                "xlsx",
                act,
                days[-1:],
                dates=[
                    self.mention(due, True, "payment due date"),
                    self.mention(act.end, False, "invoice issue date"),
                ],
            )

    # -- archetype: researcher -------------------------------------------------

    def researcher(self) -> None:
        p, rng = self.p, self.rng
        n_papers = max(1, round(2 * self.scale))
        for _ in range(n_papers):
            topic = rng.choice(list(RESEARCH_TOPICS))
            venue = rng.choice(VENUES)
            deadline = self.at(
                p.start + timedelta(days=rng.randint(90, max(91, (p.end - p.start).days - 10))),
                20,
                23,
            )
            start = deadline - timedelta(days=rng.randint(40, 90))
            folder = f"Research/{venue}_{topic.title().replace(' ', '')}"
            act = Activity(
                self.aid("paper"),
                "paper",
                f"{venue} paper on {topic}",
                topic,
                start,
                deadline,
                deadline,
                people=[self.person()],
                keywords=RESEARCH_TOPICS[topic] + [venue],
                folder=folder,
            )
            self.activities.append(act)
            dl = self.mention(deadline, True, f"{venue} submission deadline")
            days = self.session_days(start, deadline - timedelta(hours=3), 8)
            self.authored_file(f"{folder}/draft.md", "md", act, days, dates=[dl])
            self.authored_file(f"{folder}/experiments.py", "code", act, days[1:5])
            self.authored_file(f"{folder}/results.md", "md", act, days[3:])
            for i in range(max(2, round(4 * self.scale))):
                arrived = self.at(start + timedelta(days=rng.randint(0, 30)), 9, 23)
                self.downloaded_file(
                    f"{folder}/related/paper_{i + 1}.pdf",
                    "pdf",
                    act,
                    arrived,
                    arrived - timedelta(days=rng.randint(200, 2500)),
                    title=f"Related work on {topic} ({i + 1})",
                    dates=[
                        self.mention(
                            datetime(rng.randint(2012, 2024), 1, 1), False, "publication year"
                        )
                    ],
                )
            talk = self.at(deadline + timedelta(days=rng.randint(60, 120)), 9, 17)
            if talk < p.end:
                self.authored_file(
                    f"{folder}/talk_slides.pptx",
                    "pptx",
                    act,
                    [talk - timedelta(days=4), talk - timedelta(days=1)],
                    dates=[self.mention(talk, True, "talk date")],
                )

    # -- shared: trips, admin, noise ------------------------------------------

    def trips(self) -> None:
        p, rng = self.p, self.rng
        for _ in range(max(1, round(2 * self.scale))):
            place = rng.choice(TRIP_PLACES)
            start = self.at(
                p.start + timedelta(days=rng.randint(10, max(11, (p.end - p.start).days - 20))),
                5,
                8,
            )
            end = start + timedelta(days=rng.randint(2, 5))
            folder = f"Personal/Trips/{place}_{start:%b%Y}"
            act = Activity(
                self.aid("trip"),
                "trip",
                f"trip to {place}",
                place,
                start,
                end,
                place=place,
                keywords=[place, "trip", "photos"],
                folder=folder,
            )
            self.activities.append(act)
            booked = start - timedelta(days=rng.randint(10, 40))
            self.downloaded_file(
                f"{folder}/ticket_{place.lower()}.pdf",
                "pdf",
                act,
                self.at(booked),
                self.at(booked),
                title=f"Train ticket to {place}",
                dates=[
                    self.mention(start, True, "travel date"),
                    self.mention(booked, False, "booking date"),
                ],
                author="IRCTC",
            )
            imported = end + timedelta(days=rng.randint(1, 20))
            for i in range(max(3, round(rng.randint(4, 12) * self.scale))):
                shot = start + timedelta(hours=rng.uniform(0, (end - start).total_seconds() / 3600))
                shot = shot.replace(microsecond=0)
                self.add(
                    FilePlan(
                        rel_path=f"{folder}/IMG_{shot:%Y%m%d}_{i:03d}.jpg",
                        kind="jpg",
                        origin="received",
                        activity_id=act.activity_id,
                        truth=TimeTruth(
                            authored=shot,
                            arrived_here=self.at(imported, 19, 23),
                            last_user_edit=None,
                        ),
                        title=f"photo at {place}",
                        topic=place,
                        keywords=[place],
                    )
                )

    def admin(self) -> None:
        p, rng = self.p, self.rng
        act = Activity(
            self.aid("admin"),
            "admin",
            "personal admin",
            "finances",
            p.start,
            p.end,
            keywords=["budget", "rent", "bills"],
            folder="Personal/Admin",
        )
        self.activities.append(act)
        months = max(3, (p.end - p.start).days // 30)
        days = [p.start + timedelta(days=30 * m + rng.randint(0, 5)) for m in range(months)]
        self.authored_file("Personal/Admin/monthly_budget.xlsx", "xlsx", act, days)
        tax = self.at(datetime(p.end.year if p.end.month > 7 else p.end.year - 1, 7, 31), 10, 12)
        if p.start < tax < p.end:
            self.authored_file(
                "Personal/Admin/tax_checklist.txt",
                "txt",
                act,
                [tax - timedelta(days=9), tax - timedelta(days=2)],
                dates=[self.mention(tax, True, "tax filing deadline")],
            )

    def noise(self) -> None:
        p, rng = self.p, self.rng
        n = max(5, round(25 * self.scale))
        span = max(1, (p.end - p.start).days)
        for i in range(n):
            arrived = self.at(p.start + timedelta(days=rng.randint(0, span - 1)))
            kind = rng.choice(["pdf", "pdf", "txt", "jpg", "zip", "docx"])
            name = rng.choice(
                [
                    "invoice",
                    "manual",
                    "receipt",
                    "brochure",
                    "scan",
                    "wallpaper",
                    "notes",
                    "setup_guide",
                    "statement",
                    "form",
                ]
            )
            ext = {"jpg": "jpg", "zip": "zip"}.get(kind, kind)
            plan = self.downloaded_file(
                f"Downloads/{name}_{i + 1}.{ext}",
                kind,
                None,
                arrived,
                arrived - timedelta(days=rng.randint(0, 900)),
                title=f"{name.replace('_', ' ')} {i + 1}",
            )
            plan.noise = True
            plan.origin = "downloaded"
        # Duplicates the way Windows names them.
        candidates = [
            f for f in self.files if f.origin == "downloaded" and f.kind in {"pdf", "docx"}
        ]
        for orig in rng.sample(candidates, k=min(len(candidates), max(2, round(5 * self.scale)))):
            stem, _, ext = orig.rel_path.rpartition(".")
            again = orig.truth.arrived_here + timedelta(
                days=rng.randint(0, 60), minutes=rng.randint(1, 600)
            )
            if again >= p.end:
                continue
            dup = FilePlan(
                rel_path=f"{stem} (1).{ext}",
                kind=orig.kind,
                origin="downloaded",
                activity_id=orig.activity_id,
                truth=TimeTruth(
                    authored=orig.truth.authored, arrived_here=again, last_user_edit=None
                ),
                title=orig.title,
                topic=orig.topic,
                keywords=list(orig.keywords),
                dates=list(orig.dates),
                infile_author=orig.infile_author,
                duplicate_of=orig.rel_path,
            )
            self.add(dup)


def simulate(persona: Persona, scale: float = 1.0) -> tuple[list[Activity], list[FilePlan]]:
    """Simulate one persona. Returns its activities and file plans (no content yet)."""
    rng = random.Random(persona.seed)
    sim = _Sim(persona, scale, rng)
    getattr(sim, persona.archetype)()
    sim.trips()
    sim.admin()
    sim.noise()
    # Clamp: nothing may happen after the persona's "now".
    sim.files = [f for f in sim.files if f.truth.arrived_here < persona.end]
    for f in sim.files:
        if f.truth.last_user_edit and f.truth.last_user_edit >= persona.end:
            f.truth.last_user_edit = persona.end - timedelta(hours=1)
    return sim.activities, sim.files
