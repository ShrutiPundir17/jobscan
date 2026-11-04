from collections import Counter

from apply_agent.forms import (
    classify_control,
    confirmation_hits,
    file_accepted,
    judge_after_submit,
    title_overlaps,
)


def c(label="", tag="input", type_="text", name="", id_=""):
    return {"label": label, "tag": tag, "type": type_, "name": name, "id": id_}


def test_whole_label_matching():
    assert classify_control(c("First Name *")) == "first_name"
    assert classify_control(c("Email (required)")) == "email"
    assert classify_control(c("Mobile phone number")) == "phone"
    assert classify_control(c("LinkedIn Profile URL")) == "linkedin_url"
    assert classify_control(c("Confirm your email")) == "email_confirm"
    for label in (
        "Name of current employer",
        "Referrer email",
        "Emergency contact phone",
        "Name pronunciation",
        "LinkedIn of the person who referred you",
        "Company name",
    ):
        assert classify_control(c(label, type_="email")) is None, label


def test_attribute_fallback_only_for_known_ats_names():
    assert classify_control(c("", name="name")) == "full_name"
    assert classify_control(c("", name="urls[LinkedIn]")) == "linkedin_url"
    assert classify_control(c("", name="referrer_email", type_="email")) is None
    # Unknown label beats a type hint.
    assert classify_control(c("Manager email", type_="email")) is None


def test_files_and_textareas():
    assert classify_control(c("Resume/CV *", type_="file")) == "resume"
    assert classify_control(c("Cover Letter", type_="file", id_="cover_letter")) is None
    assert classify_control(c("Additional information", tag="textarea", type_="textarea")) == "cover_letter"
    assert classify_control(c("Why do you want to work here?", tag="textarea", type_="textarea")) is None


def test_file_accepted():
    assert file_accepted("cv.pdf", ".pdf,.docx")
    assert not file_accepted("cv.txt", ".pdf,.docx")
    assert file_accepted("cv.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert file_accepted("cv.txt", "")
    assert file_accepted("cv.pdf", "application/*")
    assert not file_accepted(None, "")


def test_judge_after_submit():
    jd = "Thank you for applying to Acme! Build APIs."
    base = confirmation_hits(jd)
    same = {"text": jd, "errors": [], "form_present": True}
    assert judge_after_submit(base_url="u", base_hits=base, url="u", state=same) == ("pending", None)
    # JD phrase still there but form gone and nothing new: not a confirmation.
    gone = {"text": jd, "errors": [], "form_present": False}
    assert judge_after_submit(base_url="u", base_hits=base, url="u", state=gone)[0] == "pending"
    new = {"text": jd + " Your application has been submitted.", "errors": [], "form_present": False}
    assert judge_after_submit(base_url="u", base_hits=base, url="u", state=new)[0] == "confirmed"
    # New phrase while the form is still on the same page (e.g. a toast) is not enough.
    toast = {**new, "form_present": True}
    assert judge_after_submit(base_url="u", base_hits=base, url="u", state=toast)[0] == "pending"
    assert judge_after_submit(base_url="https://x/jobs/1", base_hits=Counter(), url="https://x/jobs/1/confirmation", state=same)[0] == "confirmed"
    err = {"text": jd, "errors": ["Email is invalid"], "form_present": True}
    assert judge_after_submit(base_url="u", base_hits=base, url="u", state=err) == ("rejected", "Email is invalid")


def test_title_overlaps():
    assert title_overlaps("Backend Engineer", "Job Application for Senior Backend Engineer at Acme")
    assert not title_overlaps("Backend Engineer", "Product Designer")
    assert not title_overlaps("Data Engineer", "Backend Engineer - Acme")
    assert title_overlaps("Software Engineer", "Software Engineer II")  # nothing specific to compare
    assert title_overlaps("Backend Engineer", "")
