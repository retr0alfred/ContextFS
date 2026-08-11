"""The authored specification of the ContextFS synthetic evaluation corpus.

Why this corpus is *authored* rather than *randomly generated*
--------------------------------------------------------------
A randomly generated corpus can test that code runs. It cannot test whether
*context-aware retrieval beats semantic retrieval*, because that hypothesis is
about specific, adversarial relationships between files - a timetable that names
a lecture PDF which never uses the word "exam"; two dates a month apart where
one is a deadline and the other is a birthday; two drafts of one assignment that
should be recognised as near-duplicates. Those relationships have to be
deliberately planted, and the ground truth has to say where they were planted.

So this module is the benchmark's specification, and it is adversarial by
design: several queries are constructed so that a pure-semantic system
*should* fail them. If the full system does not beat the baseline on those,
the hypothesis is not supported, and the corpus is built to make that visible
rather than to flatter the system.

Persona
-------
A final-year CS student's file tree across roughly seven months (Aug 2025 -
Mar 2026), spanning six real work contexts plus deliberate noise.

Ground-truth labels attached here
---------------------------------
* ``session``            - which activity session a file truly belongs to (Phase 12 metric)
* ``meaningful_dates``   - dates that are genuinely actionable (Phase 10 metric)
* ``incidental_dates``   - dates that merely *appear* in the text (Phase 10 metric)
* ``near_duplicate_of``  - planted near-duplicate pairs (Phase 9 metric)
* ``QUERIES``            - natural-language re-finding queries with correct targets
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

__all__ = [
    "CORPUS_FILES",
    "QUERIES",
    "SESSIONS",
    "DateLabel",
    "FileSpec",
    "QuerySpec",
    "SessionSpec",
    "CORPUS_SEED",
]

#: Fixed seed recorded in the ground-truth file. The corpus is fully authored,
#: so nothing actually samples from it; it exists so that if a future revision
#: introduces sampling, the provenance field is already there.
CORPUS_SEED = 20260811

DateKind = Literal["meaningful", "incidental"]


@dataclass(frozen=True)
class DateLabel:
    """A date occurring in a document, with its ground-truth classification.

    Attributes:
        date: ISO ``YYYY-MM-DD``.
        kind: ``"meaningful"`` (actionable: deadline, exam, meeting, review) or
            ``"incidental"`` (mentioned but not actionable: a birth year, a
            historical event, a film release).
        surface: The literal string as it appears in the document, so the
            evaluation can align a detected mention with its label.
        why: Human-readable justification for the label. This is what makes the
            Phase 10 precision/recall number defensible in a viva: every label
            has a stated reason rather than being an unexplained annotation.
    """

    date: str
    kind: DateKind
    surface: str
    why: str


@dataclass(frozen=True)
class SessionSpec:
    """A ground-truth activity session (a real period of coherent work)."""

    id: str
    label: str
    kind: str
    start: str
    end: str
    description: str


@dataclass(frozen=True)
class FileSpec:
    """One file in the synthetic corpus and everything known to be true of it."""

    path: str
    kind: str
    mtime: str
    content: Any
    session: str | None = None
    dates: tuple[DateLabel, ...] = ()
    near_duplicate_of: str | None = None
    notes: str = ""

    @property
    def modified_at(self) -> datetime:
        """Parsed modification timestamp."""
        return datetime.fromisoformat(self.mtime)

    @property
    def meaningful_dates(self) -> list[DateLabel]:
        """Dates labelled as actionable."""
        return [d for d in self.dates if d.kind == "meaningful"]

    @property
    def incidental_dates(self) -> list[DateLabel]:
        """Dates labelled as non-actionable."""
        return [d for d in self.dates if d.kind == "incidental"]


@dataclass(frozen=True)
class QuerySpec:
    """A natural-language re-finding query and its ground-truth answers.

    Attributes:
        id: Stable identifier used in results tables.
        text: What the user types - phrased from memory, not from content.
        targets: The file(s) the user actually wanted. Used for Precision@1/MRR.
        relevant: The full set of acceptable files, a superset of ``targets``.
            Used for Recall@K.
        kind: Which retrieval capability the query exercises. Reporting metrics
            broken down by this field is what turns the ablation study from
            "the full system scores higher" into "activity modelling is what
            fixes activity-style queries" - a far stronger claim.
        difficulty: ``"easy"`` if lexical overlap alone would find it,
            ``"hard"`` if it requires context the file's own text lacks.
        rationale: Why this query belongs in the benchmark.
    """

    id: str
    text: str
    targets: tuple[str, ...]
    relevant: tuple[str, ...]
    kind: Literal["semantic", "activity", "temporal", "entity", "hybrid"]
    difficulty: Literal["easy", "hard"]
    rationale: str
    field_note: str = field(default="")


# ===========================================================================
# Sessions
# ===========================================================================

SESSIONS: tuple[SessionSpec, ...] = (
    SessionSpec(
        id="internship_apps",
        label="Internship applications",
        kind="career",
        start="2025-08-04",
        end="2025-08-22",
        description=(
            "Three weeks of applying for summer internships: CV revisions, cover "
            "letters, a tracking sheet, and interview preparation."
        ),
    ),
    SessionSpec(
        id="hackathon_urbanflow",
        label="UrbanFlow hackathon",
        kind="hackathon",
        start="2025-09-12",
        end="2025-09-15",
        description=(
            "A 36-hour inter-college hackathon. Dense burst of activity across "
            "code, slides, notes and data in a single weekend - the clearest "
            "temporal-proximity signal in the corpus."
        ),
    ),
    SessionSpec(
        id="dbms_assignment",
        label="DBMS normalization assignment",
        kind="assignment",
        start="2025-10-06",
        end="2025-10-18",
        description=(
            "A database normalization assignment written over two weeks, "
            "including a deliberately planted draft/final near-duplicate pair."
        ),
    ),
    SessionSpec(
        id="ml_exam_prep",
        label="Machine Learning exam preparation",
        kind="exam_prep",
        start="2025-11-10",
        end="2025-11-24",
        description=(
            "Revision for the Semester 7 ML end-semester exam. Contains the "
            "corpus's central adversarial case: the timetable knows about the "
            "exam, the lecture PDFs the student actually revised do not."
        ),
    ),
    SessionSpec(
        id="capstone_contextfs",
        label="Capstone project (ContextFS)",
        kind="project",
        start="2026-01-12",
        end="2026-03-20",
        description=(
            "Final-year project work: proposal, literature survey, review "
            "presentations, supervisor meetings, and prototype code. The "
            "longest-running session, testing that sessions are not merely "
            "'files touched on the same day'."
        ),
    ),
    SessionSpec(
        id="personal_misc",
        label="Personal notes (no coherent work session)",
        kind="none",
        start="2025-07-01",
        end="2026-02-28",
        description=(
            "Deliberate negative control. These files are scattered in time and "
            "topic and must NOT be clustered into a session. A session "
            "reconstructor that groups them is over-clustering, and the session "
            "accuracy metric is designed to catch that."
        ),
    ),
)


# ===========================================================================
# Corpus files
# ===========================================================================
# Paths use forward slashes; the generator translates to the platform separator.
# ---------------------------------------------------------------------------

_ML = "College/Semester7/MachineLearning"
_DBMS = "College/Semester7/DBMS"
_CAP = "College/Capstone"
_UF = "Projects/UrbanFlow"
_CAR = "Personal/Career"
_MISC = "Personal/Misc"
_DL = "Downloads"


CORPUS_FILES: tuple[FileSpec, ...] = (
    # =======================================================================
    # SESSION: ml_exam_prep  --  the corpus's central adversarial case
    # =======================================================================
    FileSpec(
        path=f"{_ML}/Exam_Timetable_Sem7.xlsx",
        kind="xlsx",
        mtime="2025-11-10T18:22:00",
        session="ml_exam_prep",
        notes=(
            "THE BRIDGE FILE. It is the only document that connects the word "
            "'exam' to the lecture PDFs. Every date here sits in a table, which "
            "is exactly the structured-context signal Phase 10 must reward."
        ),
        dates=(
            DateLabel(
                "2025-11-18",
                "meaningful",
                "18-11-2025",
                "Scheduled end-semester examination date in a timetable table.",
            ),
            DateLabel(
                "2025-11-21",
                "meaningful",
                "21-11-2025",
                "Scheduled end-semester examination date in a timetable table.",
            ),
            DateLabel(
                "2025-11-24",
                "meaningful",
                "24-11-2025",
                "The Machine Learning exam date - target of several queries.",
            ),
            DateLabel(
                "2025-11-26",
                "meaningful",
                "26-11-2025",
                "Scheduled end-semester examination date in a timetable table.",
            ),
        ),
        content=[
            (
                "Semester 7 Timetable",
                [
                    ["Date", "Day", "Subject Code", "Subject", "Session", "Hall", "Study Material"],
                    [
                        "18-11-2025",
                        "Tuesday",
                        "CS7401",
                        "Compiler Design",
                        "FN 10:00-13:00",
                        "Block A - 204",
                        "Compiler_Design_Unit5.pdf",
                    ],
                    [
                        "21-11-2025",
                        "Friday",
                        "CS7402",
                        "Computer Networks",
                        "FN 10:00-13:00",
                        "Block A - 204",
                        "CN_Unit3_Routing.pdf",
                    ],
                    [
                        "24-11-2025",
                        "Monday",
                        "CS7403",
                        "Machine Learning",
                        "FN 10:00-13:00",
                        "Block B - 112",
                        "Unit4_Ensemble_Methods.pdf; Unit3_SVM_Notes.md",
                    ],
                    [
                        "26-11-2025",
                        "Wednesday",
                        "CS7404",
                        "Cloud Computing",
                        "AN 14:00-17:00",
                        "Block B - 112",
                        "Cloud_Unit2.pdf",
                    ],
                ],
            ),
            (
                "Notes",
                [
                    ["Item", "Detail"],
                    ["Hall ticket", "Collect from department office before 15-11-2025"],
                    ["Reporting time", "30 minutes before session start"],
                    ["Prepared by", "Controller of Examinations"],
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_ML}/Unit4_Ensemble_Methods.pdf",
        kind="pdf",
        mtime="2025-11-19T21:47:00",
        session="ml_exam_prep",
        notes=(
            "CRITICAL NEGATIVE CASE. This is the file the student means by 'the "
            "PDF I studied before my ML exam', and it contains none of the words "
            "'exam', 'test', 'revision', 'timetable', 'syllabus' or 'semester'. "
            "A pure-semantic system cannot connect this file to an exam query. "
            "Only the activity session (co-edited with the timetable and the "
            "revision checklist) or the graph path through the timetable's "
            "filename reference can recover it."
        ),
        content=[
            (
                "Unit 4: Ensemble Methods",
                [
                    "Ensemble methods combine the predictions of several base estimators "
                    "in order to improve generalisation and robustness over a single "
                    "estimator. The two dominant families are averaging methods, which "
                    "build estimators independently and average their predictions, and "
                    "boosting methods, which build estimators sequentially so that each "
                    "one reduces the bias of the combined estimator.",
                    "The theoretical justification rests on the bias-variance "
                    "decomposition. Averaging reduces variance without materially "
                    "increasing bias, provided the base estimators are sufficiently "
                    "decorrelated. Boosting reduces bias by fitting successive "
                    "estimators to the residual errors of the current ensemble.",
                ],
            ),
            (
                "4.1 Bagging and Bootstrap Aggregation",
                [
                    "Bagging draws B bootstrap samples from the training set, fits one "
                    "base learner per sample, and aggregates by majority vote for "
                    "classification or by arithmetic mean for regression.",
                    "Because each bootstrap sample omits roughly 36.8 percent of the "
                    "training instances, the omitted instances form an out-of-bag set "
                    "that yields an unbiased estimate of generalisation error without a "
                    "separate holdout partition.",
                    "Bagging is most effective when the base learner has high variance "
                    "and low bias. Fully grown decision trees are the canonical choice.",
                ],
            ),
            (
                "4.2 Random Forests",
                [
                    "A random forest extends bagging by adding feature subsampling at "
                    "every split. Rather than searching all p features, the splitter "
                    "considers a random subset of size m, commonly the square root of p "
                    "for classification and p divided by three for regression.",
                    "Feature subsampling decorrelates the individual trees. Since the "
                    "variance of an average of B identically distributed variables with "
                    "pairwise correlation rho tends to rho times the individual variance "
                    "as B grows, reducing rho lowers the floor on achievable variance.",
                    "Variable importance can be estimated either by mean decrease in "
                    "impurity or by permutation importance on the out-of-bag sample. "
                    "Permutation importance is preferred when features differ in "
                    "cardinality, since impurity-based importance is biased toward "
                    "high-cardinality features.",
                ],
            ),
            (
                "4.3 Boosting: AdaBoost and Gradient Boosting",
                [
                    "AdaBoost maintains a weight distribution over training instances. "
                    "After each weak learner is fitted, the weights of misclassified "
                    "instances are increased so the next learner concentrates on them. "
                    "The final hypothesis is a weighted majority vote whose weights are "
                    "a function of each learner's error rate.",
                    "Gradient boosting generalises this by casting boosting as gradient "
                    "descent in function space. Each new learner is fitted to the "
                    "negative gradient of a differentiable loss with respect to the "
                    "current predictions, which recovers AdaBoost as the special case of "
                    "exponential loss.",
                    "The learning rate, often called shrinkage, scales each learner's "
                    "contribution. Smaller shrinkage with more learners generally "
                    "improves accuracy at the cost of training time. Shrinkage and the "
                    "number of estimators must therefore be tuned jointly.",
                ],
            ),
            (
                "4.4 Stacking and Practical Guidance",
                [
                    "Stacked generalisation trains a meta-learner on the out-of-fold "
                    "predictions of several heterogeneous base learners. Using in-fold "
                    "predictions instead leaks information and produces a meta-learner "
                    "that overfits its inputs.",
                    "In practice, random forests offer strong performance with almost no "
                    "tuning, while gradient boosting reaches higher accuracy but demands "
                    "careful tuning of depth, shrinkage and estimator count.",
                    "Worked problem: given a forest of 500 trees each with out-of-bag "
                    "accuracy near 0.72 and average pairwise correlation near 0.15, "
                    "explain why the ensemble substantially outperforms any single tree, "
                    "and state which term of the variance expression dominates as the "
                    "number of trees grows large.",
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_ML}/Unit4_Ensemble_Methods_annotated.pdf",
        kind="pdf",
        mtime="2025-11-22T23:05:00",
        session="ml_exam_prep",
        near_duplicate_of=f"{_ML}/Unit4_Ensemble_Methods.pdf",
        notes=(
            "PLANTED NEAR-DUPLICATE. Same lecture PDF with a handful of margin "
            "notes added. Phase 9 must link it to the original with a duplicate "
            "edge; Phase 20's near-duplicate detector must surface the pair."
        ),
        content=[
            (
                "Unit 4: Ensemble Methods",
                [
                    "Ensemble methods combine the predictions of several base estimators "
                    "in order to improve generalisation and robustness over a single "
                    "estimator. The two dominant families are averaging methods, which "
                    "build estimators independently and average their predictions, and "
                    "boosting methods, which build estimators sequentially so that each "
                    "one reduces the bias of the combined estimator.",
                    "NOTE: sir said the bias-variance decomposition derivation is "
                    "compulsory. Learn the full derivation, not just the statement.",
                    "The theoretical justification rests on the bias-variance "
                    "decomposition. Averaging reduces variance without materially "
                    "increasing bias, provided the base estimators are sufficiently "
                    "decorrelated. Boosting reduces bias by fitting successive "
                    "estimators to the residual errors of the current ensemble.",
                ],
            ),
            (
                "4.1 Bagging and Bootstrap Aggregation",
                [
                    "Bagging draws B bootstrap samples from the training set, fits one "
                    "base learner per sample, and aggregates by majority vote for "
                    "classification or by arithmetic mean for regression.",
                    "NOTE: remember the 36.8 percent figure, it comes from the limit of "
                    "one minus one over n, all raised to n.",
                    "Because each bootstrap sample omits roughly 36.8 percent of the "
                    "training instances, the omitted instances form an out-of-bag set "
                    "that yields an unbiased estimate of generalisation error without a "
                    "separate holdout partition.",
                    "Bagging is most effective when the base learner has high variance "
                    "and low bias. Fully grown decision trees are the canonical choice.",
                ],
            ),
            (
                "4.2 Random Forests",
                [
                    "A random forest extends bagging by adding feature subsampling at "
                    "every split. Rather than searching all p features, the splitter "
                    "considers a random subset of size m, commonly the square root of p "
                    "for classification and p divided by three for regression.",
                    "Feature subsampling decorrelates the individual trees. Since the "
                    "variance of an average of B identically distributed variables with "
                    "pairwise correlation rho tends to rho times the individual variance "
                    "as B grows, reducing rho lowers the floor on achievable variance.",
                    "NOTE: the rho question was asked last year. Expect it again.",
                ],
            ),
            (
                "4.3 Boosting: AdaBoost and Gradient Boosting",
                [
                    "AdaBoost maintains a weight distribution over training instances. "
                    "After each weak learner is fitted, the weights of misclassified "
                    "instances are increased so the next learner concentrates on them.",
                    "Gradient boosting generalises this by casting boosting as gradient "
                    "descent in function space. Each new learner is fitted to the "
                    "negative gradient of a differentiable loss with respect to the "
                    "current predictions.",
                    "The learning rate, often called shrinkage, scales each learner's "
                    "contribution. Shrinkage and the number of estimators must be tuned "
                    "jointly.",
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_ML}/Unit3_SVM_Notes.md",
        kind="md",
        mtime="2025-11-16T20:12:00",
        session="ml_exam_prep",
        notes=(
            "Second instance of the adversarial case: revision notes that never "
            "say 'exam'. Directly retrievable by a semantic query about SVMs "
            "(q03), so it also tests that context-awareness does not *hurt* "
            "straightforward semantic queries."
        ),
        content="""# Unit 3 - Support Vector Machines

