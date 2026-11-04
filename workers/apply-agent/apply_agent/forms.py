"""
Generic ATS form engine.

We enumerate the application form's controls in the page (tagging each with
`data-ja-idx`), map the ones we can answer truthfully from the applicant
profile, and report every required control we could not answer. Anything
unanswered or a visible captcha means a human has to finish the form.

Field matching is deliberately strict: a label must *be* "Email", not merely
contain it ("Referrer email", "Name of current employer" must never be filled
with the applicant's details).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Tags controls inside the chosen application form and returns metadata.
INSPECT_JS = r"""
() => {
  const forms = [...document.querySelectorAll('form')];
  let form = forms.find(f => f.querySelector('input[type=file]'));
  if (!form && forms.length) {
    form = forms.reduce((a, b) => (b.elements.length > a.elements.length ? b : a));
  }
  if (!form) return { found: false, controls: [] };
  form.setAttribute('data-ja-form', '1');

  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const labelFor = (el) => {
    let t = '';
    if (el.labels && el.labels.length) t = el.labels[0].innerText;
    if (!t && el.getAttribute('aria-labelledby')) {
      t = el.getAttribute('aria-labelledby').split(/\s+/)
        .map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    }
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) {
      const box = el.closest('fieldset, .field, .form-field, .application-question, li, [class*="field"]');
      const l = box && box.querySelector('label, legend, .application-label, .text');
      if (l) t = l.innerText;
    }
    if (!t) t = el.placeholder || '';
    return clean(t);
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  const controls = [];
  let idx = 0;
  for (const el of form.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    // File inputs are usually visually hidden behind a styled button.
    if (type !== 'file' && !visible(el)) continue;
    el.setAttribute('data-ja-idx', String(idx));
    // A radio/checkbox's own label is the option ("Yes"); report the question instead.
    let label = '';
    if (type === 'radio' || type === 'checkbox') {
      const fs = el.closest('fieldset');
      const legend = fs && fs.querySelector('legend');
      if (legend) label = clean(legend.innerText);
    }
    if (!label) label = labelFor(el);
    let empty;
    if (type === 'file') empty = !(el.files && el.files.length);
    else if (type === 'checkbox' || type === 'radio') {
      empty = !(el.name
        ? form.querySelector(`input[name="${CSS.escape(el.name)}"]:checked`)
        : el.checked);
    } else empty = !clean(el.value);
    controls.push({
      idx, tag: el.tagName.toLowerCase(), type,
      name: el.getAttribute('name') || '', id: el.id || '',
      label,
      accept: el.getAttribute('accept') || '',
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /[*✱]/.test(label)),
      // Dry runs mark the resume input as planned instead of attaching the file.
      planned: el.hasAttribute('data-ja-planned'),
      empty,
    });
    idx += 1;
  }
  return { found: true, controls };
}
"""

CAPTCHA_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 30 && r.height > 30 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  for (const f of document.querySelectorAll('iframe')) {
    const src = f.src || '';
    if (/size=invisible/.test(src)) continue;
    if (/recaptcha\/(api2|enterprise)\/(anchor|bframe)|hcaptcha\.com|challenges\.cloudflare\.com/.test(src) && vis(f)) {
      return true;
    }
  }
  const box = document.querySelector('.g-recaptcha:not([data-size="invisible"]), .h-captcha:not([data-size="invisible"]), .cf-turnstile');
  return !!(box && vis(box));
}
"""

# Picks the submit control by its text only. Returns a reason string on failure;
# never falls back to "some button" (an 'Add another' / 'Attach' click is not a submit).
SUBMIT_JS = r"""
() => {
  const form = document.querySelector('form[data-ja-form]');
  if (!form) return 'Application form disappeared before submitting.';
  const text = (b) => (b.innerText || b.value || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const cands = [...form.querySelectorAll('button, input[type=submit]')]
    .filter(b => !b.disabled && (b.offsetWidth || b.offsetHeight) && b.type === 'submit');
  const ok = /^(submit|apply|send)( my| your)?( job)?( application)?( now)?$|^submit\b/i;
  const bad = /linkedin|indeed|autofill|auto-fill|upload|attach|add|remove|cancel|back|save|draft/i;
  const hits = cands.filter(b => ok.test(text(b)) && !bad.test(text(b)));
  if (hits.length !== 1) {
    return hits.length ? 'More than one submit button — not guessing which to click.'
                       : "Could not identify the form's submit button.";
  }
  hits[0].setAttribute('data-ja-submit', '1');
  return '';
}
"""

