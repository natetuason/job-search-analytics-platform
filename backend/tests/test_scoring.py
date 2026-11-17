from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.pipeline import get_or_create_company
from app.ingestion.normalize import dedup_key, parse_locations
from app.models import Job, JobScore, UserSettings
from app.scoring.estimate import Sample, build_index, estimate, family
from app.scoring.location import classify
from app.scoring.profile import Experience, Profile, SkillVocabulary, load_profile, load_vocabulary
from app.scoring.rules import JobFacts, Preferences, score_job, title_match
from app.scoring.run import score_all

REPO = Path(__file__).resolve().parents[2]
TUNING = __import__("yaml").safe_load((REPO / "config" / "scoring.yaml").read_text())
PREFERRED = ["New York, NY", "Jersey City, NJ", "NJ"]
SECONDARY = ["Boston, MA", "Chicago, IL", "San Francisco, CA"]

VOCAB = SkillVocabulary({"SQL": ["sql"], "Tableau": ["tableau"], "Excel": ["excel"], "Forecasting": ["forecast(?:ing)?"],
                         "Looker": ["looker"], "Stakeholder management": ["stakeholders?"]})
PROFILE = Profile(
    skills={"SQL": {"level": "strong"}, "Tableau": {"level": "strong"}, "Excel": {"level": "strong"},
            "Forecasting": {"level": "moderate"}, "Stakeholder management": {"level": "strong"}},
    experiences=[Experience("Acme Analytics", ["SQL", "Tableau"]), Experience("Coursework", ["Forecasting"])],
)


def prefs(**over) -> Preferences:
    base = Preferences(
        weights={"skills": 30, "experience": 25, "responsibilities": 20, "compensation": 15, "location": 10},
        salary_min=90000, target_titles_primary=["Business Analyst", "Data Analyst", "Demand Planning Analyst"],
        target_titles_secondary=["Product Analyst", "Associate Product Manager"], experience_max_years=3,
        secondary_location_min_score=35, needs_sponsorship=None, work_authorization=None, tuning=TUNING,
    )
    return replace(base, **over)


def facts(**over) -> JobFacts:
    base = JobFacts(title="Data Analyst", description="Use SQL and Tableau to build reports for stakeholders.",
                    employment_type="full_time", location_class="preferred", location_label="New York, NY",
                    base_min=Decimal("100000"), base_max=Decimal("120000"), pay_period="year",
                    exp_min_years=Decimal("1"))
    return replace(base, **over)


# ---------- location ----------

@pytest.mark.parametrize(("raw", "expected"), [
    ("New York, NY", "preferred"), ("NYC", "preferred"), ("Brooklyn, NY", "preferred"),
    ("New York - New York City", "preferred"), ("Hoboken, New Jersey", "preferred"), ("Newark, NJ", "preferred"),
    ("Remote - US", "remote"), ("Chicago, IL", "secondary"), ("San Francisco HQ", "secondary"),
    ("Albany, NY", "other"), ("Austin, TX", "other"), ("Toronto, ON, Canada", "foreign"),
    ("Austin, TX; Jersey City, NJ", "preferred"), (None, "other"),
])
def test_location_classes(raw, expected) -> None:
    assert classify(parse_locations(raw), PREFERRED, SECONDARY) == expected


# ---------- titles ----------

@pytest.mark.parametrize(("title", "group"), [
    ("Junior Business Analyst - Operations", "primary"), ("Demand Planning Analyst II", "primary"),
    ("Associate Product Manager, Growth", "secondary"), ("Software Engineer, New Grad", "off_target"),
    ("Quantitative Trader", "off_target"), ("Insights Associate", "analytic"), ("Barista", "none"),
    ("Quantitative Analyst II (Capital Structuring & Analytics)", "off_target"),
    ("Analyst, Early Career (Full Stack)", "off_target"),
    ("Analyst, Business Operations", "primary"), ("Demand Planning Analyst - Supply", "primary"),
    ("Product Marketing Manager", "analytic"), ("Associate Product Manager, Growth", "secondary"),
])
def test_title_match(title, group) -> None:
    primary = prefs().target_titles_primary + ["Analytics Analyst"]
    secondary = prefs().target_titles_secondary + ["Supply Chain Data Scientist"]
    assert title_match(title, primary, secondary)[0] == group
    assert title_match("Supply Chain Data Scientist", primary, secondary)[0] == "secondary"


