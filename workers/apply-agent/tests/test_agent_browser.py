"""
Drives a real headless Chromium against local mock ATS forms.

The mock pages mimic the field naming of Greenhouse, Lever and Ashby forms
closely enough to exercise detection, filling, required-field checks,
captcha refusal and confirmation detection — without touching real employers.
Several pages are traps for past bugs (JD text that looks like a confirmation,
forms that upload on attach, ambiguous buttons, look-alike fields).
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from apply_agent import ApplicantProfile, ApplyAgent, ApplyAgentError

JD = (
    "<section class='jd'><h1>Backend Engineer</h1><p>Build Python APIs.</p>"
    "<p>Thank you for your interest in Acme. Thank you for applying to Acme — we read every application!</p></section>"
)

GREENHOUSE_PAGE = f"""<!doctype html><html><head><title>Job Application for Backend Engineer at Acme</title></head><body>
{JD}
<form id="application-form" action="/gh/submit" method="post" enctype="multipart/form-data">
  <div class="field"><label for="first_name">First Name *</label><input id="first_name" name="first_name" aria-required="true"></div>
  <div class="field"><label for="last_name">Last Name *</label><input id="last_name" name="last_name" aria-required="true"></div>
  <div class="field"><label for="email">Email *</label><input id="email" name="email" type="text" aria-required="true"></div>
  <div class="field"><label for="phone">Phone</label><input id="phone" name="phone"></div>
  <div class="field"><label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" accept=".pdf,.doc,.docx,.txt,.rtf" style="display:none" aria-required="true"></div>
  <div class="field"><label for="cover_letter">Cover Letter</label><input id="cover_letter" name="cover_letter" type="file" style="display:none"></div>
  <div class="field"><label for="q1">LinkedIn Profile *</label><input id="q1" name="question_1" aria-required="true"></div>
  <div class="field"><label for="q2">Website</label><input id="q2" name="question_2"></div>
  <button type="submit" id="submit_app">Submit application</button>
</form></body></html>"""

LEVER_PAGE = """<!doctype html><html><body>
<form action="/lever/submit" method="post" enctype="multipart/form-data">
  <ul>
   <li class="application-question"><label><div class="application-label">Resume/CV ✱</div><input type="file" name="resume" required></label></li>
   <li class="application-question"><label><div class="application-label">Full name ✱</div><input name="name" required></label></li>
   <li class="application-question"><label><div class="application-label">Email ✱</div><input type="email" name="email" required></label></li>
   <li class="application-question"><label><div class="application-label">Phone</div><input name="phone"></label></li>
   <li class="application-question"><label><div class="application-label">Current company</div><input name="org"></label></li>
   <li class="application-question"><label><div class="application-label">LinkedIn URL</div><input name="urls[LinkedIn]"></label></li>
   <li class="application-question"><label><div class="application-label">Additional information</div><textarea name="comments"></textarea></label></li>
  </ul>
  <button type="submit" id="btn-submit">Submit application</button>
</form></body></html>"""

# SPA: form is rendered by script after a delay and submitted via fetch.
ASHBY_PAGE = """<!doctype html><html><body><div id="root">Loading…</div>
<script>
setTimeout(() => {
  document.getElementById('root').innerHTML = `
  <form id="f">
    <label for="_systemfield_name">Name *</label><input id="_systemfield_name" name="_systemfield_name" required>
    <label for="_systemfield_email">Email *</label><input id="_systemfield_email" name="_systemfield_email" type="email" required>
    <label for="_systemfield_resume">Resume *</label><input id="_systemfield_resume" type="file" required>
    <button type="submit">Submit Application</button>
  </form>`;
  document.getElementById('f').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.append('resume', document.getElementById('_systemfield_resume').files[0]);
    await fetch('/ashby/submit', { method: 'POST', body: fd });
    document.getElementById('root').innerHTML = '<h2>Your application was successfully submitted.</h2>';
  });
}, 400);
</script></body></html>"""

# Uploads on attach and autosaves on input — prepare must leak nothing.
LEAKY_PAGE = """<!doctype html><html><body>
<form action="/leaky/submit" method="post" enctype="multipart/form-data">
  <label for="first_name">First name *</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last name *</label><input id="last_name" name="last_name" required>
  <label for="email">Email *</label><input id="email" name="email" required>
  <label for="resume">Resume *</label><input id="resume" name="resume" type="file" required>
  <button type="submit">Submit application</button>
</form>
<script>
for (const el of document.querySelectorAll('input:not([type=file])'))
  el.addEventListener('input', () => fetch('/leaky/autosave', {method: 'POST', body: el.value}).catch(() => {}));