PAGE_STATE_JS = r"""
() => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const errs = [];
  for (const el of document.querySelectorAll('[role=alert], .error, .field-error, .error-message, [class*="error" i]')) {
    const t = clean(el.innerText);
    if (t && t.length < 200 && (el.offsetWidth || el.offsetHeight)) errs.push(t);
  }
  const form = document.querySelector('form[data-ja-form]');
  const formVisible = !!(form && (form.offsetWidth || form.offsetHeight));
  return {
    text: clean(document.body ? document.body.innerText : '').slice(0, 200000),
    errors: [...new Set(errs)].slice(0, 8),
    form_present: formVisible,
  };
}
"""

TITLE_JS = r"""
() => {
  const h = document.querySelector('h1') || document.querySelector('h2');
  return ((h && h.innerText) || document.title || '').replace(/\s+/g, ' ').trim().slice(0, 300);
}
"""

# Only phrases that mean "we got it". Not "thank you for your interest" —
# job descriptions end with that line all the time.
CONFIRMATION_RE = re.compile(
    r"thank(s| you)( so much)? for (applying|your application|submitting)"
    r"|application (has been |was )?(successfully )?(submitted|received)"
    r"|we('ve| have) (successfully )?received your application"
    r"|your application (has been|was) (successfully )?(submitted|received|sent)",
    re.IGNORECASE,
)
CONFIRMATION_URL_RE = re.compile(r"confirmation|/thanks|thank-you|thank_you|/submitted|/success", re.IGNORECASE)


def confirmation_hits(text: str) -> Counter[str]:
    return Counter(m.group(0).lower() for m in CONFIRMATION_RE.finditer(text or ""))


def judge_after_submit(
    *,
    base_url: str,
    base_hits: Counter[str],
    url: str,
    state: dict[str, Any],
) -> tuple[str, str | None]:
    """
    Decide what a page looks like after the submit click.

    Returns ("confirmed", evidence) | ("rejected", reason) | ("pending", None).
    Confirmation must be *new* compared with the pre-click snapshot, and the
    form must be gone or the page must have navigated.
    """
    strip = lambda u: (u or "").split("#", 1)[0]  # noqa: E731
    navigated = strip(url) != strip(base_url)
    form_present = bool(state.get("form_present"))

    if navigated and CONFIRMATION_URL_RE.search(url) and not CONFIRMATION_URL_RE.search(base_url):
        return "confirmed", url
    hits = confirmation_hits(state.get("text", ""))
    new = [phrase for phrase, n in hits.items() if n > base_hits.get(phrase, 0)]
    if new and (navigated or not form_present):
        return "confirmed", new[0]
    if not navigated and form_present and state.get("errors"):
        return "rejected", "; ".join(state["errors"])
    return "pending", None


@dataclass
class ApplicantProfile:
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    linkedin_url: str | None = None
    resume_path: str | None = None
    resume_filename: str | None = None
    cover_letter: str | None = None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()