## Maximal margin classifier

For linearly separable data, the separating hyperplane w.x + b = 0 is chosen to
maximise the margin, the perpendicular distance to the nearest training point of
either class. Maximising the margin is equivalent to minimising half the squared
norm of w subject to y_i (w.x_i + b) >= 1 for every i.

The points that satisfy the constraint with equality are the support vectors.
They alone determine the solution; removing any other point leaves the
hyperplane unchanged. This is why SVMs are memory-efficient at prediction time.

## Soft margin and the C parameter

Real data is rarely separable, so slack variables xi_i >= 0 permit violations.
The objective becomes half the squared norm of w plus C times the sum of the
slacks.

- Large C penalises violations heavily -> narrow margin, low bias, high variance.
- Small C tolerates violations -> wide margin, higher bias, lower variance.

C is therefore a regularisation parameter and is tuned by cross-validation.

## The kernel trick

The dual formulation depends on the data only through inner products
x_i . x_j. Replacing that inner product with a kernel K(x_i, x_j) implicitly
maps the data into a higher-dimensional feature space without ever computing
the mapping.

| Kernel | Form | When to use |
| --- | --- | --- |
| Linear | x.y | High-dimensional sparse data, e.g. text |
| Polynomial | (gamma x.y + r)^d | Feature interactions matter |
| RBF | exp(-gamma ||x-y||^2) | General purpose default |
| Sigmoid | tanh(gamma x.y + r) | Rarely; not always a valid kernel |

Mercer's condition: K is a valid kernel iff its Gram matrix is positive
semi-definite for every finite sample.

## Things to be careful about

1. Scale the features. RBF and polynomial kernels are distance-based and are
   destroyed by unscaled features.
2. gamma and C interact strongly. Search them jointly on a log grid.
3. SVMs do not output calibrated probabilities natively; Platt scaling fits a
   sigmoid to the decision values.
4. Training is roughly quadratic to cubic in the number of samples, so plain
   SVMs do not scale to very large datasets. Prefer a linear SVM with SGD there.

## Worked derivation to memorise

Start from the primal, form the Lagrangian, take stationarity conditions with
respect to w and b, substitute back, and obtain the dual. The dual is a
quadratic program in the multipliers alpha with a linear equality constraint
from the b-stationarity condition and box constraints 0 <= alpha_i <= C from
the slack terms.
""",
    ),
    FileSpec(
        path=f"{_ML}/ml_revision_checklist.txt",
        kind="txt",
        mtime="2025-11-20T22:30:00",
        session="ml_exam_prep",
        notes=(
            "Uses the word 'exam' explicitly and names the PDFs. This is the "
            "second bridge between the vocabulary of the query and the "
            "vocabulary of the target files."
        ),
        dates=(
            DateLabel(
                "2025-11-24",
                "meaningful",
                "24 Nov",
                "The ML exam date, stated next to the word 'exam'.",
            ),
            DateLabel(
                "2025-11-23",
                "meaningful",
                "23 Nov",
                "Self-imposed deadline for finishing revision - actionable.",
            ),
        ),
        content="""ML EXAM REVISION CHECKLIST
==========================
Exam: Machine Learning (CS7403), 24 Nov, forenoon, Block B - 112.
Everything must be done by 23 Nov night. No new material after that.

UNIT 3 - SVM
[x] Read Unit3_SVM_Notes.md end to end
[x] Primal -> dual derivation, written out by hand twice
[x] Kernel table memorised
[ ] Redo the gamma/C grid search question from the tutorial sheet

UNIT 4 - ENSEMBLES
[x] Read Unit4_Ensemble_Methods.pdf
[x] Bias-variance decomposition derivation
[x] Out-of-bag error, and why 36.8 percent
[ ] AdaBoost weight update worked example
[ ] Stacking: why out-of-fold predictions and not in-fold

UNIT 5 - NEURAL NETS
[ ] Backpropagation by hand on a 2-layer net
[ ] Vanishing gradient, and what ReLU actually fixes

PRACTICALS
[x] confusion_matrix_practice.py - runs, output verified
[ ] Precision/recall/F1 by hand from a confusion matrix

NOTES TO SELF
- The 36.8 percent derivation came up last year. Do not skip it.
- Sir hinted the rho/correlation question is coming back.
- Bring the hall ticket. Collected it already, it is in the blue folder.
""",
    ),
    FileSpec(
        path=f"{_ML}/confusion_matrix_practice.py",
        kind="code",
        mtime="2025-11-17T19:05:00",
        session="ml_exam_prep",
        notes="Source-code file inside an exam-prep session; tests code extraction.",
        content='''"""Practice: compute classification metrics from scratch and check against
