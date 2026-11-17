from decimal import Decimal

import pytest

from app.scoring.extract import early_career, hard_flags, parse_experience, parse_pay, rotational, seniority


@pytest.mark.parametrize(("text", "expected"), [
    ("The applicable salary range for this position is $90,000 - $112,000 USD, which", (90000, 112000, "year")),
    ("Pay Range\n$86,000—$101,000 USD\n", (86000, 101000, "year")),
    ("Salary/Payrate: $110K to $130K \n", (110000, 130000, "year")),
    ("The base pay for this role is: $75,348- $98,894 per year.", (75348, 98894, "year")),
    ("Hourly rate: $28.50 - $32.00 per hour", (Decimal("28.50"), 32, "hour")),
    ("Base pay range by location:\nZone 2: $80,000 - $95,000\nNew York: $95,000 - $115,000", (95000, 115000, "year")),
])
def test_parse_pay(text, expected) -> None:
    pay = parse_pay(text)
    assert (pay.base_min, pay.base_max, pay.pay_period) == (Decimal(expected[0]), Decimal(expected[1]), expected[2])


@pytest.mark.parametrize("text", [
    "We move more than $19B of merchandise",
    "Sales bonus potential: $20,000 - $40,000 annually",
    "- $360 per quarter to spend on tickets",
    "health plans (some as low as $0 per paycheck)",
    "$0.01—$0.01 USD",
    None,
])
def test_parse_pay_ignores_non_base_amounts(text) -> None:
    assert parse_pay(text) is None


def test_parse_experience_takes_largest_required() -> None:
    text = "- 1+ year experience with fraud statutes\n- 3+ years of experience leading projects\n- 5+ years preferred"
    exp = parse_experience(text)
    assert exp.min_years == 3 and "leading projects" in exp.snippet


@pytest.mark.parametrize("text", [
    "Founded over 30 years of investing experience ago",
    "10 paid holidays throughout the calendar year",
    "2 weeks paid time off each year",
    "- 4 years of college",
    "- 5+ years of SQL experience preferred",
])
def test_parse_experience_ignores_non_requirements(text) -> None:
    assert parse_experience(text) is None


def test_parse_experience_ranges() -> None:
    exp = parse_experience("- 1.5 to 4 years of experience in compliance")
    assert (exp.min_years, exp.max_years) == (Decimal("1.5"), Decimal("4"))


@pytest.mark.parametrize(("title", "expected"), [
    ("Senior Data Analyst", "senior"), ("Sr. Business Analyst", "senior"), ("Lead Analyst", "senior"),
    ("Manager, Strategy", "senior"), ("Associate Product Manager", None), ("Project Manager", None),
    ("Data Analyst II", "level_ii"), ("Business Analyst III", "level_iii"), ("Data Analyst", None),
    ("Analyst 1, Planning", None), ("Product Marketing Manager - Commercial Content", "senior"),
    ("Associate Manager - Field Marketing", None), ("Associate Product Manager, Growth", None),
])
def test_seniority(title, expected) -> None:
    assert seniority(title) == expected


def test_early_career_and_rotational() -> None:
    assert early_career("Business Analyst - New Grad 2027", None)
    assert early_career("Analyst", "Our campus program hires recent graduates")
    assert not early_career("Analyst", "5+ years of experience")
    assert rotational("Finance Rotational Program Analyst", None)
    assert rotational("Analyst", "a two-year leadership development program")
    assert not rotational("Analyst", "rotate on-call duties")


@pytest.mark.parametrize(("text", "kinds"), [
    ("We are unable to sponsor visas for this role.", {"no_sponsorship"}),
    ("Must be authorized to work without the need for sponsorship now or in the future", {"no_sponsorship"}),
    ("Sponsorship (from list): Does Not Offer Sponsorship", {"no_sponsorship"}),
    ("Applicants must be a U.S. citizen.", {"citizenship"}),
    ("- Active TOP SECRET security clearance (TS/SCI eligibility preferred).", {"clearance"}),
    ("eligible for a US security clearance (preferred but not required)", set()),
    ("- FINRA Series 7 required", {"certification"}),
    ("- CPA preferred", set()),
    ("Degrees (from list): Master's, PhD", {"advanced_degree"}),
    ("Degrees (from list): Bachelor's, Master's", set()),
    ("We offer visa sponsorship.", set()),
])
def test_hard_flags(text, kinds) -> None:
    assert {f["type"] for f in hard_flags("Analyst", text)} == kinds