document.getElementById('resume').addEventListener('change', (e) =>
  fetch('/leaky/upload', {method: 'POST', body: e.target.files[0]}).catch(() => {}));
</script></body></html>"""

LOOKALIKE_PAGE = """<!doctype html><html><body>
<form action="/look/submit" method="post" enctype="multipart/form-data">
  <label for="a">Name *</label><input id="a" name="a" required>
  <label for="b">Name of current employer</label><input id="b" name="b">
  <label for="c">Email *</label><input id="c" name="c" type="email" required>
  <label for="d">Confirm email *</label><input id="d" name="d" type="email" required>
  <label for="e">Referrer email</label><input id="e" name="e" type="email">
  <label for="f">Emergency contact phone</label><input id="f" name="f" type="tel">
  <label for="g">Phone number</label><input id="g" name="g" type="tel">
  <label for="h">Resume</label><input id="h" name="h" type="file" required>
  <button type="submit">Submit</button>
</form></body></html>"""

PDF_ONLY_PAGE = LOOKALIKE_PAGE.replace('name="h" type="file" required', 'name="h" type="file" accept=".pdf" required')

# Submit does nothing visible; the JD already says "thank you for applying".
JD_TRAP_PAGE = f"""<!doctype html><html><body>{JD}
<form id="f" action="/never" method="post">
  <label for="first_name">First name</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last name</label><input id="last_name" name="last_name" required>
  <label for="email">Email</label><input id="email" name="email" required>
  <label for="resume">Resume</label><input id="resume" name="resume" type="file" required>
  <button type="submit">Submit application</button>
</form>
<script>document.getElementById('f').addEventListener('submit', (e) => e.preventDefault());</script>
</body></html>"""

# Submit shows a validation error and stays on the form.
REJECT_PAGE = JD_TRAP_PAGE.replace(
    "(e) => e.preventDefault()",
    "(e) => { e.preventDefault(); document.getElementById('err').textContent = 'Phone is invalid'; }",
).replace("</form>", '<div id="err" class="error"></div></form>')

AMBIGUOUS_PAGE = """<!doctype html><html><body>
<form action="/amb/submit" method="post" enctype="multipart/form-data">
  <label for="first_name">First name</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last name</label><input id="last_name" name="last_name" required>
  <label for="email">Email</label><input id="email" name="email" required>
  <label for="resume">Resume</label><input id="resume" name="resume" type="file" required>
  <button>Add another</button>
  <button aria-label="">➜</button>
</form></body></html>"""

CUSTOM_PAGE = """<!doctype html><html><body>
<form action="/custom/submit" method="post" enctype="multipart/form-data">
  <label for="first_name">First name *</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last name *</label><input id="last_name" name="last_name" required>
  <label for="email">Email *</label><input id="email" name="email" required>
  <label for="resume">Resume *</label><input id="resume" name="resume" type="file" required>
  <fieldset><legend>Are you legally authorized to work in India? *</legend>
    <label><input type="radio" name="auth" value="yes" required> Yes</label>
    <label><input type="radio" name="auth" value="no"> No</label>
  </fieldset>
  <label for="notice">Notice period *</label>
  <select id="notice" name="notice" required><option value="">Select…</option><option>Immediate</option></select>
  <button type="submit">Submit</button>
</form></body></html>"""

CAPTCHA_PAGE = """<!doctype html><html><body>
<form action="/captcha/submit" method="post" enctype="multipart/form-data">
  <label for="first_name">First name</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last name</label><input id="last_name" name="last_name" required>
  <label for="email">Email</label><input id="email" name="email" required>
  <label for="resume">Resume</label><input id="resume" name="resume" type="file" required>
  <div class="g-recaptcha" style="width:304px;height:78px;background:#eee"></div>
  <button type="submit">Submit application</button>