scikit-learn. Written while revising Unit 6 (model evaluation)."""

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.datasets import make_classification
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split


def confusion_matrix(y_true, y_pred, n_classes):
    """Build an n_classes x n_classes confusion matrix, rows = true labels."""
    matrix = np.zeros((n_classes, n_classes), dtype=int)
    for actual, predicted in zip(y_true, y_pred):
        matrix[actual][predicted] += 1
    return matrix


def precision_recall_f1(matrix, cls):
    """Per-class precision, recall and F1 from a confusion matrix."""
    true_positive = matrix[cls][cls]
    false_positive = matrix[:, cls].sum() - true_positive
    false_negative = matrix[cls, :].sum() - true_positive

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    if precision + recall == 0:
        return precision, recall, 0.0
    return precision, recall, 2 * precision * recall / (precision + recall)


def main():
    X, y = make_classification(n_samples=600, n_features=12, n_informative=6,
                               n_classes=3, random_state=42)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3,
                                                        random_state=42)

    # Random forest, because Unit 4 is the one I am weakest on.
    model = RandomForestClassifier(n_estimators=200, oob_score=True, random_state=42)
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)

    print("out-of-bag score:", round(model.oob_score_, 4))

    matrix = confusion_matrix(y_test, predictions, n_classes=3)
    print("confusion matrix:")
    print(matrix)

    for cls in range(3):
        p, r, f1 = precision_recall_f1(matrix, cls)
        print(f"class {cls}: precision={p:.3f} recall={r:.3f} f1={f1:.3f}")

    # Cross-check my arithmetic against the library.
    print(classification_report(y_test, predictions, digits=3))


if __name__ == "__main__":
    main()
''',
    ),
    FileSpec(
        path=f"{_ML}/ml_lab_attendance.xlsx",
        kind="xlsx",
        mtime="2025-11-11T09:15:00",
        session="ml_exam_prep",
        notes=(
            "Contains many dates in a table, but they are attendance records - "
            "not actionable. A naive 'dates in tables are meaningful' rule "
            "would mark all of these meaningful, so this file is the control "
            "that stops the structured-context signal from dominating alone."
        ),
        dates=(
            DateLabel("2025-08-11", "incidental", "11-08-2025", "A past lab attendance record."),
            DateLabel("2025-08-18", "incidental", "18-08-2025", "A past lab attendance record."),
            DateLabel("2025-09-01", "incidental", "01-09-2025", "A past lab attendance record."),
            DateLabel("2025-09-08", "incidental", "08-09-2025", "A past lab attendance record."),
            DateLabel("2025-10-06", "incidental", "06-10-2025", "A past lab attendance record."),
        ),
        content=[
            (
                "Attendance",
                [
                    ["Date", "Experiment", "Status", "Marks"],
                    ["11-08-2025", "Linear regression from scratch", "Present", 9],
                    ["18-08-2025", "Logistic regression, gradient descent", "Present", 8],
                    ["01-09-2025", "Decision trees, entropy and gini", "Present", 10],
                    ["08-09-2025", "Support vector machines with kernels", "Absent", 0],
                    ["06-10-2025", "Random forest and out-of-bag error", "Present", 9],
                ],
            )
        ],
    ),
    # =======================================================================
    # SESSION: hackathon_urbanflow
    # =======================================================================
    FileSpec(
        path=f"{_UF}/README.md",
        kind="md",
        mtime="2025-09-13T02:40:00",
        session="hackathon_urbanflow",
        dates=(
            DateLabel(
                "2025-09-14",
                "meaningful",
                "14 September 2025",
                "Hackathon submission deadline - stated as a deadline.",
            ),
        ),
        content="""# UrbanFlow

Adaptive traffic signal timing from cheap roadside sensors.
Built at HackChennai 2025 by team Nightshift.

Submission closes 14 September 2025 at 09:00. Judging starts 10:30.

## The problem

Fixed-cycle traffic signals waste green time on empty approaches. Indian cities
mostly run fixed cycles because adaptive systems assume expensive inductive
loops or camera arrays that nobody is going to retrofit at 40,000 junctions.

## What we built

A signal controller that takes counts from a 900 rupee ultrasonic sensor pair
per approach and reallocates green time every cycle, with a fairness constraint
so no approach starves.

## Team

- Alfred Mathew - model and simulation
- Abu Ibrahim Mothi - sensor firmware and data pipeline
- Nithya Ramanathan - frontend dashboard
- Karan Velu - pitch and slides

## Running it

    python app.py --config junction_a.json

Sample sensor data is in sensor_data_sample.xlsx.

## Results on the simulator

Average wait per vehicle dropped from 41.2 s to 28.7 s on our four-approach
test junction, a 30 percent reduction. Fairness constraint kept the worst-case
approach wait under 95 s.

## What is not done

- No real hardware validation. Sensor data is synthetic.
- Fairness constraint is a hard floor, not a proper optimisation term.
- Dashboard polls; it should use websockets.
""",
    ),
    FileSpec(
        path=f"{_UF}/app.py",
        kind="code",
        mtime="2025-09-13T04:18:00",
        session="hackathon_urbanflow",
        content='''"""UrbanFlow controller entry point. HackChennai 2025.

Reads approach counts, computes a green-time allocation, and prints the
resulting signal plan. Deliberately single-file: it was 4am.
"""

import argparse
import json
from pathlib import Path

from traffic_model import GreenTimeAllocator, Junction, load_counts

MIN_GREEN_SECONDS = 8
MAX_GREEN_SECONDS = 60
CYCLE_SECONDS = 120


def parse_args():
    parser = argparse.ArgumentParser(description="UrbanFlow adaptive signal controller")
    parser.add_argument("--config", required=True, type=Path, help="junction config JSON")
    parser.add_argument("--counts", type=Path, default=None, help="override count source")
    parser.add_argument("--cycles", type=int, default=10, help="how many cycles to simulate")
    parser.add_argument("--fairness", type=float, default=0.15,
                        help="minimum share of the cycle guaranteed to each approach")
    return parser.parse_args()


def main():
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    junction = Junction.from_config(config)

    allocator = GreenTimeAllocator(
        cycle_seconds=CYCLE_SECONDS,
        min_green=MIN_GREEN_SECONDS,
        max_green=MAX_GREEN_SECONDS,
        fairness_floor=args.fairness,
    )

    total_wait = 0.0
    for cycle in range(args.cycles):
        counts = load_counts(junction, cycle, override=args.counts)
        plan = allocator.allocate(counts)
        wait = junction.simulate_cycle(plan, counts)
        total_wait += wait
        print(f"cycle {cycle:02d}  plan={plan}  avg_wait={wait:6.2f}s")

    print(f"\\nmean wait across {args.cycles} cycles: {total_wait / args.cycles:.2f}s")


if __name__ == "__main__":
    main()
''',
    ),
    FileSpec(
        path=f"{_UF}/traffic_model.py",
        kind="code",
        mtime="2025-09-13T05:52:00",
        session="hackathon_urbanflow",
        content='''"""Green-time allocation and a crude junction simulator.

The allocator is proportional-with-a-floor: green time is shared out in
proportion to demand, then clipped so that no approach drops below a fairness
floor or exceeds a saturation ceiling. Not optimal, but it is stable, it is
explainable to a judge in thirty seconds, and it never starves an approach.
"""

from dataclasses import dataclass


@dataclass
class Approach:
    """One arm of a junction."""

    name: str
    lanes: int
    saturation_flow: float = 1800.0  # vehicles per hour of green, per lane

    def capacity(self, green_seconds: float) -> float:
        return self.lanes * self.saturation_flow * green_seconds / 3600.0


@dataclass
class Junction:
    """A set of approaches sharing one signal cycle."""

    name: str
    approaches: list

    @classmethod
    def from_config(cls, config):
        approaches = [
            Approach(name=a["name"], lanes=a.get("lanes", 2)) for a in config["approaches"]
        ]
        return cls(name=config.get("name", "unnamed"), approaches=approaches)

    def simulate_cycle(self, plan, counts):
        """Return mean wait per vehicle for one cycle under `plan`."""
        total_wait = 0.0
        total_vehicles = 0
        for approach in self.approaches:
            arrivals = counts[approach.name]
            served = min(arrivals, approach.capacity(plan[approach.name]))
            leftover = max(0.0, arrivals - served)
            # Served vehicles wait on average half the red time; leftovers wait
            # a whole further cycle on top of that.
            red = sum(plan.values()) - plan[approach.name]
            total_wait += served * red / 2.0 + leftover * sum(plan.values())
            total_vehicles += arrivals
        return total_wait / max(total_vehicles, 1)


class GreenTimeAllocator:
    """Proportional green-time allocation with a fairness floor."""

    def __init__(self, cycle_seconds, min_green, max_green, fairness_floor):
        self.cycle_seconds = cycle_seconds
        self.min_green = min_green
        self.max_green = max_green
        self.fairness_floor = fairness_floor

    def allocate(self, counts):
        demand = sum(counts.values())
        if demand <= 0:
            equal = self.cycle_seconds / len(counts)
            return {name: equal for name in counts}

        floor = self.fairness_floor * self.cycle_seconds
        raw = {
            name: max(floor, self.cycle_seconds * count / demand)
            for name, count in counts.items()
        }
        raw = {n: min(self.max_green, max(self.min_green, g)) for n, g in raw.items()}

        # Renormalise so the plan still sums to one cycle after clipping.
        scale = self.cycle_seconds / sum(raw.values())
        return {name: round(green * scale, 1) for name, green in raw.items()}


def load_counts(junction, cycle, override=None):
    """Synthetic arrival counts. Real sensors were never wired up."""
    base = {"north": 34, "south": 29, "east": 51, "west": 18}
    peak = 1.0 + 0.35 * ((cycle % 5) / 4.0)
    return {
        approach.name: int(base.get(approach.name, 25) * peak)
        for approach in junction.approaches
    }
''',
    ),
    FileSpec(
        path=f"{_UF}/pitch_deck.pptx",
        kind="pptx",
        mtime="2025-09-14T07:10:00",
        session="hackathon_urbanflow",
        dates=(
            DateLabel(
                "2025-09-14",
                "meaningful",
                "14 Sept 2025",
                "Presentation/judging date stated on the title slide.",
            ),
        ),
        content=[
            (
                "UrbanFlow",
                [
                    "Adaptive traffic signals for cities that cannot afford them",
                    "Team Nightshift - HackChennai, 14 Sept 2025",
                ],
            ),
            (
                "The problem",
                [
                    "Fixed-cycle signals give green time to empty roads",
                    "Adaptive control exists, but assumes expensive infrastructure",
                    "Inductive loops: about 2 lakh rupees per junction",
                    "Indian cities have tens of thousands of junctions",
                    "So almost nothing gets retrofitted",
                ],
            ),
            (
                "Our approach",
                [
                    "Two ultrasonic sensors per approach, about 900 rupees each",
                    "Count vehicles, not classify them - keeps the problem cheap",
                    "Reallocate green time every cycle in proportion to demand",
                    "Fairness floor so a quiet approach never starves",
                ],
            ),
            (
                "Results in simulation",
                [
                    "Mean wait per vehicle: 41.2 s to 28.7 s",
                    "That is a 30 percent reduction",
                    "Worst-case approach wait stayed under 95 s",
                    "Caveat: synthetic sensor data, no hardware validation yet",
                ],
            ),
            (
                "What is next",
                [
                    "Wire up the actual sensor board",
                    "Validate on one real junction with the traffic police",
                    "Replace the hard fairness floor with a proper penalty term",
                    "Ask us about the 4am rewrite of the allocator",
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_UF}/team_notes.md",
        kind="md",
        mtime="2025-09-12T23:15:00",
        session="hackathon_urbanflow",
        dates=(
            DateLabel(
                "2025-09-12",
                "meaningful",
                "12 September",
                "Kickoff meeting, stated with a time and attendees.",
            ),
            DateLabel(
                "2025-09-14",
                "meaningful",
                "14 September",
                "Submission deadline restated in the plan.",
            ),
        ),
        content="""# Team Nightshift - working notes

## Kickoff, 12 September, 20:00

Present: Alfred Mathew, Abu Ibrahim Mothi, Nithya Ramanathan, Karan Velu.

Decided in the first hour:

- Problem: adaptive traffic signals, cheap sensors. Rejected the food-waste idea
  because three other teams announced it in the opening pitch round.
- Scope hard limit: simulation only. No hardware. If we try to wire the sensor
  board we will spend the whole night debugging serial ports.
- Deadline is 14 September 09:00. Working backwards: code frozen at 06:00,
  slides done by 07:30, one full dry run at 08:00.

## Split

| Person | Owns |
| --- | --- |
| Alfred | allocator + simulator (traffic_model.py, app.py) |
| Abu | data pipeline, sensor_data_sample.xlsx |
| Nithya | dashboard |
| Karan | pitch_deck.pptx, demo script |

## 13 September, 02:30 - allocator is wrong

Pure proportional allocation starves the west approach completely when east is
busy. Wait time on west goes above 200 s. Added a fairness floor of 15 percent
of the cycle. Numbers look sane now.

## 13 September, 05:40 - renormalisation bug

After clipping to min/max green the plan no longer summed to the cycle length,
so the simulator was reporting impossibly good waits. Fixed by rescaling after
clipping. Mean wait went from a fake 19 s up to an honest 28.7 s. Karan is
annoyed because the slide already said 19.

## Open, going into the morning

- Nithya's dashboard polls every 2 s. Ugly but fine for a demo.
- Do NOT claim hardware validation in the pitch. Judges will ask.
""",
    ),
    FileSpec(
        path=f"{_UF}/submission_checklist.txt",
        kind="txt",
        mtime="2025-09-14T06:05:00",
        session="hackathon_urbanflow",
        dates=(
            DateLabel(
                "2025-09-14",
                "meaningful",
                "14 Sep",
                "Hard submission deadline with a time - the most actionable date in this session.",
            ),
        ),
        content="""SUBMISSION CHECKLIST - HackChennai