# ---------- scoring rules ----------

def test_good_match_gets_apply_with_explanation() -> None:
    r = score_job(facts(), PROFILE, VOCAB, prefs())
    assert r.recommendation == "apply" and r.tier in ("strong_fit", "competitive")
    assert r.explanation["skills"]["matched"] == ["SQL", "Tableau", "Stakeholder management"]
    assert r.explanation["best_experiences"][0]["name"] == "Acme Analytics"
    assert "Posted base $100,000–$120,000/yr" == r.explanation["compensation"]
    assert set(r.breakdown["categories"]) == {"skills", "experience", "responsibilities", "compensation", "location"}


@pytest.mark.parametrize(("over", "reason"), [
    ({"employment_type": "internship"}, "internship"),
    ({"title": "Senior Data Analyst", "seniority": "senior"}, "senior"),
    ({"exp_min_years": Decimal("5")}, "5+ years"),
    ({"base_min": Decimal("60000"), "base_max": Decimal("80000")}, "below $90,000"),
    ({"location_class": "foreign"}, "outside the U.S."),
    ({"location_class": "other", "location_label": "Austin, TX"}, "Austin, TX"),
    ({"title": "Software Engineer"}, "target role types"),
    ({"base_min": Decimal("270000"), "base_max": Decimal("345000")}, "senior role"),
])
def test_blockers_mean_skip(over, reason) -> None:
    r = score_job(facts(**over), PROFILE, VOCAB, prefs())
    assert r.recommendation == "skip"
    assert any(reason in x for x in r.explanation["reasons"])


def test_off_target_words_and_tools_come_from_config() -> None:
    engineer = facts(title="Software Engineer, New Grad")
    assert score_job(engineer, PROFILE, VOCAB, prefs()).recommendation == "skip"
    no_rule = dict(TUNING, off_target_title_words=[])
    assert "target role types" not in " ".join(score_job(engineer, PROFILE, VOCAB, prefs(tuning=no_rule)).explanation["reasons"])
    coordinator = facts(title="Revenue Coordinator", description="SQL, Tableau and Excel daily with stakeholders.")
    other_tools = dict(TUNING, tool_skills=["Figma"])
    assert score_job(coordinator, PROFILE, VOCAB, prefs()).breakdown["categories"]["responsibilities"] == 50
    assert score_job(coordinator, PROFILE, VOCAB, prefs(tuning=other_tools)).breakdown["categories"]["responsibilities"] < 50


def test_work_arrangement_setting() -> None:
    onsite_only = prefs(work_arrangements=("onsite",))
    assert score_job(facts(work_arrangement="remote"), PROFILE, VOCAB, onsite_only).recommendation == "skip"
    assert score_job(facts(work_arrangement="unknown"), PROFILE, VOCAB, onsite_only).recommendation == "apply"
    assert score_job(facts(work_arrangement="remote"), PROFILE, VOCAB, prefs()).recommendation == "apply"


@pytest.mark.parametrize(("auth", "status"), [("U.S. citizen", "ok"), ("US citizen", "ok"),
                                              ("Non-citizen, green card", "blocks"), ("Permanent resident", "blocks")])
def test_citizenship_flag_reads_work_authorization(auth, status) -> None:
    flag = {"type": "citizenship", "label": "Requires U.S. citizenship", "evidence": "..."}
    assert score_job(facts(flags=[flag]), PROFILE, VOCAB, prefs(work_authorization=auth)).hard_flags[0]["status"] == status


def test_secondary_city_needs_min_score() -> None:
    ok = score_job(facts(location_class="secondary"), PROFILE, VOCAB, prefs())
    assert ok.recommendation != "skip"
    blocked = score_job(facts(location_class="secondary"), PROFILE, VOCAB, prefs(secondary_location_min_score=99))
    assert blocked.recommendation == "skip"


