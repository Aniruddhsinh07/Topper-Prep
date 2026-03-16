import frappe
import random

@frappe.whitelist(allow_guest=True)
def get_questions(student, mode, limit=20, subjects=None, exam=None):

    limit = int(limit)

    subject_distribution = {}

    # -------------------------
    # PRACTICE MODE
    # -------------------------
    if mode == "practice":
        
        subjects = frappe.parse_json(subjects)

        per_subject = limit // len(subjects)

        for s in subjects:
            subject_distribution[s] = per_subject

        history_field = "student_question_history"

    # -------------------------
    # TEST MODE
    # -------------------------
    elif mode == "test":

        exam_doc = frappe.get_doc("Government Exam", exam)

        for row in exam_doc.subject:

            count = round((row.percentage / 100) * limit)

            subject_distribution[row.subject] = count

        history_field = "test_question_history"

    else:
        frappe.throw("Invalid Mode")

    student_doc = frappe.get_doc("Student", student)

    history = student_doc.get(history_field)

    right_questions = set()
    wrong_questions = set()

    for h in history:

        if h.status == "Right":
            right_questions.add(h.question_id)

        elif h.status == "Wrong":
            wrong_questions.add(h.question_id)

    result = []

    # -------------------------
    # FETCH QUESTIONS
    # -------------------------

    for subject, count in subject_distribution.items():

        subject_doc = frappe.get_doc("Subject Master", subject)

        questions = subject_doc.questions_table

        subject_questions = []

        # repeat wrong questions first
        for q in questions:
            if q.name in wrong_questions:
                subject_questions.append(q)

        # add new questions (skip right ones)
        for q in questions:
            if q.name not in right_questions and q.name not in wrong_questions:
                subject_questions.append(q)

        subject_questions = subject_questions[:count]

        for q in subject_questions:

            result.append({
                "question_id": q.name,
                "subject": subject,
                "question": q.question,
                "a": q.a,
                "b": q.b,
                "c": q.c,
                "d": q.d,
                "right_option": q.right_option,
                "right_answer": q.right_answer,
                "exam_and_year": q.exam_and_year
            })

    random.shuffle(result)

    return result

@frappe.whitelist(allow_guest=True)
def submit_answers(student, mode, answers, exam=None):

    answers = frappe.parse_json(answers)

    student_doc = frappe.get_doc("Student", student)

    if mode == "practice":
        history_field = "student_question_history"

    elif mode == "test":
        history_field = "test_question_history"

    else:
        frappe.throw("Invalid Mode")

    for ans in answers:

        subject_doc = frappe.get_doc("Subject Master", ans["subject"])

        question = None

        for q in subject_doc.questions_table:
            if q.name == ans["question_id"]:
                question = q
                break

        if not question:
            continue

        status = "Wrong"

        if ans["selected"] == question.right_option:
            status = "Right"

        row = {
            "question_id": question.name,
            "subject": ans["subject"],
            "selected_option": ans["selected"],
            "status": status
        }

        if mode == "test":
            row["exam"] = exam

        student_doc.append(history_field, row)

    student_doc.save(ignore_permissions=True)

    return {"message": "Answers saved"}