Deadline: 14 Sep 09:00 sharp. Portal closes automatically.

[x] Repo pushed, main branch, no secrets in history
[x] README.md explains the problem and how to run it
[x] pitch_deck.pptx exported to PDF as backup
[x] Demo video recorded (2 min 40 s, under the 3 min limit)
[x] sensor_data_sample.xlsx included so judges can rerun
[ ] Team registration IDs pasted into the portal form
[ ] Final dry run at 08:00

DO NOT FORGET
- Slide 4 still says 19 s. Fix it to 28.7 s before submitting.
- Say clearly that sensor data is synthetic. Do not let them assume hardware.
- Karan has the HDMI adapter.
""",
    ),
    FileSpec(
        path=f"{_UF}/sensor_data_sample.xlsx",
        kind="xlsx",
        mtime="2025-09-13T01:22:00",
        session="hackathon_urbanflow",
        notes="Structured data with timestamps that are observations, not commitments.",
        dates=(
            DateLabel(
                "2025-09-13",
                "incidental",
                "13-09-2025",
                "Timestamp on a sensor reading - a record of the past, not an obligation.",
            ),
        ),
        content=[
            (
                "Readings",
                [
                    ["Timestamp", "Approach", "Vehicle Count", "Sensor ID", "Confidence"],
                    ["13-09-2025 01:00", "north", 34, "US-N-01", 0.91],
                    ["13-09-2025 01:00", "south", 29, "US-S-01", 0.88],
                    ["13-09-2025 01:00", "east", 51, "US-E-01", 0.93],
                    ["13-09-2025 01:00", "west", 18, "US-W-01", 0.87],
                    ["13-09-2025 01:02", "north", 37, "US-N-01", 0.90],
                    ["13-09-2025 01:02", "south", 31, "US-S-01", 0.89],
                    ["13-09-2025 01:02", "east", 55, "US-E-01", 0.92],
                    ["13-09-2025 01:02", "west", 16, "US-W-01", 0.85],
                ],
            ),
            (
                "Junction",
                [
                    ["Field", "Value"],
                    ["Name", "Velachery Junction (simulated)"],
                    ["Approaches", 4],
                    ["Cycle seconds", 120],
                    ["Data source", "SYNTHETIC - generated, not measured"],
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_UF}/demo_script.md",
        kind="md",
        mtime="2025-09-14T07:45:00",
        session="hackathon_urbanflow",
        content="""# Demo script - 2 minutes 30

**0:00-0:20 - the hook**
"Every one of you sat at a red light this morning with no cross traffic. That
light was on a fixed cycle written down in the nineties."

**0:20-0:50 - the problem**
Adaptive control is a solved problem. It is solved with hardware nobody buys.
Show the cost slide: 2 lakh per junction, tens of thousands of junctions.

**0:50-1:30 - what we did**
Live run of `python app.py --config junction_a.json`. Point at the plan changing
between cycles as east demand rises. Say the allocator is proportional with a
fairness floor - do not go into the maths unless asked.

**1:30-2:00 - the number**
41.2 seconds down to 28.7 seconds mean wait. Thirty percent.
Immediately say: simulation, synthetic sensor data, no hardware yet. Judges
respect the caveat more than they punish it.

**2:00-2:30 - what is next**
One real junction. Traffic police contact via Nithya's uncle. Sensor board is
ordered, arrives next week.

**If asked "why not cameras"**
Cameras need power, network, and a GPU at the edge, plus they are a privacy
fight in every ward. Ultrasonic counting is dumber and that is the point.
""",
    ),
    # =======================================================================
    # SESSION: dbms_assignment
    # =======================================================================
    FileSpec(
        path=f"{_DBMS}/assignment_brief.pdf",
        kind="pdf",
        mtime="2025-10-06T11:30:00",
        session="dbms_assignment",
        dates=(
            DateLabel(
                "2025-10-18",
                "meaningful",
                "18 October 2025",
                "Assignment submission deadline, stated as 'due'.",
            ),
            DateLabel(
                "2025-10-06",
                "meaningful",
                "6 October 2025",
                "Date the assignment was issued - bounds the work period.",
            ),
        ),
        content=[
            (
                "CS7405 Database Management Systems - Assignment 2",
                [
                    "Issued: 6 October 2025. Due: 18 October 2025, 23:59, via the "
                    "department portal. Late submissions lose 10 percent per day and "
                    "are not accepted after 21 October 2025.",
                    "Weightage: 15 percent of the internal assessment.",
                    "This assignment is individual. Plagiarism will be checked against "
                    "prior submissions and against each other.",
                ],
            ),
            (
                "Task 1 - Functional dependencies (10 marks)",
                [
                    "Given the relation ORDERS(order_id, customer_id, customer_name, "
                    "customer_city, product_id, product_name, unit_price, quantity, "
                    "order_date), identify all non-trivial functional dependencies and "
                    "justify each one in a sentence.",
                    "State the candidate keys and show your working. A candidate key "
                    "asserted without an attribute-closure computation earns no marks.",
                ],
            ),
            (
                "Task 2 - Normalisation to BCNF (20 marks)",
                [
                    "Decompose ORDERS into Boyce-Codd Normal Form. At each step, state "
                    "which dependency violates the current normal form and why.",
                    "Prove that your decomposition is lossless using the chase test or "
                    "the binary decomposition rule.",
                    "State explicitly whether your decomposition is dependency "
                    "preserving. If it is not, say which dependency was lost and "
                    "explain why BCNF sometimes forces that trade-off.",
                ],
            ),
            (
                "Task 3 - Implementation (15 marks)",
                [
                    "Write the CREATE TABLE statements for your final schema with "
                    "appropriate primary and foreign key constraints.",
                    "Provide five INSERT statements per table with realistic data, and "
                    "three SELECT queries that would have required a join in the "
                    "normalised schema but not in the original.",
                    "Submit the SQL as a separate .sql file alongside the report.",
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_DBMS}/Assignment2_Normalization_draft.docx",
        kind="docx",
        mtime="2025-10-12T16:40:00",
        session="dbms_assignment",
        notes="Draft half of the planted near-duplicate pair.",
        dates=(
            DateLabel(
                "2025-10-18",
                "meaningful",
                "18 October 2025",
                "The submission deadline, restated in the cover block.",
            ),
        ),
        content=[
            ("h1", "CS7405 Assignment 2 - Normalization"),
            ("p", "Alfred Mathew, 43110050. Due 18 October 2025. DRAFT - not submitted."),
            ("h2", "Task 1: Functional dependencies"),
            (
                "p",
                "The relation ORDERS holds order, customer and product information in a "
                "single table. The following non-trivial functional dependencies hold.",
            ),
            ("bullet", "order_id -> customer_id, order_date"),
            ("bullet", "customer_id -> customer_name, customer_city"),
            ("bullet", "product_id -> product_name, unit_price"),
            ("bullet", "order_id, product_id -> quantity"),
            (
                "p",
                "Computing the closure of {order_id, product_id} yields every attribute "
                "of the relation, so it is a superkey. Neither attribute alone has a "
                "closure covering all attributes, so the pair is a candidate key.",
            ),
            ("h2", "Task 2: Decomposition"),
            (
                "p",
                "customer_id -> customer_name, customer_city violates BCNF because "
                "customer_id is not a superkey of ORDERS. Split out CUSTOMER.",
            ),
            (
                "p",
                "TODO: I still need to do the lossless join proof properly. Right now I "
                "have only asserted it. Also need to check dependency preservation.",
            ),
            ("h2", "Task 3: SQL"),
            ("p", "See normalization_examples.sql. Not finished yet."),
        ],
    ),
    FileSpec(
        path=f"{_DBMS}/Assignment2_Normalization_final.docx",
        kind="docx",
        mtime="2025-10-17T22:55:00",
        session="dbms_assignment",
        near_duplicate_of=f"{_DBMS}/Assignment2_Normalization_draft.docx",
        notes=(
            "PLANTED NEAR-DUPLICATE. Substantially the same document as the "
            "draft with the TODOs resolved. Tests that duplicate detection is "
            "not so strict it only catches byte-identical copies."
        ),
        dates=(
            DateLabel(
                "2025-10-18",
                "meaningful",
                "18 October 2025",
                "The submission deadline, restated in the cover block.",
            ),
        ),
        content=[
            ("h1", "CS7405 Assignment 2 - Normalization"),
            ("p", "Alfred Mathew, 43110050. Due 18 October 2025. Submitted 17 October 2025."),
            ("h2", "Task 1: Functional dependencies"),
            (
                "p",
                "The relation ORDERS holds order, customer and product information in a "
                "single table. The following non-trivial functional dependencies hold.",
            ),
            ("bullet", "order_id -> customer_id, order_date"),
            ("bullet", "customer_id -> customer_name, customer_city"),
            ("bullet", "product_id -> product_name, unit_price"),
            ("bullet", "order_id, product_id -> quantity"),
            (
                "p",
                "Computing the closure of {order_id, product_id} yields every attribute "
                "of the relation, so it is a superkey. Neither attribute alone has a "
                "closure covering all attributes, so the pair is a candidate key.",
            ),
            ("h2", "Task 2: Decomposition"),
            (
                "p",
                "customer_id -> customer_name, customer_city violates BCNF because "
                "customer_id is not a superkey of ORDERS. Split out CUSTOMER.",
            ),
            (
                "p",
                "product_id -> product_name, unit_price likewise violates BCNF. Split "
                "out PRODUCT. The residual relation is ORDER_LINE(order_id, product_id, "
                "quantity) together with ORDER(order_id, customer_id, order_date).",
            ),
            (
                "p",
                "Losslessness: each binary decomposition splits on an attribute set "
                "that is a key of one of the two resulting relations, so by the binary "
                "decomposition rule every step is lossless, and losslessness composes.",
            ),
            (
                "p",
                "Dependency preservation: all four dependencies survive the "
                "decomposition, each falling entirely within one resulting relation. "
                "This decomposition is therefore both lossless and dependency "
                "preserving, which is not guaranteed for BCNF in general.",
            ),
            ("h2", "Task 3: SQL"),
            (
                "p",
                "The full schema, constraints, sample data and the three join queries "
                "are in normalization_examples.sql, submitted alongside this report.",
            ),
        ],
    ),
    FileSpec(
        path=f"{_DBMS}/normalization_examples.sql",
        kind="code",
        mtime="2025-10-16T20:10:00",
        session="dbms_assignment",
        content="""-- CS7405 Assignment 2, Task 3
-- Final BCNF schema for the ORDERS relation, with constraints and sample data.

DROP TABLE IF EXISTS order_line;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS product;
DROP TABLE IF EXISTS customer;

-- customer_id -> customer_name, customer_city
CREATE TABLE customer (
    customer_id    INTEGER PRIMARY KEY,
    customer_name  TEXT    NOT NULL,
    customer_city  TEXT    NOT NULL
);

-- product_id -> product_name, unit_price
CREATE TABLE product (
    product_id    INTEGER PRIMARY KEY,
    product_name  TEXT    NOT NULL,
    unit_price    NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0)
);

-- order_id -> customer_id, order_date
CREATE TABLE orders (
    order_id     INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customer(customer_id),
    order_date   DATE    NOT NULL
);

-- (order_id, product_id) -> quantity
CREATE TABLE order_line (
    order_id    INTEGER NOT NULL REFERENCES orders(order_id),
    product_id  INTEGER NOT NULL REFERENCES product(product_id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_id)
);

INSERT INTO customer VALUES
    (1, 'Priya Raghavan', 'Chennai'),
    (2, 'Imran Sheikh',   'Hyderabad'),
    (3, 'Meera Nair',     'Kochi'),
    (4, 'Rohit Deshmukh', 'Pune'),
    (5, 'Ananya Bose',    'Kolkata');

INSERT INTO product VALUES
    (10, 'Mechanical keyboard', 4599.00),
    (11, 'USB-C hub',            1299.00),
    (12, 'Monitor arm',          2750.00),
    (13, 'Noise cancelling headphones', 8990.00),
    (14, 'Laptop stand',          899.00);