def test_rotational_bonus() -> None:
    plain = score_job(facts(), PROFILE, VOCAB, prefs())
    rot = score_job(facts(rotational=True), PROFILE, VOCAB, prefs())
    assert rot.score == min(plain.score + TUNING["rotational_bonus"], 100)


def test_estimate_is_labeled_and_never_counts_as_posted() -> None:
    r = score_job(facts(base_min=None, base_max=None, pay_period=None, est_base_min=Decimal("95000"),
                        est_base_max=Decimal("115000"), est_pay_period="year", est_confidence="low"), PROFILE, VOCAB, prefs())
    assert r.explanation["compensation"].startswith("ESTIMATE") and "not posted" in r.explanation["compensation"]
    posted = score_job(facts(base_min=Decimal("95000"), base_max=Decimal("115000")), PROFILE, VOCAB, prefs())
    assert r.breakdown["categories"]["compensation"] < posted.breakdown["categories"]["compensation"]


def test_flags_need_check_until_settings_filled() -> None:
    flag = {"type": "no_sponsorship", "label": "Says it won't sponsor work visas", "evidence": "..."}
    unknown = score_job(facts(flags=[flag]), PROFILE, VOCAB, prefs())
    assert unknown.hard_flags[0]["status"] == "needs_your_check" and unknown.recommendation == "apply"
    assert any("Needs your check" in x for x in unknown.explanation["reasons"])
    blocked = score_job(facts(flags=[flag]), PROFILE, VOCAB, prefs(needs_sponsorship=True))
    assert blocked.hard_flags[0]["status"] == "blocks" and blocked.recommendation == "maybe"
    fine = score_job(facts(flags=[flag]), PROFILE, VOCAB, prefs(needs_sponsorship=False))
    assert fine.hard_flags[0]["status"] == "ok" and fine.recommendation == "apply"


def test_clearance_flag_downgrades_to_maybe() -> None:
    flag = {"type": "clearance", "label": "Requires a security clearance", "evidence": "..."}
    assert score_job(facts(flags=[flag]), PROFILE, VOCAB, prefs()).recommendation == "maybe"


def test_non_target_title_without_my_tools_is_skip_not_maybe() -> None:
    r = score_job(facts(title="Business Development Representative", description="Cold-call prospects with Excel."),
                  PROFILE, VOCAB, prefs())
    assert r.breakdown["categories"]["responsibilities"] < 50
    assert r.recommendation == "skip" and "not one of your target role types" in r.explanation["reasons"][0]
    rotational = score_job(facts(title="Business Development Representative", rotational=True,
                                 description="Cold-call prospects with Excel."), PROFILE, VOCAB, prefs())
    assert not any("target role types" in x for x in rotational.explanation["reasons"])


def test_off_title_job_using_my_tools_is_considered() -> None:
    r = score_job(facts(title="Revenue Coordinator", description="SQL, Tableau and Excel daily with stakeholders."),
                  PROFILE, VOCAB, prefs())
    assert r.breakdown["categories"]["responsibilities"] == 50 and r.recommendation != "skip"


def test_weights_change_ranking() -> None:
    no_pay = facts(base_min=None, base_max=None, pay_period=None)
    heavy_pay = prefs(weights={"skills": 0, "experience": 0, "responsibilities": 0, "compensation": 100, "location": 0})
    assert score_job(no_pay, PROFILE, VOCAB, heavy_pay).score == TUNING["pay"]["unknown_points"]


# ---------- estimates ----------

def test_estimate_needs_enough_similar_postings() -> None:
    samples = [Sample(i, "data_analytics", True, 90000 + i * 1000) for i in range(12)]
    index = build_index(samples)
    est = estimate("Data Analyst", True, index, min_samples=10, medium_at=30)
    assert est.confidence == "low" and est.evidence["n"] == 12 and est.low < est.high
    assert estimate("Data Analyst", True, build_index(samples[:5]), min_samples=10) is None
    assert family("Demand Planning Analyst") == "supply_chain" and family("Barista") == "other"


# ---------- profile loading ----------

