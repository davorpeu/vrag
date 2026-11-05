import os

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")

st.set_page_config(page_title="versioned-rag", layout="wide")
st.title("versioned-rag")
st.caption(
    "Ask about the latest rules, a specific version (\"what did v1 say...\", \"in March 2024...\"), "
    "or what changed between versions. Every answer cites the exact version it used."
)

ROUTE_LABELS = {
    "latest": "Latest version",
    "version": "Specific version",
    "compare": "Comparison",
    "unclear": "Needs clarification",
}

question = st.text_input("Question", placeholder="How many days do customers have to request a refund?")

if st.button("Ask", type="primary") and question:
    with st.spinner("Searching and answering (local model, can take ~30s)..."):
        try:
            r = httpx.post(f"{API_URL}/ask", json={"question": question}, timeout=300)
            r.raise_for_status()
            res = r.json()
        except httpx.HTTPError as e:
            st.error(f"API error: {e}")
            st.stop()

    route = res.get("route") or {}
    if route:
        bits = [ROUTE_LABELS.get(route["intent"], route["intent"])]
        if route.get("doc_id"):
            bits.append(route["doc_id"])
        if route.get("versions"):
            bits.append(" -> ".join(route["versions"]))
        note = f"  \n{route['note']}" if route.get("note") else ""  # two spaces + newline = markdown line break
        st.info("  |  ".join(bits) + note)

    if route.get("intent") == "unclear":
        st.warning(res["answer"])
        st.stop()
    if res["insufficient_context"]:
        st.warning("The documents don't seem to contain this answer.")
    st.markdown(res["answer"])

    g = res.get("grounding") or {}
    status = g.get("status")
    if status == "grounded":
        extra = f" ({g['repaired_quotes']} quote(s) corrected to the exact source text)" if g.get("repaired_quotes") else ""
        st.success("Checked against the sources: every quote and number was found." + extra)
        for issue in g.get("issues", []):  # e.g. a comparison summary replaced by the exact list
            st.caption(f"Note: {issue}")
    elif status in ("partial", "ungrounded"):
        head = (
            "Parts of this answer could not be verified against the sources."
            if status == "partial"
            else "This answer could not be verified against the sources. Treat it with caution."
        )
        retried = " (after one retry)" if g.get("retried") else ""
        st.error(head + retried + "\n\n" + "\n".join(f"- {i}" for i in g.get("issues", [])))

    st.subheader("Sources")
    if not res["citations"]:
        st.info("No citations returned.")
    for c in res["citations"]:
        with st.expander(f"[{c['source']}] {c['doc_title']} - {c['version']} (effective {c['effective_date']}) - section {c['section']}"):
            st.markdown(f"> {c['quote']}")
            st.caption(c["chunk_id"])
    if res["unknown_citations"]:
        st.error(f"Model cited sources that were not retrieved: {res['unknown_citations']}")

    if res.get("trace_id"):
        st.caption(f"[Langfuse trace]({LANGFUSE_HOST}/project/vrag/traces/{res['trace_id']})")