INSERT INTO orders VALUES
    (100, 1, '2025-09-02'),
    (101, 2, '2025-09-07'),
    (102, 1, '2025-09-19'),
    (103, 3, '2025-10-01'),
    (104, 5, '2025-10-04');

INSERT INTO order_line VALUES
    (100, 10, 1), (100, 14, 2),
    (101, 11, 3),
    (102, 13, 1),
    (103, 12, 2), (103, 14, 1),
    (104, 10, 1);

-- Query 1: joins that the unnormalised relation would not have needed.
SELECT c.customer_name, c.customer_city, o.order_date, p.product_name, ol.quantity
FROM order_line ol
JOIN orders   o ON o.order_id    = ol.order_id
JOIN customer c ON c.customer_id = o.customer_id
JOIN product  p ON p.product_id  = ol.product_id
ORDER BY o.order_date, c.customer_name;

-- Query 2: order value, which now requires reaching into product.
SELECT o.order_id, SUM(ol.quantity * p.unit_price) AS order_value
FROM order_line ol
JOIN orders  o ON o.order_id   = ol.order_id
JOIN product p ON p.product_id = ol.product_id
GROUP BY o.order_id
ORDER BY order_value DESC;

-- Query 3: spend per city.
SELECT c.customer_city, SUM(ol.quantity * p.unit_price) AS city_spend
FROM order_line ol
JOIN orders   o ON o.order_id    = ol.order_id
JOIN customer c ON c.customer_id = o.customer_id
JOIN product  p ON p.product_id  = ol.product_id
GROUP BY c.customer_city
ORDER BY city_spend DESC;
""",
    ),
    FileSpec(
        path=f"{_DBMS}/dbms_lab_record.xlsx",
        kind="xlsx",
        mtime="2025-10-08T14:20:00",
        session="dbms_assignment",
        dates=(
            DateLabel("2025-08-05", "incidental", "05-08-2025", "Past lab session record."),
            DateLabel("2025-08-19", "incidental", "19-08-2025", "Past lab session record."),
            DateLabel("2025-09-16", "incidental", "16-09-2025", "Past lab session record."),
            DateLabel(
                "2025-10-21",
                "meaningful",
                "21-10-2025",
                "Lab record submission deadline - forward-looking and actionable.",
            ),
        ),
        content=[
            (
                "Lab Record",
                [
                    ["Date", "Experiment", "Status", "Marks"],
                    ["05-08-2025", "DDL and DML basics", "Completed", 10],
                    ["19-08-2025", "Joins and subqueries", "Completed", 9],
                    ["16-09-2025", "Views, indexes, and query plans", "Completed", 10],
                    ["07-10-2025", "Normalization worked examples", "Completed", 9],
                ],
            ),
            (
                "Submission",
                [
                    ["Item", "Detail"],
                    ["Lab record due", "21-10-2025"],
                    ["Signed by", "Pending - staff signature required"],
                    ["Total marks", 38],
                ],
            ),
        ],
    ),
    # =======================================================================
    # SESSION: capstone_contextfs
    # =======================================================================
    FileSpec(
        path=f"{_CAP}/ContextFS_Proposal.docx",
        kind="docx",
        mtime="2026-01-19T15:30:00",
        session="capstone_contextfs",
        dates=(
            DateLabel(
                "2026-01-23",
                "meaningful",
                "23 January 2026",
                "Proposal submission deadline stated in the cover block.",
            ),
            DateLabel(
                "2026-02-06",
                "meaningful",
                "6 February 2026",
                "Scheduled Review 1 date.",
            ),
        ),
        content=[
            ("h1", "ContextFS: Context-Aware, Time-Intelligent File Retrieval"),
            (
                "p",
                "Final Year Project Proposal. Alfred Mathew (43110050) and Abu Ibrahim "
                "Mothi (43110024). Supervisor: Dr. Murari Devakannan Kamalesh. "
                "Submission deadline 23 January 2026. Review 1 on 6 February 2026.",
            ),
            ("h2", "1. Problem statement"),
            (
                "p",
                "Filesystems index paths and, at best, content. Users do not remember "
                "either. They remember activities, deadlines and projects: 'the PDF I "
                "studied before my ML exam', 'the slides from the hackathon weekend'. "
                "Retrieval systems answer content queries; users ask memory queries.",
            ),
            ("h2", "2. Proposed contribution"),
            (
                "p",
                "A retrieval layer that combines four signals rather than one: semantic "
                "similarity, an inter-file relationship graph, reconstructed activity "
                "sessions, and a timeline built only from dates classified as "
                "meaningful rather than incidental.",
            ),
            (
                "bullet",
                "Meaningful vs incidental date classification, so a deadline and a "
                "birthday are not treated as the same kind of fact.",
            ),
            (
                "bullet",
                "Activity session reconstruction, so a file can be retrieved by what "
                "the user was doing when they used it, not only by what it says.",
            ),
            ("bullet", "Explanations attached to every result, not a similarity score alone."),
            ("h2", "3. Method"),
            (
                "p",
                "Local-first pipeline: scan, extract, entity-tag with spaCy, embed with "
                "sentence-transformers into LanceDB, build a NetworkX relationship "
                "graph, classify dates, reconstruct sessions, then retrieve by weighted "
                "hybrid ranking over graph traversal from query seed nodes.",
            ),
            ("h2", "4. Evaluation"),
            (
                "p",
                "A pure-semantic retrieval system is implemented as a baseline and run "
                "side by side on the same query set. Metrics: Precision@K, Recall@K, "
                "MRR, query latency, index build time, incremental update time, plus "
                "date-classification precision/recall and session accuracy. An ablation "
                "study isolates the marginal contribution of each layer.",
            ),
            ("h2", "5. Known risks"),
            (
                "p",
                "The chief risk is that the evaluation corpus is synthetic and authored "
                "by the same people building the system, which validates correctness "
                "but not generalisability. A real-corpus validation plan is required "
                "before any claim of external validity.",
            ),
        ],
    ),
    FileSpec(
        path=f"{_CAP}/literature_survey.md",
        kind="md",
        mtime="2026-01-28T18:05:00",
        session="capstone_contextfs",
        content="""# Literature survey

## 1. Keyword and metadata search

Windows Search, Spotlight, Everything, recoll. Fast, exact, and completely
dependent on the user remembering a token that literally occurs in the file or
its name. Everything indexes the MFT and is astonishingly fast, but it searches
filenames only. None of them model why a file mattered.

**Gap:** no notion of context. A file is a bag of tokens at a path.

## 2. Semantic / vector retrieval

Dense retrieval with sentence encoders. Handles paraphrase, so "car" finds
"automobile". This is the baseline we must beat.

**Gap that matters for us:** semantic retrieval can only match what the document
*says*. If the lecture PDF never contains the word "exam", no embedding of the
query "the PDF I studied before my exam" will be close to it, because the
relationship is external to the document.

## 3. Graph-based retrieval

GraphRAG, HippoRAG, LightRAG. Build an entity or passage graph and traverse it
rather than doing flat nearest-neighbour search. HippoRAG's personalised
PageRank over an entity graph is the closest relative of what we do at the
retrieval step.

**Gap:** these graphs are built over *content* relations - shared entities,
co-occurrence, citation. None of them carry activity or time as first-class
node types.

## 4. Hierarchical retrieval

RAPTOR builds a recursive summary tree over chunks and retrieves at multiple
levels of abstraction. We borrow the tree, and the idea that a summary node is
a legitimate retrieval target.

**Gap:** the hierarchy is topical only. A folder is not a project and a project
is not a work session.

## 5. Personal information management (PIM)

This is the weakest part of this survey and it is the part a CHIIR reviewer will
care about most. The PIM literature on re-finding - Teevan, Elsweiler, Jones,
Bergman - established decades ago that people re-find by context and episodic
memory, not by content, and that they prefer navigating to searching. That
literature motivates our entire problem statement.

**Honest status:** we are citing the existence of this line of work rather than
engaging with it in depth. Before submission this section needs real reading,
not a list. Flagged as an open weakness in the project record.

## Where ContextFS sits

Nothing in 1-4 combines content, relationship, activity, and time inside one
retrieval architecture. The individual ingredients - embeddings, graphs,
summary trees - are all prior art and we claim none of them. The combination,
and specifically the activity and meaningful-date layers, is the contribution.
""",
    ),
    FileSpec(
        path=f"{_CAP}/supervisor_meeting_notes.md",
        kind="md",
        mtime="2026-02-11T17:20:00",
        session="capstone_contextfs",
        dates=(
            DateLabel(
                "2026-01-16",
                "meaningful",
                "16 January 2026",
                "A supervisor meeting that took place - an attended appointment.",
            ),
            DateLabel(
                "2026-02-06",
                "meaningful",
                "6 February 2026",
                "Review 1 presentation date.",
            ),
            DateLabel(
                "2026-02-11",
                "meaningful",
                "11 February 2026",
                "A supervisor meeting with an agenda and action items.",
            ),
            DateLabel(
                "2026-03-13",
                "meaningful",
                "13 March 2026",
                "Review 2 date, agreed in the meeting.",
            ),
        ),
        content="""# Supervisor meetings - Dr. Murari Devakannan Kamalesh

## 16 January 2026, 11:00, staff room

Present: Alfred Mathew, Abu Ibrahim Mothi, Dr. Murari.

- Showed the draft proposal. Sir's main objection: the novelty claim was too
  broad. "Combining embeddings and graphs is not novel. Say precisely which
  part nobody has done."
- Narrowed the claim to two things: meaningful-vs-incidental date
  classification, and activity-session-aware retrieval.
- He asked directly what the baseline is. We did not have a firm answer. Action:
  specify the baseline as flat semantic retrieval over the same embeddings, so
  the only difference is the context layers.
- Dept HOD Dr. L. Lakshmanan wants proposals in before 23 January.

## 6 February 2026 - Review 1

Panel: Dr. Murari, plus two internal examiners.

- Presentation went fine. The question that hurt: "how do you know a date is a
  deadline and not a birthday?" We described the four signals but had no
  numbers. Panel accepted the design, asked for precision and recall on that
  classifier by Review 2.
- Second question: "your dataset is made by you, so of course it works." Fair.
  Action: write a real-data migration plan even if we cannot execute it.

## 11 February 2026, 14:30

- Reported that the corpus generator is done and the ground truth is separate
  from the files, so the labels cannot leak into the index.
- Sir's warning: do not let the GUI eat the evaluation. "The examiners will
  spend four minutes on your interface and forty on your numbers."
- Agreed Review 2 is on 13 March 2026. Deliverables for it: date classifier
  precision/recall, session accuracy, and the baseline comparison table.

## Standing action items

1. Ablation study must isolate each layer - not just full vs baseline.
2. Report metrics broken down by query type, not only in aggregate.
3. Be explicit in the report about statistical significance on a corpus this
   small. Sir said an honest limitation reads better than an inflated claim.