def test_repo_skill_vocab_loads_and_matches() -> None:
    vocab = load_vocabulary(REPO / "config" / "skills.yaml")
    found = vocab.find("Strong SQL and Power BI; R&D experience; build dashboards in Tableau; use R for stats")
    assert {"SQL", "Power BI", "Dashboards & BI reporting", "Tableau", "R"} <= set(found)
    assert "R" not in vocab.find("Partner with R&D teams")


def test_profile_rejects_unknown_skill(tmp_path: Path) -> None:
    p = tmp_path / "profile.yaml"
    p.write_text("skills:\n  Cobol: {level: strong}\n")
    with pytest.raises(ValueError, match="not in config/skills.yaml"):
        load_profile(p, VOCAB)


# ---------- end to end ----------

def _job(session: Session, title: str, location: str, description: str, **over) -> Job:
    company = get_or_create_company(session, "Acme")
    job = Job(company_id=company.id, title=title, normalized_title=title.lower(), location_raw=location,
              locations=parse_locations(location), employment_type="full_time", description_text=description,
              apply_url=f"https://acme.com/{title}", canonical_apply_url=f"https://acme.com/{title}",
              dedup_key=dedup_key("Acme", title, location), **over)
    session.add(job)
    session.flush()
    return job


def test_score_all_end_to_end(session: Session, tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("skills:\n  SQL: {level: strong, evidence: x}\n  Tableau: {level: strong, evidence: x}\n"
                       "experiences:\n  - {name: Acme Analytics, skills: [SQL, Tableau]}\n")
    good = _job(session, "Data Analyst", "New York, NY",
                "New grad role. Pay range: $95,000 - $115,000. Use SQL and Tableau.")
    senior = _job(session, "Senior Data Analyst", "Jersey City, NJ", "7+ years of experience with SQL.")
    abroad = _job(session, "Business Analyst", "London, UK", "SQL")
    summary = score_all(session, REPO / "config", profile)
    assert summary.scored == 3 and summary.pay_parsed == 1

    session.refresh(good)
    assert (good.base_min, good.comp_parse_source, good.is_early_career_program) == (
        Decimal("95000.00"), "description_text", True)
    session.refresh(senior)
    assert (senior.seniority_signal, senior.exp_min_years) == ("senior", Decimal("7.0"))
    recs = dict(session.execute(select(JobScore.job_id, JobScore.recommendation)).all())
    assert recs[good.id] == "apply" and recs[senior.id] == "skip" and recs[abroad.id] == "skip"
    version = session.get(UserSettings, 1).version
    assert session.scalar(select(JobScore.settings_version).where(JobScore.job_id == good.id)) == version

    # Re-scoring updates in place (one row per job and scorer).
    score_all(session, REPO / "config", profile)
    assert session.query(JobScore).count() == 3


def test_older_postings_rank_lower() -> None:
    fresh = score_job(facts(posted_days=5), PROFILE, VOCAB, prefs())
    undated = score_job(facts(posted_days=None), PROFILE, VOCAB, prefs())
    month = score_job(facts(posted_days=30), PROFILE, VOCAB, prefs())
    halfway = score_job(facts(posted_days=105), PROFILE, VOCAB, prefs())
    stale = score_job(facts(posted_days=1700), PROFILE, VOCAB, prefs())
    cfg = TUNING["posting_age"]
    assert fresh.score == undated.score == month.score
    assert fresh.explanation["posting_age"] is None and fresh.breakdown["age_penalty"] == 0
    assert halfway.breakdown["age_penalty"] == pytest.approx(cfg["max_penalty"] / 2)
    assert stale.breakdown["age_penalty"] == cfg["max_penalty"]
    assert stale.score == pytest.approx(fresh.score - cfg["max_penalty"])
    assert "over 4 years ago" in stale.explanation["posting_age"]


def test_age_penalty_applies_before_the_100_cap() -> None:
    # A rotational bonus that pushes a match over 100 is not wasted when the posting is old.
    capped = score_job(facts(rotational=True, posted_days=1000), PROFILE, VOCAB, prefs())
    raw = score_job(facts(rotational=True), PROFILE, VOCAB, prefs())
    assert capped.score <= raw.score
    assert capped.score >= raw.score - TUNING["posting_age"]["max_penalty"]
