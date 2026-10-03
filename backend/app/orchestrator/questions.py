"""Agents asking people questions mid-stage.

The asking agent's worker thread blocks until the question is answered from the UI, so the agent keeps its full
working state. A server restart abandons the wait: the run fails and a retry re-runs the stage, reusing answers
already given to the same question.
"""

import threading

from sqlalchemy import select

from app.db.models import Question, Run, utcnow
from app.db.session import emit, session_scope

POLL_SECONDS = 0.5
SKIPPED = "The user chose not to answer. Use your best judgement and state the assumption in your output."

shutting_down = threading.Event()


class QuestionAbandoned(RuntimeError):
    pass


def ask(run_id: str, stage: str, attempt: int, question: str, options: list[str] | None = None) -> str:
    question = question.strip()
    with session_scope() as s:
        earlier = s.scalar(select(Question).where(
            Question.run_id == run_id, Question.stage == stage, Question.question == question,
            Question.status == "answered").order_by(Question.id.desc()))
        if earlier is not None:
            return earlier.answer
        q = Question(run_id=run_id, stage=stage, attempt=attempt, question=question, options=options or None)
        s.add(q)
        s.flush()
        question_id = q.id
        s.get(Run, run_id).status = "awaiting_input"
    emit(run_id, "question", f"Question: {question}", stage)

    while not shutting_down.wait(POLL_SECONDS):
        with session_scope() as s:
            q = s.get(Question, question_id)
            if q.status == "answered":
                return q.answer
            if q.status == "cancelled":
                raise QuestionAbandoned("The question was cancelled")
    cancel_open(run_id)
    raise QuestionAbandoned("Server stopped while waiting for an answer. Retry to ask again.")


def answer(question_id: int, text: str, answered_by: str) -> Question:
    """Record an answer and put the run back to running; the waiting agent picks it up on its next poll."""
    with session_scope() as s:
        q = s.get(Question, question_id)
        if q is None:
            raise KeyError(question_id)
        if q.status != "open":
            raise ValueError("This question is no longer open")
        q.status, q.answer, q.answered_by, q.answered_at = "answered", text.strip() or SKIPPED, answered_by, utcnow()
        run = s.get(Run, q.run_id)
        if run.status == "awaiting_input":
            run.status = "running"
        s.flush()
        s.expunge(q)
    emit(q.run_id, "answer", f"Answered by {answered_by}: {text.strip() or '(skipped)'}", q.stage)
    return q


def answered_for(run_id: str, stage: str) -> list[tuple[str, str]]:
    with session_scope() as s:
        rows = s.scalars(select(Question).where(Question.run_id == run_id, Question.stage == stage,
                                                Question.status == "answered").order_by(Question.id)).all()
        return [(q.question, q.answer) for q in rows]


def cancel_open(run_id: str | None = None) -> None:
    with session_scope() as s:
        query = s.query(Question).filter(Question.status == "open")
        if run_id:
            query = query.filter(Question.run_id == run_id)
        query.update({Question.status: "cancelled"})