""",
    ),
    FileSpec(
        path=f"{_CAP}/review1_slides.pptx",
        kind="pptx",
        mtime="2026-02-05T21:40:00",
        session="capstone_contextfs",
        dates=(
            DateLabel(
                "2026-02-06",
                "meaningful",
                "6 February 2026",
                "The review presentation date, on the title slide.",
            ),
        ),
        content=[
            (
                "ContextFS",
                [
                    "Context-aware, time-intelligent file retrieval",
                    "Review 1 - 6 February 2026",
                    "Alfred Mathew, Abu Ibrahim Mothi",
                    "Guide: Dr. Murari Devakannan Kamalesh",
                ],
            ),
            (
                "The gap",
                [
                    "Filesystems index paths. Search engines index content.",
                    "Users remember neither.",
                    "They remember: exams, deadlines, hackathons, projects",
                    "Example: 'the PDF I studied before my ML exam'",
                    "That PDF does not contain the word exam",
                ],
            ),
            (
                "Four layers, not one",
                [
                    "Semantic: what the document says",
                    "Relationship graph: what it is connected to",
                    "Activity sessions: what you were doing when you used it",
                    "Timeline: which dates in it actually matter",
                ],
            ),
            (
                "Meaningful vs incidental dates",
                [
                    "A deadline and a birth year are not the same kind of date",
                    "Four signals: keyword proximity, structured context,",
                    "  metadata consistency, cross-file recurrence",
                    "Weighted into a 0-1 relevance score",
                    "Only high-scoring dates become timeline nodes",
                ],
            ),
            (
                "Evaluation plan",
                [
                    "Baseline: flat semantic retrieval, same embeddings",
                    "Precision@K, Recall@K, MRR, latency",
                    "Date classification precision and recall",
                    "Session reconstruction accuracy",
                    "Ablation: turn each layer off, measure what is lost",
                ],
            ),
            (
                "Known limitations",
                [
                    "Corpus is synthetic and authored by us",
                    "Corpus is small - significance testing will be limited",
                    "PIM literature grounding is still thin",
                    "Real-data validation plan needed before any external claim",
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_CAP}/evaluation_plan.xlsx",
        kind="xlsx",
        mtime="2026-02-14T12:10:00",
        session="capstone_contextfs",
        dates=(
            DateLabel(
                "2026-02-20",
                "meaningful",
                "20-02-2026",
                "Milestone due date in a project plan table.",
            ),
            DateLabel(
                "2026-03-02",
                "meaningful",
                "02-03-2026",
                "Milestone due date in a project plan table.",
            ),
            DateLabel(
                "2026-03-13",
                "meaningful",
                "13-03-2026",
                "Review 2 date - a hard external commitment.",
            ),
            DateLabel(
                "2026-03-20",
                "meaningful",
                "20-03-2026",
                "Final report submission date.",
            ),
        ),
        content=[
            (
                "Milestones",
                [
                    ["Milestone", "Owner", "Due", "Status"],
                    ["Corpus + ground truth frozen", "Alfred", "20-02-2026", "In progress"],
                    ["Date classifier evaluated", "Alfred", "02-03-2026", "Not started"],
                    ["Session accuracy measured", "Abu", "02-03-2026", "Not started"],
                    ["Baseline vs full comparison", "Alfred", "09-03-2026", "Not started"],
                    ["Review 2 presentation", "Both", "13-03-2026", "Not started"],
                    ["Final report", "Both", "20-03-2026", "Not started"],
                ],
            ),
            (
                "Metrics",
                [
                    ["Metric", "Applies to", "Target", "Notes"],
                    ["Precision@1", "Retrieval", "Beat baseline", "Primary quality metric"],
                    ["Recall@10", "Retrieval", "Beat baseline", ""],
                    ["MRR", "Retrieval", "Beat baseline", "Rank-sensitive"],
                    ["Query latency p50", "Retrieval", "Under 1 s", "On the dev laptop"],
                    ["Date precision", "Temporal", "Report honestly", "Small sample"],
                    ["Date recall", "Temporal", "Report honestly", "Small sample"],
                    ["Session accuracy", "Activity", "Report honestly", "6 sessions only"],
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_CAP}/prototype_scanner.py",
        kind="code",
        mtime="2026-02-02T23:12:00",
        session="capstone_contextfs",
        content='''"""Throwaway prototype of the file walker, written before the real one.

Kept in the corpus deliberately: it is topically near-identical to the capstone
proposal and to the real scanner, so it tests whether the graph links files by
topic across formats (docx proposal <-> py prototype).
"""

import hashlib
import os
from pathlib import Path

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
INTERESTING = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".py"}


def file_hash(path, chunk_size=1 << 16):
    """Content hash, streamed so a big file does not land in memory at once."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def walk(root):
    """Yield (path, size, mtime, hash) for every interesting file under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in INTERESTING:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            yield path, stat.st_size, stat.st_mtime, file_hash(path)


if __name__ == "__main__":
    import sys

    total = 0
    for path, size, mtime, digest in walk(sys.argv[1]):
        total += 1
        print(f"{digest[:12]}  {size:>9}  {path}")
    print(f"\\n{total} files")
''',
    ),
    FileSpec(
        path=f"{_CAP}/references.txt",
        kind="txt",
        mtime="2026-01-30T13:45:00",
        session="capstone_contextfs",
        notes=(
            "Contains publication years - the archetypal incidental date. A "
            "bibliography is dense with dates and none of them are actionable."
        ),
        dates=(
            DateLabel("2024-01-01", "incidental", "2024", "A paper's publication year."),
            DateLabel("2019-01-01", "incidental", "2019", "A paper's publication year."),
            DateLabel("2004-01-01", "incidental", "2004", "A paper's publication year."),
            DateLabel("2003-01-01", "incidental", "2003", "A paper's publication year."),
            DateLabel("2007-01-01", "incidental", "2007", "A paper's publication year."),
            DateLabel("2005-01-01", "incidental", "2005", "A paper's publication year."),
            DateLabel("1998-01-01", "incidental", "1998", "A paper's publication year."),
        ),
        content="""WORKING REFERENCE LIST (not formatted yet, do before final report)

[1] Sarthi et al. RAPTOR: Recursive Abstractive Processing for Tree-Organized
    Retrieval. 2024. -- the summary-tree idea we borrow.

[2] Edge et al. From Local to Global: A Graph RAG Approach to Query-Focused
    Summarization. 2024. -- graph construction over content.

[3] Gutierrez et al. HippoRAG: Neurobiologically Inspired Long-Term Memory for
    LLMs. 2024. -- personalised PageRank over an entity graph.

[4] Reimers and Gurevych. Sentence-BERT. 2019. -- the embedding backbone.

[5] Teevan, Alvarado, Ackerman, Karger. The Perfect Search Engine Is Not Enough:
    A Study of Orienteering Behavior in Directed Search. 2004. -- people navigate
    rather than search, and re-find by context.

[6] Dumais et al. Stuff I've Seen: A System for Personal Information Retrieval
    and Re-Use. 2003. -- the canonical personal re-finding system.

[7] Elsweiler and Ruthven. Towards Task-Based Personal Information Management
    Evaluations. 2007. -- how to evaluate re-finding properly. READ THIS ONE
    PROPERLY, sir will ask about evaluation methodology.

[8] Bergman et al. The user-subjective approach to personal information
    management systems. 2005.

[9] Brin and Page. The Anatomy of a Large-Scale Hypertextual Web Search Engine.
    1998. -- for the PageRank background in the graph section.

NOTE TO SELF: items 5-8 are the PIM grounding that the survey is currently thin
on. Sir flagged this. Do not pad the list; actually read them.
""",
    ),
    # =======================================================================
    # SESSION: internship_apps
    # =======================================================================
    FileSpec(
        path=f"{_CAR}/Resume_Alfred_Mathew.docx",
        kind="docx",
        mtime="2025-08-06T19:30:00",
        session="internship_apps",
        content=[
            ("h1", "Alfred Mathew"),
            (
                "p",
                "B.E. Computer Science and Engineering, final year. "
                "alfred.mathew@example.edu | Chennai",
            ),
            ("h2", "Education"),
            (
                "p",
                "B.E. Computer Science and Engineering. CGPA 8.7 through semester 6. "
                "Relevant coursework: Machine Learning, Database Management Systems, "
                "Computer Networks, Compiler Design, Cloud Computing.",
            ),
            ("h2", "Projects"),
            (
                "bullet",
                "UrbanFlow - adaptive traffic signal control from low-cost ultrasonic "
                "sensors. Built at HackChennai. Reduced mean simulated wait time by 30 "
                "percent against a fixed-cycle baseline.",
            ),
            (
                "bullet",
                "ContextFS - context-aware personal file retrieval combining semantic "
                "search with an activity and timeline model. Final year project.",
            ),
            (
                "bullet",
                "Course project on database normalization with a full BCNF "
                "decomposition and lossless-join proof.",
            ),
            ("h2", "Skills"),
            (
                "p",
                "Python, SQL, C, Java. scikit-learn, pandas, NetworkX. Git, Linux, "
                "SQLite, PostgreSQL. Comfortable with information retrieval and "
                "applied machine learning.",
            ),
            ("h2", "Other"),
            (
                "p",
                "Reads too much about search systems. Maintains a personal wiki that "
                "is, ironically, hard to search.",
            ),
        ],
    ),
    FileSpec(
        path=f"{_CAR}/cover_letter_zoho.docx",
        kind="docx",
        mtime="2025-08-12T21:05:00",
        session="internship_apps",
        dates=(
            DateLabel(
                "2025-08-18",
                "meaningful",
                "18 August 2025",
                "Application closing date, stated as a deadline.",
            ),
        ),
        content=[
            ("h1", "Application for Software Engineering Internship - Zoho Corporation"),
            ("p", "Alfred Mathew. Submitted before the 18 August 2025 closing date."),
            (
                "p",
                "I am applying for the software engineering internship at Zoho. My "
                "interest is specifically in your work on search and indexing within "
                "the Workplace suite, which is close to what I have spent the last "
                "year building for myself.",
            ),
            (
                "p",
                "At HackChennai I built UrbanFlow, an adaptive traffic signal "
                "controller that reallocates green time from cheap ultrasonic sensor "
                "counts. The interesting part was not the model but the constraint: we "
                "had to make it work with hardware a municipal corporation would "
                "actually buy, which forced a much simpler design than the literature "
                "assumes.",
            ),
            (
                "p",
                "My final year project, ContextFS, is a personal file retrieval system "
                "that treats retrieval as a memory problem rather than a content "
                "problem. It reconstructs work sessions from file activity and "
                "distinguishes dates that are commitments from dates that are merely "
                "mentioned. I would be glad to talk about the evaluation design, which "
                "is the part I have found hardest.",
            ),
            (
                "p",
                "I am available from May 2026 and can relocate to Chennai. Thank you "
                "for considering this application.",
            ),
        ],
    ),
    FileSpec(
        path=f"{_CAR}/application_tracker.xlsx",
        kind="xlsx",
        mtime="2025-08-20T18:45:00",
        session="internship_apps",
        dates=(
            DateLabel(
                "2025-08-18",
                "meaningful",
                "18-08-2025",
                "Application closing date in a deadline column.",
            ),
            DateLabel(
                "2025-08-25",
                "meaningful",
                "25-08-2025",
                "Application closing date in a deadline column.",
            ),
            DateLabel(
                "2025-09-01",
                "meaningful",
                "01-09-2025",
                "Application closing date in a deadline column.",
            ),
            DateLabel(
                "2025-08-27",
                "meaningful",
                "27-08-2025",
                "A scheduled interview - an appointment.",
            ),
        ),
        content=[
            (
                "Applications",
                [
                    ["Company", "Role", "Applied On", "Deadline", "Status", "Next Step"],
                    [
                        "Zoho Corporation",
                        "SDE Intern",
                        "12-08-2025",
                        "18-08-2025",
                        "Submitted",
                        "Online test 27-08-2025",
                    ],
                    [
                        "Freshworks",
                        "Backend Intern",
                        "14-08-2025",
                        "25-08-2025",
                        "Submitted",
                        "Awaiting response",
                    ],
                    [
                        "Postman",
                        "Platform Intern",
                        "19-08-2025",
                        "01-09-2025",
                        "Submitted",
                        "Awaiting response",
                    ],
                    [
                        "Chargebee",
                        "Data Intern",
                        "-",
                        "01-09-2025",
                        "Not applied",
                        "Resume needs a data section",
                    ],
                ],
            ),
            (
                "Interviews",
                [
                    ["Company", "Round", "Date", "Mode", "Prepared"],
                    ["Zoho Corporation", "Online test", "27-08-2025", "Remote", "Partly"],
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_CAR}/interview_prep_notes.md",
        kind="md",
        mtime="2025-08-21T22:15:00",
        session="internship_apps",
        dates=(
            DateLabel(
                "2025-08-27",
                "meaningful",
                "27 August",
                "The scheduled online test - an appointment being prepared for.",
            ),
        ),
        content="""# Interview prep

Zoho online test is on 27 August. 90 minutes, mixed aptitude and coding.

## What they actually ask (from seniors)

- Two coding problems, easy to medium. Strings and arrays mostly.
- SQL is common. Joins, group by, sometimes a window function.
- Basic OS and DBMS theory in the MCQ section.

## Revise

**DSA**
- Sliding window: longest substring without repeating characters
- Two pointers: pair sum in a sorted array, container with most water
- Hashing: anagram grouping, first non-repeating character
- Sorting: custom comparators

**SQL**
- Second highest salary, three ways
- Group by with having
- Self join for manager-employee
- Window functions: ROW_NUMBER vs RANK vs DENSE_RANK

