"""The customer's name as the hub holds it: found in the answers of the read only customer tools
after a sign in, never taken from a package or service name."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from mcp_gateway import profile  # noqa: E402


@pytest.mark.parametrize("data,expected", [
    ({"customerName": "NIMAL PERERA"}, "Nimal Perera"),
    ({"status": "success", "customer_name": "Kamala Silva"}, "Kamala Silva"),
    ({"result": {"accountHolder": {"name": "Sunil de Silva"}}}, "Sunil de Silva"),
    ({"subscriberName": "  Anura   Kumara  "}, "Anura Kumara"),
    ({"firstName": "Nimal", "lastName": "Perera"}, "Nimal Perera"),
    ({"customerName": "නිමල් පෙරේරා"}, "නිමල් පෙරේරා"),
    ({"customerName": "நிமல் பெரேரா"}, "நிமல் பெரேரா"),
    ({"customerName": "D'Souza-Fernando"}, "D'Souza-Fernando"),
    ({"customerName": "kamala"}, "Kamala"),
])
def test_a_person_name_field_is_found(data, expected):
    assert profile.extract_name(data) == expected


@pytest.mark.parametrize("data", [
    {"packageName": "Fibre Unlimited"}, {"serviceName": "Broadband"}, {"name": "Fibre Unlimited 60"},
    {"packages": [{"name": "Daily five GB"}]}, {"listofBBSService": [{"serviceName": "Home Broadband"}]},
    {"bankName": "Commercial Bank"}, {"channelName": "Online"}, {"cityName": "Colombo"},
    {"customerName": "n/a"}, {"customerName": "null"}, {"customerName": "nimal@example.com"}, {"customerName": "A"},
    {"customerName": "SLT Mobitel Pvt Ltd"}, {"customerName": ""}, {"customerName": None}, {"customerName": 123},
    {}, [], "text", None,
])
def test_names_of_things_and_junk_are_ignored(data):
    assert profile.extract_name(data) is None


def test_the_customer_field_beats_a_bare_name_field():
    data = {"name": "Some Label", "details": {"customerName": "Nimal Perera"}}
    assert profile.extract_name(data) == "Nimal Perera"


class Out:
    def __init__(self, text):
        self.text = text


class FakeSession:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def call(self, tool, arguments, timeout=None):
        self.calls.append((tool, arguments, timeout))
        answer = self.answers.get(tool)
        if isinstance(answer, Exception):
            raise answer
        return Out(answer if isinstance(answer, str) else json.dumps(answer))


def test_the_first_tool_that_carries_a_name_wins_and_the_rest_are_not_asked():
    session = FakeSession({
        "show_service_detail": "Upstream error 405 Method Not Allowed",
        "show_bill_history": {"accountName": "Kamala Silva", "billAmount": 100},
        "show_last_payment": {"customerName": "Someone Else"},
    })
    assert profile.fetch_name(session, "0112345678", 6) == "Kamala Silva"
    assert [c[0] for c in session.calls] == ["show_service_detail", "show_bill_history"]
    assert all(c[1] == {"telephone_no": "0112345678"} for c in session.calls)


def test_a_failing_or_name_less_tool_only_means_no_name():
    session = FakeSession({
        "show_service_detail": TimeoutError("slow"),
        "show_bill_history": {"billAmount": 1},
        "show_last_payment": {"paymentAmount": 2},
        "show_gb_balance": {"packageName": "Fibre"},
    })
    assert profile.fetch_name(session, "0112345678", 6) is None
    assert len(session.calls) == 4


def test_the_budget_stops_the_search():
    ticks = iter([0.0, 0.1, 0.2, 5.9, 6.5, 7.0, 7.5, 8.0])
    session = FakeSession({"show_service_detail": {"billAmount": 1}, "show_bill_history": {"billAmount": 1}})
    assert profile.fetch_name(session, "0112345678", 6, clock=lambda: next(ticks)) is None
    assert len(session.calls) == 1, "the second tool is skipped when under half a second is left"


def test_a_zero_budget_asks_nothing():
    session = FakeSession({})
    assert profile.fetch_name(session, "0112345678", 0) is None and session.calls == []


@pytest.fixture(autouse=True)
def _fresh_state():
    profile.reset()
    yield
    profile.reset()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_tool_that_gave_a_name_is_asked_first_next_time():
    nameless = {"show_service_detail": {"billAmount": 1}, "show_bill_history": {"accountName": "Kamala Silva"}}
    assert profile.fetch_name(FakeSession(nameless), "0112345678", 6) == "Kamala Silva"
    second = FakeSession(nameless)
    assert profile.fetch_name(second, "0112345678", 6) == "Kamala Silva"
    assert [c[0] for c in second.calls] == ["show_bill_history"]


def test_a_hub_that_never_gives_a_name_is_left_alone_for_a_while():
    clock = Clock()
    empty = {t: {"billAmount": 1} for t in profile.TOOLS}
    for _ in range(profile.MISS_LIMIT):
        assert profile.fetch_name(FakeSession(empty), "0112345678", 6, clock=clock) is None
    resting = FakeSession(empty)
    assert profile.fetch_name(resting, "0112345678", 6, clock=clock) is None
    assert resting.calls == [], "no calls during the rest"
    clock.now += profile.REST_SECONDS + 1
    again = FakeSession({"show_service_detail": {"customerName": "Kamala Silva"}})
    assert profile.fetch_name(again, "0112345678", 6, clock=clock) == "Kamala Silva"
