from apply_agent.detect import ASHBY, GREENHOUSE, LEVER, detect_ats, find_ats_urls, first_ats_target

LEVER_ID = "0f3c2a1b-1234-4abc-9def-0123456789ab"
ASHBY_ID = "a1b2c3d4-e5f6-4711-8899-aabbccddeeff"


def test_greenhouse_variants_canonicalize():
    for url in (
        "https://boards.greenhouse.io/acme/jobs/4012345",
        "https://job-boards.greenhouse.io/acme/jobs/4012345/",
        "https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345",
        "http://boards.greenhouse.io/acme/jobs/4012345?gh_src=abc",
    ):
        t = detect_ats(url)
        assert t is not None, url
        assert t.provider == GREENHOUSE
        assert t.company == "acme" and t.job_id == "4012345"
        assert t.apply_url == "https://job-boards.greenhouse.io/acme/jobs/4012345"


def test_greenhouse_eu_host_kept():
    t = detect_ats("https://job-boards.eu.greenhouse.io/acme/jobs/77")
    assert t and t.apply_url == "https://job-boards.eu.greenhouse.io/acme/jobs/77"


def test_lever_and_ashby():
    t = detect_ats(f"https://jobs.lever.co/acme/{LEVER_ID.upper()}")
    assert t and t.provider == LEVER
    assert t.apply_url == f"https://jobs.lever.co/acme/{LEVER_ID}/apply"

    t = detect_ats(f"https://jobs.ashbyhq.com/acme/{ASHBY_ID}/application")
    assert t and t.provider == ASHBY
    assert t.apply_url == f"https://jobs.ashbyhq.com/acme/{ASHBY_ID}/application"


def test_rejects_non_job_and_lookalike_urls():
    for url in (
        None,
        "",
        "https://boards.greenhouse.io/acme",  # board index, not a job
        "https://jobs.lever.co/acme",
        "https://jobs.lever.co/acme/not-a-uuid/apply",
        "https://evil.com/boards.greenhouse.io/acme/jobs/1",
        "https://boards.greenhouse.io.evil.com/acme/jobs/1",
        "javascript:alert(1)",
        "https://www.naukri.com/job-listings-python-123",
    ):
        assert detect_ats(url) is None, url


def test_find_in_linkedin_external_apply_html():
    html = (
        '<code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/'
        "externalApply/3900000000?url=https%3A%2F%2Fjobs%2Elever%2Eco%2Facme%2F"
        f'{LEVER_ID}%3Flever-source%3DLinkedIn&amp;urlHash=abc"--></code>'
    )
    hits = find_ats_urls(html)
    assert [h.apply_url for h in hits] == [f"https://jobs.lever.co/acme/{LEVER_ID}/apply"]


def test_find_in_description_dedupes():
    text = (
        "Apply at https://boards.greenhouse.io/acme/jobs/55. "
        "Or https://job-boards.greenhouse.io/acme/jobs/55."
    )
    assert len(find_ats_urls(text)) == 1


def test_first_ats_target_order():
    assert first_ats_target(None, "no links here") is None
    t = first_ats_target(
        "https://www.linkedin.com/jobs/view/1",
        f"see https://jobs.ashbyhq.com/acme/{ASHBY_ID}",
    )
    assert t and t.provider == ASHBY