**DBMS theory**
- Normal forms up to BCNF. I have this cold from the assignment.
- ACID, isolation levels, what a dirty read actually is
- Index structures: B+ tree vs hash

## Stories to have ready

1. UrbanFlow - the renormalisation bug. Good story because it made our numbers
   *worse* and we still shipped the honest one.
2. Normalization assignment - the dependency preservation part, since most
   people skip it.
3. Something I got wrong: assumed proportional allocation was fair. It starved
   the west approach entirely.

## Do not

- Do not claim UrbanFlow ran on real hardware. It did not.
- Do not overstate the CGPA rounding.
""",
    ),
    FileSpec(
        path=f"{_CAR}/company_research.md",
        kind="md",
        mtime="2025-08-08T20:50:00",
        session="internship_apps",
        content="""# Company notes

## Zoho
Chennai/Tenkasi. Builds everything in-house, famously including their own
low-level infrastructure. Zoho Schools takes people without degrees, which says
something about how they evaluate. Relevant team: Workplace search and indexing.
Angle for the cover letter: search and indexing overlaps with my FYP.

## Freshworks
Chennai. SaaS, customer engagement. Big on product engineering culture. Backend
roles are Ruby and Java heavy. Less overlap with what I do, but a good name.

## Postman
Bangalore, API tooling. Platform team works on large-scale collection sync.
Interesting problem space, harder to get into.

## Chargebee
Chennai. Subscription billing. The data intern role wants SQL depth and some
analytics. My resume currently under-sells the SQL work from the DBMS
assignment - fix that before applying.

## General
Everyone asks the same three things: a project you can explain end to end, a
bug you found and fixed, and whether you can write SQL without a search engine.
Prepare those, ignore the rest.
""",
    ),
    # =======================================================================
    # NEGATIVE CONTROL: personal_misc - must NOT form a session
    # =======================================================================
    FileSpec(
        path=f"{_MISC}/history_essay_partition.md",
        kind="md",
        mtime="2025-07-14T16:20:00",
        session="personal_misc",
        notes=(
            "Dense with historical dates, none actionable. This is the primary "
            "test that the date classifier does not simply extract every date."
        ),
        dates=(
            DateLabel(
                "1947-08-15",
                "incidental",
                "15 August 1947",
                "A historical event date. Nothing is scheduled or due.",
            ),
            DateLabel(
                "1947-08-14",
                "incidental",
                "14 August 1947",
                "A historical event date.",
            ),
            DateLabel(
                "1948-01-30",
                "incidental",
                "30 January 1948",
                "A historical event date.",
            ),
            DateLabel(
                "1946-08-16",
                "incidental",
                "16 August 1946",
                "A historical event date.",
            ),
        ),
        content="""# Essay: the partition of British India

Written for the general elective. Not a CS module.

## Timeline

The Muslim League's call for Direct Action on 16 August 1946 turned a
constitutional dispute into a public order crisis in Calcutta, and the violence
that followed made a unified transfer of power politically unsustainable.

Mountbatten arrived in March 1947 and, against the advice of most of his staff,
advanced the transfer of power by nearly a year. Pakistan came into being on
14 August 1947 and India on 15 August 1947. The Radcliffe boundary award was
not published until 17 August, two days after independence, so millions of
people did not know which country they were in on the day they became citizens
of it.

Gandhi was assassinated on 30 January 1948, five months later.

## Argument

The standard account treats partition as the outcome of irreconcilable
communal difference. The essay argues the opposite: that the compressed
timetable itself manufactured much of the catastrophe. A boundary published
after independence, with no time to plan population movement and no security
arrangements agreed in advance, converted a difficult political settlement into
a humanitarian disaster.

The counter-argument is that delay would have meant more violence, not less,
given the collapse of civil authority through 1946. The essay concedes this is
not resolvable from the available record but notes that the argument for haste
was made by people who did not have to move.

## Sources used

Standard undergraduate reading list. Nothing archival.
""",
    ),
    FileSpec(
        path=f"{_MISC}/birthday_list.txt",
        kind="txt",
        mtime="2025-12-28T11:05:00",
        session="personal_misc",
        notes=(
            "Recurring annual dates with no year-specific commitment. The "
            "cross-file recurrence signal must not be fooled into promoting "
            "these: they recur *within* a file, not across the corpus."
        ),
        dates=(
            DateLabel(
                "2003-04-11", "incidental", "11 April 2003", "A birth date, not an obligation."
            ),
            DateLabel("2002-09-23", "incidental", "23 September 2002", "A birth date."),
            DateLabel("1971-06-05", "incidental", "5 June 1971", "A birth date."),
            DateLabel("1974-11-30", "incidental", "30 November 1974", "A birth date."),
        ),
        content="""BIRTHDAYS
=========
Keep this updated, I keep forgetting.

Family
  Amma        - 5 June 1971
  Appa        - 30 November 1974
  Sister      - 23 September 2002
  Me          - 11 April 2003

Friends
  Abu         - 2 February
  Nithya      - 19 July
  Karan       - 8 December
  Priya       - 14 March

Notes
  Amma does not like surprises. Ask first.
  Karan's is close to end-sem every year, so it never gets celebrated.
  Nithya's is during the holidays, so call instead.
""",
    ),
    FileSpec(
        path=f"{_MISC}/movie_watchlist.txt",
        kind="txt",
        mtime="2026-01-05T23:40:00",
        session="personal_misc",
        dates=(
            DateLabel("1995-01-01", "incidental", "1995", "A film release year."),
            DateLabel("2014-01-01", "incidental", "2014", "A film release year."),
            DateLabel("2019-01-01", "incidental", "2019", "A film release year."),
            DateLabel("1966-01-01", "incidental", "1966", "A film release year."),
        ),
        content="""WATCHLIST

Seen
  Heat (1995) - the diner scene is as good as everyone says
  Whiplash (2014) - unbearable in a good way
  Parasite (2019) - the flood sequence
  The Battle of Algiers (1966) - watched for the history elective, stayed for
    the filmmaking

To watch
  Andrei Rublev
  Tokyo Story
  Chungking Express
  The Conversation - apparently the sound design is the whole point
  Kumbalangi Nights - Abu will not stop talking about it

Abandoned
  Anything over three hours during exam season. Bad idea, learned it twice.
""",
    ),
    FileSpec(
        path=f"{_MISC}/book_notes_sapiens.md",
        kind="md",
        mtime="2025-10-30T22:10:00",
        session="personal_misc",
        dates=(
            DateLabel("1776-07-04", "incidental", "1776", "A historical reference."),
            DateLabel("1492-01-01", "incidental", "1492", "A historical reference."),
        ),
        content="""# Notes - Sapiens

Read most of it over the mid-sem break.

## The bit that stuck

The argument that money, nations and corporations are all shared fictions, and
that large-scale cooperation depends on strangers believing the same story.
It is not a new idea but the framing is clean.

## Chronology I keep mixing up

- Cognitive revolution, roughly 70,000 years ago
- Agricultural revolution, roughly 12,000 years ago
- 1492 as the conventional marker for the start of European expansion
- 1776 for both the American declaration and Adam Smith's Wealth of Nations,
  which the book makes a point of

## Where I think it overreaches

The agricultural revolution as "history's biggest fraud" is a good line but it
smuggles in a value judgement about what wheat did to us. Individual wellbeing
and species success are different measures and the book slides between them
when it suits the argument.

## Worth arguing about

Whether the shared-fiction framing explains anything or just relabels it.
Abu thinks it is a tautology. I think the tautology is the point.
""",
    ),
    FileSpec(
        path=f"{_MISC}/recipe_biryani.txt",
        kind="txt",
        mtime="2026-02-22T19:30:00",
        session="personal_misc",
        notes="Contains no dates at all. A file with zero temporal content.",
        content="""AMMA'S CHICKEN BIRYANI (approximation, hers is better)

For 4.

Rice
  Basmati, 2 cups. Soak 30 minutes, no more, it goes to mush.
  Boil with 2 bay leaves, 4 cloves, 1 cinnamon stick, salt.
  Drain at 70 percent done. It finishes in the dum.

Marinade
  Chicken 750g, bone in. Boneless is convenient and worse.
  Thick curd 3/4 cup, ginger garlic paste 2 tbsp, red chilli 1.5 tsp,
  turmeric 1/2 tsp, garam masala 1 tsp, salt, juice of half a lime.
  At least 2 hours. Overnight is noticeably better.

Masala
  Slice 3 large onions thin. Fry in ghee until genuinely brown, not golden.
  This takes 20 minutes and there is no way to rush it. Reserve half for
  layering.
  Add tomatoes 2, chopped mint and coriander a big handful each, then the
  marinated chicken. Cook until the oil separates.

Dum
  Layer: masala, rice, fried onions, mint, saffron milk. Repeat.
  Seal with dough or a tight lid with a weight.
  Lowest flame, 25 minutes. Rest 10 minutes before opening.

Mistakes I have made
  Soaked the rice an hour - mush.
  Under-fried the onions - the whole thing tasted flat.
  Opened the dum early to check - do not.
""",
    ),
    # =======================================================================
    # NOISE / DISTRACTORS - no session, mixed topics
    # =======================================================================
    FileSpec(
        path=f"{_DL}/python_cheatsheet.pdf",
        kind="pdf",
        mtime="2025-09-29T10:15:00",
        session=None,
        notes=(
            "Topical distractor. Overlaps vocabulary with several code files "
            "but belongs to no session and answers no query - it should appear "
            "in nearest-neighbour results and be correctly de-prioritised."
        ),
        content=[
            (
                "Python quick reference",
                [
                    "Comprehensions: [f(x) for x in xs if p(x)] builds a list; swap the "
                    "brackets for a generator expression to avoid materialising it.",
                    "Dict methods worth remembering: get with a default, setdefault, "
                    "and collections.defaultdict for accumulation patterns.",
                    "Slicing: a[start:stop:step]. Negative step reverses. a[::-1] is the "
                    "idiomatic reverse and is faster than reversed() plus list().",
                ],
            ),
            (
                "Standard library",
                [
                    "pathlib.Path for filesystem work. Never build paths with string "
                    "concatenation; Path handles separators and expansion correctly.",
                    "dataclasses for plain records. frozen=True makes them hashable and "
                    "usable as dict keys.",
                    "itertools: groupby requires the input to be sorted by the key, "
                    "which is the single most common mistake with it.",
                    "functools.lru_cache for memoising pure functions with hashable " "arguments.",
                ],
            ),
            (
                "Common gotchas",
                [
                    "Mutable default arguments are evaluated once at definition time. "
                    "Use None and construct inside the function.",
                    "Late binding in closures: a lambda inside a loop captures the "
                    "variable, not its value at capture time.",
                    "is versus equality. Identity comparison for small integers and "
                    "interned strings appears to work and then stops working.",
                ],
            ),
        ],
    ),
    FileSpec(
        path=f"{_DL}/wifi_setup_instructions.txt",
        kind="txt",
        mtime="2025-08-02T09:00:00",
        session=None,
        notes="Pure noise. Belongs to nothing, answers nothing.",
        content="""CAMPUS WIFI SETUP

SSID: CAMPUS-SECURE
Security: WPA2 Enterprise
EAP method: PEAP
Phase 2: MSCHAPV2
CA certificate: Do not validate (yes, this is bad, it is what IT says)
Identity: your roll number
Password: portal password, not the LMS one

If it keeps disconnecting
  Forget the network, re-add it. Works about half the time.
  Turn off randomised MAC for this network. The captive portal binds to MAC.
  If the portal will not load, browse to http://1.1.1.1 to force it open.

Printer
  Queue name: LIB-PRN-02
  Only reachable from CAMPUS-SECURE, not from the guest network.
""",
    ),
    FileSpec(
        path=f"{_DL}/scholarship_form_notes.txt",
        kind="txt",
        mtime="2025-12-11T15:30:00",
        session=None,
        notes=(
            "Unsessioned file that nevertheless carries a genuinely meaningful "
            "date. Tests that the timeline layer is independent of the session "
            "layer - a deadline in an orphan file must still become a timeline "
            "node."
        ),
        dates=(
            DateLabel(
                "2025-12-31",
                "meaningful",
                "31 December 2025",
                "A hard application deadline, stated as 'last date'.",
            ),
            DateLabel(
                "2025-12-20",
                "meaningful",
                "20 December 2025",
                "Deadline for obtaining a prerequisite document.",
            ),
        ),
        content="""MERIT SCHOLARSHIP RENEWAL