def normalize_label(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\((required|optional)\)", " ", t)
    t = re.sub(r"[*✱:?]", " ", t)
    t = re.sub(r"[^a-z0-9/'\- ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Whole-label patterns (after normalize_label).
_LABEL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("first_name", re.compile(r"^(legal |preferred )?first name$|^given name$|^first$")),
    ("last_name", re.compile(r"^(legal )?(last name|surname|family name)$|^last$")),
    ("full_name", re.compile(r"^(full |legal |your |candidate )?name$|^(full )?name first and last$|^first and last name$")),
    ("email", re.compile(r"^(your |personal )?e-?mail( address)?$")),
    ("email_confirm", re.compile(r"^(confirm|re-?enter|repeat) (your )?e-?mail( address)?$")),
    ("phone", re.compile(r"^(your )?(phone|mobile|cell|mobile phone|cell phone|phone number|mobile number|contact number)( number)?$")),
    ("linkedin_url", re.compile(r"^linkedin( profile)?( url| link)?$")),
]
# Exact name/id attributes used by Greenhouse / Lever / Ashby.
_ATTR_RULES: dict[str, str] = {
    "first_name": "first_name",
    "firstname": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "name": "full_name",
    "full_name": "full_name",
    "_systemfield_name": "full_name",
    "email": "email",
    "_systemfield_email": "email",
    "phone": "phone",
    "urls[linkedin]": "linkedin_url",
}
_COVER_RE = re.compile(r"^(cover letter|additional information|anything else\b.*|comments)$")
_RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum")
_COVER_ANY_RE = re.compile(r"cover")

_TEXT_TYPES = {"text", "email", "tel", "url", "input", ""}


def classify_control(control: dict[str, Any]) -> str | None:
    """Map one inspected control to a profile key, or None if we should not answer it."""
    ctype = control.get("type", "")
    label = normalize_label(str(control.get("label") or ""))
    attrs = [str(control.get("name") or "").lower(), str(control.get("id") or "").lower()]

    if ctype == "file":
        hay = " ".join([label, *attrs])
        if _RESUME_RE.search(hay) and not _COVER_ANY_RE.search(hay):
            return "resume"
        return None
    if control.get("tag") == "textarea":
        return "cover_letter" if _COVER_RE.match(label) or "comments" in attrs else None
    if control.get("tag") != "input" or ctype not in _TEXT_TYPES:
        return None

    if label:
        for key, pattern in _LABEL_RULES:
            if pattern.match(label):
                return key
        # A labelled field we don't recognise is someone else's question.
        if not any(a in _ATTR_RULES for a in attrs):
            return None
    for attr in attrs:
        if attr in _ATTR_RULES:
            return _ATTR_RULES[attr]
    return None


def profile_value(profile: ApplicantProfile, key: str) -> str | None:
    value = {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "full_name": profile.full_name,
        "email": profile.email,
        "email_confirm": profile.email,
        "phone": profile.phone,
        "linkedin_url": profile.linkedin_url,
        "cover_letter": profile.cover_letter,
        "resume": profile.resume_path,
    }.get(key)
    if isinstance(value, str):
        value = value.strip()
    return value or None


_MIME_EXT = {
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "text/plain": {".txt"},
    "application/rtf": {".rtf"},
    "text/rtf": {".rtf"},
}


def file_accepted(filename: str | None, accept: str | None) -> bool:
    """Mirror the browser's `accept` filter so we never pick a file the form rejects."""
    if not filename:
        return False
    tokens = [t.strip().lower() for t in (accept or "").split(",") if t.strip()]
    if not tokens:
        return True
    ext = Path(filename).suffix.lower()
    for token in tokens:
        if token.startswith(".") and token == ext:
            return True
        if token in {"*/*", "*"}:
            return True
        if token.endswith("/*") and ext in {e for m, es in _MIME_EXT.items() if m.startswith(token[:-1]) for e in es}:
            return True
        if ext in _MIME_EXT.get(token, set()):
            return True
    return False


def plan_fills(
    controls: list[dict[str, Any]],
    profile: ApplicantProfile,
) -> list[tuple[dict[str, Any], str, str]]:
    """Return (control, key, value) for each control we will fill — at most one per key."""
    keys = {c["idx"]: classify_control(c) for c in controls}
    has_split_name = any(k in {"first_name", "last_name"} for k in keys.values())
    used: set[str] = set()
    plan: list[tuple[dict[str, Any], str, str]] = []
    for control in controls:
        key = keys[control["idx"]]
        if key is None or key in used:
            continue
        if key == "full_name" and has_split_name:
            continue
        value = profile_value(profile, key)
        if value is None:
            continue
        if key == "resume" and not file_accepted(profile.resume_filename, control.get("accept")):
            continue
        used.add(key)
        plan.append((control, key, value))
    return plan


def unanswered_required(controls: list[dict[str, Any]]) -> list[str]:
    """Labels of required controls that are still empty (radio groups counted once)."""
    out: list[str] = []
    seen_groups: set[str] = set()
    for c in controls:
        if not c.get("required") or not c.get("empty") or c.get("planned"):
            continue
        if c.get("type") in {"radio", "checkbox"} and c.get("name"):
            if c["name"] in seen_groups:
                continue
            seen_groups.add(c["name"])
        label = str(c.get("label") or c.get("name") or c.get("id") or "Unlabelled field")
        out.append(label.rstrip(" *✱").strip() or "Unlabelled field")
    return out


def title_overlaps(job_title: str | None, form_title: str | None) -> bool:
    """Loose check that the opened form is for the job we matched (warns, never blocks)."""
    stop = {"and", "the", "for", "with", "job", "jobs", "role", "at", "of", "in", "a", "an", "-", "–", "|",
            "application", "apply"}
    # Words every tech title shares prove nothing ("Data Engineer" vs "Backend Engineer").
    stop |= {"engineer", "engineering", "developer", "software", "senior", "sr", "junior", "jr", "lead",
             "staff", "principal", "intern", "internship", "associate", "ii", "iii", "iv", "trainee", "fresher"}
    tokens = lambda s: {w for w in re.findall(r"[a-z0-9+#.]+", (s or "").lower()) if w not in stop and len(w) > 1}  # noqa: E731
    a, b = tokens(job_title), tokens(form_title)
    if not a or not b:
        return True
    return bool(a & b)
