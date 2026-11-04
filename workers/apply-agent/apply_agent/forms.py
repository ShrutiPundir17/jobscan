"""
Generic ATS form engine.

We enumerate the application form's controls in the page (tagging each with
`data-ja-idx`), map the ones we can answer truthfully from the applicant
profile, and report every required control we could not answer. Anything
unanswered or a visible captcha means a human has to finish the form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(label)),
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

SUBMIT_JS = r"""
() => {
  const form = document.querySelector('form[data-ja-form]');
  if (!form) return false;
  const cands = [...form.querySelectorAll('button, input[type=submit]')]
    .filter(b => !b.disabled && (b.offsetWidth || b.offsetHeight));
  const text = (b) => (b.innerText || b.value || '').trim();
  const btn = cands.find(b => /submit|send application|apply/i.test(text(b)) && (b.type || 'submit') === 'submit')
    || cands.find(b => /submit application|submit/i.test(text(b)))
    || cands.find(b => (b.getAttribute('type') || 'submit') === 'submit');
  if (!btn) return false;
  btn.setAttribute('data-ja-submit', '1');
  return true;
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
  return { text: clean(document.body ? document.body.innerText : '').slice(0, 4000), errors: [...new Set(errs)].slice(0, 8) };
}
"""

CONFIRMATION_RE = re.compile(
    r"thank(s| you)( so much)? for (applying|your application|your interest)"
    r"|application (has been |was )?(successfully )?(submitted|received)"
    r"|we('ve| have) received your application"
    r"|your application (has been|was) (successfully )?(submitted|received|sent)",
    re.IGNORECASE,
)
CONFIRMATION_URL_RE = re.compile(r"confirmation|/thanks|thank-you|thank_you|submitted", re.IGNORECASE)


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


_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("first_name", re.compile(r"\bfirst[\s_-]*name\b|given[\s_-]*name|^first$", re.I)),
    ("last_name", re.compile(r"\blast[\s_-]*name\b|surname|family[\s_-]*name|^last$", re.I)),
    ("email", re.compile(r"e-?mail", re.I)),
    ("phone", re.compile(r"phone|mobile", re.I)),
    ("linkedin_url", re.compile(r"linked\s*in", re.I)),
    ("full_name", re.compile(r"^(full[\s_-]*|your[\s_-]*|legal[\s_-]*)?name\b|_systemfield_name", re.I)),
]
_RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)
_COVER_RE = re.compile(r"cover[\s_-]*letter|additional[\s_-]*information|anything else|comments", re.I)


def classify_control(control: dict[str, Any]) -> str | None:
    """Map one inspected control to a profile key, or None if we should not answer it."""
    ctype = control.get("type", "")
    label = str(control.get("label") or "")
    name = str(control.get("name") or "")
    cid = str(control.get("id") or "")
    keys = [label, name, cid]

    if ctype == "file":
        if any(_RESUME_RE.search(k) for k in keys) and not any(_COVER_RE.search(k) for k in keys):
            return "resume"
        return None
    if control.get("tag") == "textarea":
        return "cover_letter" if any(_COVER_RE.search(k) for k in keys) else None
    if control.get("tag") != "input" or ctype in {"checkbox", "radio", "date", "number"}:
        return None
    if ctype == "email":
        return "email"
    if ctype == "tel":
        return "phone"
    # Lever's plain `name="name"` full-name input.
    if name == "name" or cid == "name":
        return "full_name"
    if re.search(r"urls\[linkedin\]", name, re.I):
        return "linkedin_url"
    for key, pattern in _RULES:
        if any(pattern.search(k) for k in keys if k):
            return key
    return None


def profile_value(profile: ApplicantProfile, key: str) -> str | None:
    value = {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin_url": profile.linkedin_url,
        "cover_letter": profile.cover_letter,
        "resume": profile.resume_path,
    }.get(key)
    if isinstance(value, str):
        value = value.strip()
    return value or None


def plan_fills(
    controls: list[dict[str, Any]],
    profile: ApplicantProfile,
) -> list[tuple[dict[str, Any], str, str]]:
    """Return (control, key, value) for each control we will fill — at most one per key."""
    has_split_name = any(classify_control(c) in {"first_name", "last_name"} for c in controls)
    used: set[str] = set()
    plan: list[tuple[dict[str, Any], str, str]] = []
    for control in controls:
        key = classify_control(control)
        if key is None or key in used:
            continue
        if key == "full_name" and has_split_name:
            continue
        value = profile_value(profile, key)
        if value is None:
            continue
        used.add(key)
        plan.append((control, key, value))
    return plan


def unanswered_required(controls: list[dict[str, Any]]) -> list[str]:
    """Labels of required controls that are still empty (radio groups counted once)."""
    out: list[str] = []
    seen_groups: set[str] = set()
    for c in controls:
        if not c.get("required") or not c.get("empty"):
            continue
        if c.get("type") in {"radio", "checkbox"} and c.get("name"):
            if c["name"] in seen_groups:
                continue
            seen_groups.add(c["name"])
        label = str(c.get("label") or c.get("name") or c.get("id") or "Unlabelled field")
        out.append(label.rstrip(" *").strip() or "Unlabelled field")
    return out