Last date for submission: 31 December 2025. No extensions, the portal closes.

Documents needed
  [x] Previous semester marksheet
  [x] Aadhaar copy
  [ ] Income certificate - must be issued within the last 6 months.
      The current one expires. Get a fresh one from the taluk office by
      20 December 2025 or the application is rejected on scrutiny.
  [ ] Bank passbook first page, self-attested
  [ ] Passport photo, white background

Notes
  Portal only works properly in Chrome. It silently fails to upload in Firefox
  and does not tell you.
  File size limit 200 KB per document, which is absurd for a scan.
  Keep the acknowledgement PDF. Last year they claimed they never received it.
""",
    ),
)


# ===========================================================================
# Ground-truth query set
# ===========================================================================

QUERIES: tuple[QuerySpec, ...] = (
    QuerySpec(
        id="q01",
        text="the pdf I studied before my machine learning exam",
        targets=(f"{_ML}/Unit4_Ensemble_Methods.pdf",),
        relevant=(
            f"{_ML}/Unit4_Ensemble_Methods.pdf",
            f"{_ML}/Unit4_Ensemble_Methods_annotated.pdf",
            f"{_ML}/Unit3_SVM_Notes.md",
            f"{_ML}/ml_revision_checklist.txt",
            f"{_ML}/Exam_Timetable_Sem7.xlsx",
        ),
        kind="activity",
        difficulty="hard",
        rationale=(
            "THE CENTRAL CASE FOR THE HYPOTHESIS. The target PDF contains no "
            "occurrence of 'exam', 'studied', 'revision' or 'machine learning' as "
            "a phrase. A pure-semantic system must fail or rank it poorly. Only "
            "the activity session (co-edited with the timetable and checklist in "
            "the same two-week window) or a graph path through the timetable's "
            "explicit filename reference can recover it."
        ),
        field_note="If the full system does not beat the baseline here, the thesis is in trouble.",
    ),
    QuerySpec(
        id="q02",
        text="what was due in the third week of October",
        targets=(f"{_DBMS}/assignment_brief.pdf",),
        relevant=(
            f"{_DBMS}/assignment_brief.pdf",
            f"{_DBMS}/Assignment2_Normalization_final.docx",
            f"{_DBMS}/Assignment2_Normalization_draft.docx",
            f"{_DBMS}/dbms_lab_record.xlsx",
        ),
        kind="temporal",
        difficulty="hard",
        rationale=(
            "Pure date-range retrieval. The phrase 'third week of October' "
            "appears nowhere; it must be resolved to 15-21 October 2025 and "
            "matched against classified meaningful dates. A semantic system has "
            "nothing to match on."
        ),
    ),
    QuerySpec(
        id="q03",
        text="my notes on support vector machines and the kernel trick",
        targets=(f"{_ML}/Unit3_SVM_Notes.md",),
        relevant=(f"{_ML}/Unit3_SVM_Notes.md",),
        kind="semantic",
        difficulty="easy",
        rationale=(
            "Control query. Straight lexical and semantic overlap; the baseline "
            "should do well. Included so the evaluation can show the context "
            "layers do not *degrade* queries semantic search already handles - "
            "a claim reviewers ask for and most papers omit."
        ),
    ),
    QuerySpec(
        id="q04",
        text="everything from the hackathon weekend",
        targets=(f"{_UF}/README.md", f"{_UF}/team_notes.md"),
        relevant=(
            f"{_UF}/README.md",
            f"{_UF}/team_notes.md",
            f"{_UF}/app.py",
            f"{_UF}/traffic_model.py",
            f"{_UF}/pitch_deck.pptx",
            f"{_UF}/submission_checklist.txt",
            f"{_UF}/sensor_data_sample.xlsx",
            f"{_UF}/demo_script.md",
        ),
        kind="activity",
        difficulty="hard",
        rationale=(
            "Session-recall query: the user wants a *set*, not a document. "
            "Recall@10 is the metric that matters. Files like traffic_model.py "
            "never say 'hackathon', so semantic retrieval recovers only the "
            "subset that happens to mention it."
        ),
    ),
    QuerySpec(
        id="q05",
        text="documents where my supervisor Dr Murari is mentioned",
        targets=(f"{_CAP}/supervisor_meeting_notes.md",),
        relevant=(
            f"{_CAP}/supervisor_meeting_notes.md",
            f"{_CAP}/ContextFS_Proposal.docx",
            f"{_CAP}/review1_slides.pptx",
        ),
        kind="entity",
        difficulty="easy",
        rationale=(
            "Named-entity query. Tests that the entity layer contributes and "
            "that entity edges connect the three capstone documents that share "
            "this person."
        ),
    ),
    QuerySpec(
        id="q06",
        text="the slides I presented about the traffic project",
        targets=(f"{_UF}/pitch_deck.pptx",),
        relevant=(f"{_UF}/pitch_deck.pptx", f"{_UF}/demo_script.md"),
        kind="hybrid",
        difficulty="easy",
        rationale=(
            "Format plus topic. 'Slides' is a format cue rather than content; "
            "the target is a PPTX about traffic. Tests that the ranker uses "
            "structural signal alongside semantics."
        ),
    ),
    QuerySpec(
        id="q07",
        text="deadlines I had in September",
        targets=(f"{_UF}/submission_checklist.txt",),
        relevant=(
            f"{_UF}/submission_checklist.txt",
            f"{_UF}/README.md",
            f"{_UF}/team_notes.md",
            f"{_UF}/pitch_deck.pptx",
        ),
        kind="temporal",
        difficulty="hard",
        rationale=(
            "Tests meaningful-vs-incidental discrimination directly. September "
            "also contains sensor-reading timestamps (13-09-2025) and a lab "
            "attendance record (08-09-2025), neither of which is a deadline. A "
            "naive date extractor returns those and loses precision."
        ),
    ),
    QuerySpec(
        id="q08",
        text="what I was working on while applying for internships",
        targets=(f"{_CAR}/application_tracker.xlsx",),
        relevant=(
            f"{_CAR}/application_tracker.xlsx",
            f"{_CAR}/Resume_Alfred_Mathew.docx",
            f"{_CAR}/cover_letter_zoho.docx",
            f"{_CAR}/interview_prep_notes.md",
            f"{_CAR}/company_research.md",
        ),
        kind="activity",
        difficulty="hard",
        rationale=(
            "Session-scoped query where the linking concept ('internships') "
            "appears in only some members. Resume and company_research never "
            "say 'applying'; they belong by co-activity."
        ),
    ),
    QuerySpec(
        id="q09",
        text="database normalization with BCNF decomposition examples",
        targets=(f"{_DBMS}/Assignment2_Normalization_final.docx",),
        relevant=(
            f"{_DBMS}/Assignment2_Normalization_final.docx",
            f"{_DBMS}/normalization_examples.sql",
            f"{_DBMS}/Assignment2_Normalization_draft.docx",
            f"{_DBMS}/assignment_brief.pdf",
        ),
        kind="semantic",
        difficulty="easy",
        rationale=(
            "Second control query. Also probes near-duplicate handling: the "
            "draft and final are nearly identical, and a good system should "
            "rank the final above the draft rather than treating them as "
            "interchangeable."
        ),
    ),
    QuerySpec(
        id="q10",
        text="the spreadsheet that told me when my exams were",
        targets=(f"{_ML}/Exam_Timetable_Sem7.xlsx",),
        relevant=(f"{_ML}/Exam_Timetable_Sem7.xlsx",),
        kind="hybrid",
        difficulty="easy",
        rationale=(
            "Direct hit for both systems, but a useful discriminator: "
            "ml_lab_attendance.xlsx is also a spreadsheet in the same folder "
            "full of dates. Ranking it above the timetable is a precision "
            "failure the metric will catch."
        ),
    ),
    QuerySpec(
        id="q11",
        text="my final year project proposal document",
        targets=(f"{_CAP}/ContextFS_Proposal.docx",),
        relevant=(
            f"{_CAP}/ContextFS_Proposal.docx",
            f"{_CAP}/review1_slides.pptx",
            f"{_CAP}/literature_survey.md",
        ),
        kind="semantic",
        difficulty="easy",
        rationale="Control query with a strong lexical signal ('proposal').",
    ),
    QuerySpec(
        id="q12",
        text="meetings I had with my guide in February",
        targets=(f"{_CAP}/supervisor_meeting_notes.md",),
        relevant=(f"{_CAP}/supervisor_meeting_notes.md", f"{_CAP}/review1_slides.pptx"),
        kind="hybrid",
        difficulty="hard",
        rationale=(
            "Requires all three of entity ('guide' -> supervisor), temporal "
            "('February' -> 2026-02), and semantic ('meetings'). The single "
            "best test of whether the layers compose rather than merely coexist."
        ),
    ),
    QuerySpec(
        id="q13",
        text="anything related to my Zoho application",
        targets=(f"{_CAR}/cover_letter_zoho.docx",),
        relevant=(
            f"{_CAR}/cover_letter_zoho.docx",
            f"{_CAR}/application_tracker.xlsx",
            f"{_CAR}/interview_prep_notes.md",
            f"{_CAR}/company_research.md",
        ),
        kind="entity",
        difficulty="hard",
        rationale=(
            "Organisation-entity query. interview_prep_notes.md mentions Zoho "
            "once in passing; company_research.md has a Zoho section. Entity "
            "edges should pull in the whole cluster where semantics alone "
            "recovers only the strongest lexical matches."
        ),
    ),
    QuerySpec(
        id="q14",
        text="the code I wrote during the hackathon",
        targets=(f"{_UF}/traffic_model.py", f"{_UF}/app.py"),
        relevant=(f"{_UF}/traffic_model.py", f"{_UF}/app.py"),
        kind="activity",
        difficulty="hard",
        rationale=(
            "Neither target contains the word 'hackathon' in prose - only "
            "'HackChennai' in a docstring. The distractor prototype_scanner.py "
            "and confusion_matrix_practice.py are also Python. Session "
            "membership is what separates them."
        ),
    ),
    QuerySpec(
        id="q15",
        text="how do bagging and boosting differ",
        targets=(f"{_ML}/Unit4_Ensemble_Methods.pdf",),
        relevant=(
            f"{_ML}/Unit4_Ensemble_Methods.pdf",
            f"{_ML}/Unit4_Ensemble_Methods_annotated.pdf",
        ),
        kind="semantic",
        difficulty="easy",
        rationale=(
            "The same target as q01 reached by a purely semantic route. The "
            "pair (q01, q15) is the cleanest single demonstration in the "
            "benchmark: identical target, one query phrased from content and "
            "one from memory. If the baseline wins q15 and loses q01, the "
            "hypothesis is precisely illustrated."
        ),
        field_note="Pairs with q01. Report these two together in the paper.",
    ),
    QuerySpec(
        id="q16",
        text="the assignment I revised and submitted a version of",
        targets=(f"{_DBMS}/Assignment2_Normalization_final.docx",),
        relevant=(
            f"{_DBMS}/Assignment2_Normalization_final.docx",
            f"{_DBMS}/Assignment2_Normalization_draft.docx",
        ),
        kind="hybrid",
        difficulty="hard",
        rationale=(
            "Tests duplicate-edge awareness: the user is describing a "
            "draft/final relationship, not content. Correct behaviour is to "
            "return the final above the draft and to explain the link."
        ),
    ),
    QuerySpec(
        id="q17",
        text="what deadline is coming up that I might have forgotten",
        targets=(f"{_DL}/scholarship_form_notes.txt",),
        relevant=(f"{_DL}/scholarship_form_notes.txt",),
        kind="temporal",
        difficulty="hard",
        rationale=(
            "The target belongs to no session and is topically isolated from "
            "everything else in the corpus. Only the timeline layer can reach "
            "it. This isolates the temporal contribution from the activity "
            "contribution in the ablation - RQ2 without RQ1 confounding it."
        ),
    ),
)
