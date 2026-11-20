"""A minimal tool-use loop over the TrialGraph tool contract.

The model may only answer through the tools; it never sees the warehouse or
writes SQL. After the loop ends, every NCT ID in the final answer is checked
against the citations the tools actually returned in this conversation. An ID
the tools never produced is reported as ungrounded - the cheapest reliable
hallucination check there is, because it needs no second model.

The client is injected, so the loop is provider-neutral and is tested with a
scripted fake client.
"""
from __future__ import annotations

import json
import os
import re

from .tools import SPECS, Tools

SYSTEM = (
    "You answer questions about heart-failure clinical trials using only the tools provided. "
    "Call `describe` first if unsure which metric or dimension to use. Every number must come from "
    "`metric`; never estimate. Cite trials by NCT ID. If a tool returns not_found, say the data does "
    "not contain it - do not substitute a similar name."
)
_NCT = re.compile(r"NCT\d{8}")


def _blocks(content):
    for b in content:
        yield b if isinstance(b, dict) else b.model_dump()


def run(question: str, tools: Tools, client, model: str | None = None, max_steps: int = 8) -> dict:
    model = model or os.environ.get("TRIALGRAPH_MODEL", "tool-model")
    messages = [{"role": "user", "content": question}]
    cited: set[str] = set()
    trace = []

    for _ in range(max_steps):
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=SYSTEM,
            tools=SPECS,
            messages=messages,
        )
        blocks = list(_blocks(resp.content))
        messages.append({"role": "assistant", "content": blocks})

        if resp.stop_reason != "tool_use":
            text = "".join(b.get("text", "") for b in blocks if b["type"] == "text")
            mentioned = set(_NCT.findall(text))
            return {
                "answer": text,
                "trace": trace,
                "citations": sorted(mentioned & cited),
                "ungrounded": sorted(mentioned - cited),
                "grounded": not (mentioned - cited),
            }

        results = []
        for b in blocks:
            if b["type"] != "tool_use":
                continue

            out = tools.call(b["name"], b.get("input") or {})
            cited |= set(out.get("citations", []))
            cited |= set(_NCT.findall(json.dumps(out, default=str)))

            trace.append({
                "tool": b["name"],
                "input": b.get("input"),
                "status": out.get("status"),
            })

            results.append({
                "type": "tool_result",
                "tool_use_id": b["id"],
                "content": json.dumps(out, default=str)[:20000],
                "is_error": out.get("status") == "error",
            })

        messages.append({"role": "user", "content": results})

    return {
        "answer": None,
        "trace": trace,
        "citations": [],
        "ungrounded": [],
        "grounded": False,
        "error": f"no final answer within {max_steps} steps",
    }
