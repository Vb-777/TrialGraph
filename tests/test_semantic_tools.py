import json

import pytest

from trialgraph import agent, semantic
from trialgraph.tools import SPECS, Tools


def test_specs_are_valid_tool_schemas():
    for s in SPECS:
        assert set(s) == {"name", "description", "input_schema"} and s["input_schema"]["type"] == "object"


def test_fanout_safe_vs_naive(con):
    # group by a fan-out dimension: per-trial sums must never exceed the trial-grain total
    total = semantic.query(con, "planned_enrollment")[0]["planned_enrollment"]
    naive = con.execute("SELECT sum(f.enrollment_planned) FROM gold.fct_trial f "
                        "JOIN gold.brg_trial_condition b ON b.nct_id = f.nct_id").fetchone()[0]
    safe = con.execute(f"SELECT sum(planned_enrollment) FROM ({semantic.compile('planned_enrollment', ['condition'])})").fetchone()[0]
    assert naive > total                 # the trap is real in the fixture
    assert semantic.query(con, "trials", where={"condition": [r[0] for r in con.execute(
        "SELECT DISTINCT mesh_term FROM gold.dim_condition").fetchall()]})[0]["trials"] <= \
        semantic.query(con, "trials")[0]["trials"]
    assert safe >= total                 # grouped: a trial counts once per group, by design


def test_unknown_names_abstain(con):
    t = Tools(con)
    assert t.call("resolve_intervention", {"name": "Notarealumab"})["status"] == "not_found"
    assert t.call("get_trial", {"nct_id": "NCT99999999"})["status"] == "not_found"
    assert t.call("get_trial", {"nct_id": "drop table"})["status"] == "error"
    r = t.call("metric", {"metric": "trials", "where": {"bogus": 1}})
    assert r["status"] == "error" and "known" in r["error"]


def test_sql_injection_in_filter_values_is_inert(con):
    t = Tools(con)
    r = t.call("metric", {"metric": "trials", "where": {"status": "x'); DROP TABLE gold.fct_trial; --"}})
    assert r["status"] == "ok" and r["rows"][0]["trials"] == 0
    assert con.execute("SELECT count(*) FROM gold.fct_trial").fetchone()[0] > 0


class _Resp:
    def __init__(self, content, stop):
        self.content, self.stop_reason = content, stop


class FakeClient:
    """Scripted stand-in for a tool-use model client: replays a fixed conversation."""
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)(kw)


def test_agent_loop_grounds_citations(con):
    some = con.execute("SELECT nct_id FROM gold.fct_trial WHERE overall_status='RECRUITING' ORDER BY 1 LIMIT 1").fetchone()[0]
    script = [
        lambda kw: _Resp([{"type": "tool_use", "id": "t1", "name": "find_trials",
                           "input": {"where": {"status": "RECRUITING"}, "limit": 5}}], "tool_use"),
        lambda kw: _Resp([{"type": "text", "text": f"For example {some}, and also NCT12345678."}], "end_turn"),
    ]
    client = FakeClient(script)
    r = agent.run("Which trials are recruiting?", Tools(con), client)
    assert r["citations"] == [some]
    assert r["ungrounded"] == ["NCT12345678"] and r["grounded"] is False
    tool_result = client.calls[1]["messages"][2]["content"][0]  # list is shared; index, not [-1]
    assert tool_result["type"] == "tool_result" and json.loads(tool_result["content"])["status"] == "ok"