</form></body></html>"""

NOCONFIRM_PAGE = CAPTCHA_PAGE.replace("/captcha/submit", "/noconfirm/submit").replace(
    '<div class="g-recaptcha" style="width:304px;height:78px;background:#eee"></div>', ""
)

PAGES = {
    "/gh/jobs/1": GREENHOUSE_PAGE,
    "/lever/apply": LEVER_PAGE,
    "/ashby/application": ASHBY_PAGE,
    "/leaky": LEAKY_PAGE,
    "/look": LOOKALIKE_PAGE,
    "/pdfonly": PDF_ONLY_PAGE,
    "/jdtrap": JD_TRAP_PAGE,
    "/reject": REJECT_PAGE,
    "/ambiguous": AMBIGUOUS_PAGE,
    "/custom": CUSTOM_PAGE,
    "/captcha": CAPTCHA_PAGE,
    "/noconfirm": NOCONFIRM_PAGE,
    "/gh/confirmation": "<html><body><h1>Thank you for applying.</h1></body></html>",
    "/lever/thanks": "<html><body><h3>Application submitted!</h3></body></html>",
    "/leaky/done": "<html><body><h1>We have received your application</h1></body></html>",
    "/look/done": "<html><body><h1>Application received</h1></body></html>",
    "/noconfirm/error": '<html><body><div class="error">Something went wrong</div></body></html>',
}
REDIRECTS = {
    "/gh/submit": "/gh/confirmation",
    "/lever/submit": "/lever/thanks",
    "/leaky/submit": "/leaky/done",
    "/look/submit": "/look/done",
    "/noconfirm/submit": "/noconfirm/error",
    "/custom/submit": "/gh/confirmation",
    "/captcha/submit": "/gh/confirmation",
    "/amb/submit": "/gh/confirmation",
}


class _Handler(BaseHTTPRequestHandler):
    posts: list[tuple[str, bytes]] = []

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        _Handler.posts.append((self.path, self.rfile.read(length)))
        target = REDIRECTS.get(self.path)
        if target:
            self.send_response(303)
            self.send_header("Location", target)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def _reset_posts():
    _Handler.posts.clear()


@pytest.fixture
def profile(tmp_path: Path) -> ApplicantProfile:
    resume = tmp_path / "Shruti_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake resume bytes")
    return ApplicantProfile(
        first_name="Shruti",
        last_name="Pundir",
        email="shruti@example.com",
        phone="+919876543210",
        linkedin_url="https://www.linkedin.com/in/shruti-example",
        resume_path=str(resume),
        resume_filename="Shruti_Resume.pdf",
        cover_letter="I build Python backends and would love to join.",
    )


def _agent(**kw) -> ApplyAgent:
    opts = {"allowed_hosts": {"127.0.0.1"}, "pace": 0, "timeout_ms": 10_000, "confirm_timeout_s": 4}
    opts.update(kw)
    return ApplyAgent(**opts)


def run(coro):
    return asyncio.run(coro)


def posted_paths() -> list[str]:
    return [p for p, _ in _Handler.posts]


def test_greenhouse_prepare_is_a_dry_run(server, profile):
    report = run(_agent().prepare(f"{server}/gh/jobs/1", "greenhouse", profile))
    keys = {f["key"]: f["value"] for f in report.filled}
    assert keys == {
        "first_name": "Shruti",
        "last_name": "Pundir",
        "email": "shruti@example.com",
        "phone": "+919876543210",
        "linkedin_url": "https://www.linkedin.com/in/shruti-example",
        "resume": "Shruti_Resume.pdf",
    }
    assert report.resume_attached and not report.captcha
    assert report.unanswered_required == []
    assert report.ready
    assert report.form_title == "Backend Engineer"
    assert _Handler.posts == []


def test_prepare_leaks_nothing_to_the_employer(server, profile):
    report = run(_agent().prepare(f"{server}/leaky", "greenhouse", profile))
    assert report.ready
    assert report.blocked_requests >= 1  # autosave fetches were blocked
    assert _Handler.posts == []  # no autosave, no upload — resume never attached

    result = run(_agent().submit(f"{server}/leaky", "greenhouse", profile))
    assert result.submitted, result.error
    assert "/leaky/submit" in posted_paths()


def test_greenhouse_submit_sends_fields_and_file(server, profile):
    result = run(_agent().submit(f"{server}/gh/jobs/1", "greenhouse", profile))
    assert result.submitted and result.maybe_sent, result.error
    assert result.confirmation and "confirmation" in result.confirmation
    assert posted_paths() == ["/gh/submit"]
    body = _Handler.posts[0][1]
    for needle in (b"Shruti", b"Pundir", b"shruti@example.com", b"Shruti_Resume.pdf", b"%PDF-1.4 fake"):
        assert needle in body
    # The cover-letter *file* input must not get the resume.
    assert body.count(b'filename="Shruti_Resume.pdf"') == 1


def test_lever_full_name_and_comments(server, profile):
    result = run(_agent().submit(f"{server}/lever/apply", "lever", profile))
    assert result.submitted, result.error
    keys = {f["key"] for f in result.report.filled}
    assert {"full_name", "email", "phone", "linkedin_url", "resume", "cover_letter"} <= keys
    _, body = _Handler.posts[0]
    assert b"Shruti Pundir" in body
    assert b"I build Python backends" in body
    assert b'name="org"\r\n\r\n\r\n' in body  # we do not invent a current company


def test_ashby_spa_form(server, profile):
    result = run(_agent().submit(f"{server}/ashby/application", "ashby", profile))
    assert result.submitted, result.error
    assert posted_paths() == ["/ashby/submit"]


def test_lookalike_fields_are_not_filled(server, profile):
    report = run(_agent().prepare(f"{server}/look", "greenhouse", profile))
    by_label = {f["label"]: f["value"] for f in report.filled}
    assert by_label == {
        "Name *": "Shruti Pundir",
        "Email *": "shruti@example.com",
        "Confirm email *": "shruti@example.com",
        "Phone number": "+919876543210",
        "Resume": "Shruti_Resume.pdf",
    }
    assert report.ready


def test_resume_type_not_accepted(server, profile, tmp_path):
    txt = tmp_path / "resume.txt"
    txt.write_text("plain resume")
    profile.resume_path, profile.resume_filename = str(txt), "resume.txt"
    report = run(_agent().prepare(f"{server}/pdfonly", "greenhouse", profile))
    assert not report.ready and not report.resume_attached
    assert any(".pdf" in n for n in report.notes)


def test_jd_text_is_not_a_confirmation(server, profile):
    result = run(_agent().submit(f"{server}/jdtrap", "greenhouse", profile))
    assert not result.submitted  # the old regex matched the JD and reported success
    assert result.maybe_sent  # click fired, nothing visible changed: unknown, never auto-retry
    assert "may or may not" in (result.error or "")


def test_visible_validation_error_means_not_sent(server, profile):
    result = run(_agent().submit(f"{server}/reject", "greenhouse", profile))
    assert not result.submitted and not result.maybe_sent
    assert "Phone is invalid" in (result.error or "")


def test_ambiguous_buttons_are_never_clicked(server, profile):
    result = run(_agent().submit(f"{server}/ambiguous", "greenhouse", profile))
    assert not result.submitted and not result.maybe_sent
    assert "submit button" in (result.error or "")
    assert _Handler.posts == []


def test_unanswerable_required_questions_block_submit(server, profile):
    agent = _agent()
    report = run(agent.prepare(f"{server}/custom", "greenhouse", profile))
    assert not report.ready
    assert report.unanswered_required == [
        "Are you legally authorized to work in India?",
        "Notice period",
    ]
    result = run(agent.submit(f"{server}/custom", "greenhouse", profile))
    assert not result.submitted and not result.maybe_sent
    assert "Notice period" in (result.error or "")
    assert _Handler.posts == []


def test_captcha_blocks_submit(server, profile):
    report = run(_agent().prepare(f"{server}/captcha", "greenhouse", profile))
    assert report.captcha and not report.ready
    result = run(_agent().submit(f"{server}/captcha", "greenhouse", profile))
    assert not result.submitted and not result.maybe_sent and "captcha" in (result.error or "")
    assert _Handler.posts == []


def test_missing_resume_blocks_submit(server, profile):
    profile.resume_path = None
    report = run(_agent().prepare(f"{server}/gh/jobs/1", "greenhouse", profile))
    assert not report.resume_attached and not report.ready
    assert "Resume/CV" in report.unanswered_required


def test_navigation_without_confirmation_is_unconfirmed(server, profile):
    result = run(_agent().submit(f"{server}/noconfirm", "greenhouse", profile))
    assert not result.submitted
    assert result.maybe_sent  # the POST reached the server; never claim it's safe to retry
    assert posted_paths() == ["/noconfirm/submit"]


def test_submit_aborts_when_values_differ_from_approved_plan(server, profile):
    agent = _agent()
    report = run(agent.prepare(f"{server}/gh/jobs/1", "greenhouse", profile))
    approved = {f["key"]: f["value"] for f in report.filled}

    profile.phone = "+910000000000"
    result = run(agent.submit(f"{server}/gh/jobs/1", "greenhouse", profile, expected=approved))
    assert not result.submitted and not result.maybe_sent
    assert "Phone" in (result.error or "") and "Prepare again" in (result.error or "")
    assert _Handler.posts == []

    profile.phone = "+919876543210"
    result = run(agent.submit(f"{server}/gh/jobs/1", "greenhouse", profile, expected=approved))
    assert result.submitted, result.error


def test_refuses_non_ats_host(server, profile):
    with pytest.raises(ApplyAgentError, match="non-ATS host"):
        run(ApplyAgent(pace=0).prepare(f"{server}/gh/jobs/1", "greenhouse", profile))


def test_closed_job_404(server, profile):
    with pytest.raises(ApplyAgentError, match="HTTP 404"):
        run(_agent().prepare(f"{server}/gone", "greenhouse", profile))
