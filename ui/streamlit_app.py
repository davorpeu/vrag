"""versioned-rag UI.

Left: the document library. Picking a document there is also what questions are about.
Bottom of the left side: add a document from a GitHub link (one field, one button).
Main: the conversation. Each answer shows which version(s) it came from on a version timeline.
"""

import html
import os

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
ALL = "__all__"
BIG_VERSIONS, BIG_KB = 30, 100  # above this, adding a file asks for confirmation

st.set_page_config(page_title="versioned-rag", page_icon=":material/history_edu:", layout="centered")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:ital,wght@0,400;1,400&display=swap');
:root { --ink:#1C2433; --muted:#5B6577; --rule:#D8DDE4; --now:#2B4C7E; --then:#B7791F; --ok:#2E6B47; }
h1 { font-weight:600; letter-spacing:-0.01em; font-size:1.6rem !important; }
.vr-context { color:var(--muted); font-size:0.9rem; margin:0 0 0.35rem 0; }
.vr-context b { color:var(--ink); font-weight:600; }
.vr-timeline { display:flex; align-items:center; flex-wrap:wrap; gap:0; margin:0.2rem 0 0.9rem 0; }
.vr-v { font-size:0.78rem; padding:0.1rem 0.45rem; border:1px solid var(--rule); border-radius:999px;
        color:var(--muted); background:#fff; white-space:nowrap; }
.vr-v.now { background:var(--now); border-color:var(--now); color:#fff; }
.vr-v.then { background:var(--then); border-color:var(--then); color:#fff; }
.vr-line { width:1.1rem; height:1px; background:var(--rule); }
.vr-gap { font-size:0.75rem; color:var(--muted); padding:0 0.3rem; }
.vr-check { color:var(--ok); font-size:0.85rem; margin-top:0.4rem; }
.vr-src { margin:0 0 0.9rem 0; }
.vr-src-head { font-size:0.8rem; color:var(--muted); margin-bottom:0.2rem; }
.vr-src-head b { color:var(--ink); font-weight:600; }
.vr-quote { font-family:'IBM Plex Serif', Georgia, serif; font-size:0.98rem; line-height:1.6;
            border-left:3px solid var(--rule); padding:0.1rem 0 0.1rem 0.8rem; color:var(--ink); }
.vr-src.now .vr-quote { border-left-color:var(--now); }
.vr-src.then .vr-quote { border-left-color:var(--then); }
[data-testid="stChatMessageAvatarCustom"], [data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] { background:#fff; color:var(--now); border:1px solid var(--rule); }
.vr-empty { color:var(--muted); line-height:1.7; margin-bottom:0.3rem; }
.vr-examples { list-style:none; padding:0; margin:0; }
.vr-examples li { color:var(--ink); padding:0.15rem 0; }
/* document list: a navigation list, not a row of radio buttons */
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > div { width:100%; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] {
  width:100%; padding:0.45rem 0.6rem; margin:0 0 2px 0; border-radius:6px;
  border-left:3px solid transparent; cursor:pointer; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child:not([data-testid]) { display:none; }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover { background:#F3F5F8; }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] { background:#fff; border-left-color:var(--now); }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:focus-visible) { outline:2px solid var(--now); outline-offset:1px; }
</style>
""",
    unsafe_allow_html=True,
)


# -- API ------------------------------------------------------------------------


def _detail(r: httpx.Response) -> str:
    try:
        return r.json().get("detail") or r.text
    except ValueError:
        return r.text


def _post(path: str, body: dict, timeout: float) -> tuple[dict | list | None, str | None]:
    """(json, None) on success, (None, message) on failure."""
    try:
        r = httpx.post(f"{API_URL}{path}", json=body, timeout=timeout)
    except httpx.HTTPError as e:
        return None, f"The app's API isn't reachable ({e}). Start it and try again."
    if r.status_code >= 400:
        return None, _detail(r)
    return r.json(), None


def _documents() -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/documents", timeout=10)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return []


docs = {d["doc_id"]: d for d in _documents()}
if "pending_focus" in st.session_state:
    st.session_state["doc_focus"] = st.session_state.pop("pending_focus")
if st.session_state.get("doc_focus", ALL) not in (ALL, *docs):
    st.session_state["doc_focus"] = ALL


# -- rendering an answer ----------------------------------------------------------


def _esc(s: str) -> str:
    return html.escape(str(s))


def timeline(doc: dict, now: list[str], then: list[str] = ()) -> str:
    """The document's versions in order; the ones the answer used are filled in.
    Long histories are shortened around the used versions."""
    labels = [v["version"] for v in doc["versions"]]
    keep = set()
    for i, label in enumerate(labels):
        if label in now or label in then:
            keep.update({i - 1, i, i + 1})
    keep.update({0, len(labels) - 1})
    parts, last = [], -1
    for i, label in enumerate(labels):
        if i not in keep:
            continue
        if last >= 0:
            skipped = i - last - 1
            parts.append('<span class="vr-line"></span>')
            if skipped:
                parts.append(f'<span class="vr-gap">{skipped} more</span><span class="vr-line"></span>')
        cls = "now" if label in now else "then" if label in then else ""
        parts.append(f'<span class="vr-v {cls}">{_esc(label)}</span>')
        last = i
    return f'<div class="vr-timeline">{"".join(parts)}</div>'


def context_line(res: dict) -> str:
    route = res.get("route") or {}
    doc = docs.get(route.get("doc_id") or "")
    title = _esc(doc["doc_title"] if doc else route.get("doc_id") or "")
    vs = route.get("versions") or []
    intent = route.get("intent")
    if intent == "compare" and len(vs) == 2:
        text = f"Comparing <b>{title}</b> {_esc(vs[0])} and {_esc(vs[1])}"
    elif intent == "version" and vs:
        text = f"From <b>{title}</b> {_esc(vs[0])}"
    elif intent == "latest" and doc:
        text = f"From the latest version of <b>{title}</b>"
    elif intent == "latest":
        text = "From the latest version of each document"
    else:
        return ""
    note = route.get("note") or ""
    if note and intent in ("version", "compare") and not note.startswith("Compared the latest"):
        text += f". {_esc(note)}"
    return f'<p class="vr-context">{text}</p>'


def render_answer(res: dict) -> None:
    route = res.get("route") or {}
    intent = route.get("intent")

    if intent == "unclear":
        if route.get("doc_id"):
            st.markdown(route.get("note") or res["answer"])
        else:
            st.markdown("Which document do you mean? Pick one on the left, or name it in your question.")
        return

    st.markdown(context_line(res), unsafe_allow_html=True)
    cites = res.get("citations") or []
    vs = route.get("versions") or []
    then = vs[:1] if intent == "compare" and len(vs) == 2 else []
    now = vs[-1:] if vs else []
    shown_docs = [route["doc_id"]] if route.get("doc_id") in docs else []
    if not shown_docs:  # all-documents question: one timeline per cited document
        shown_docs = list(dict.fromkeys(c["doc_id"] for c in cites if c["doc_id"] in docs))
    for doc_id in shown_docs[:3]:
        used = now or [c["version"] for c in cites if c["doc_id"] == doc_id]
        st.markdown(timeline(docs[doc_id], used, then), unsafe_allow_html=True)

    if res.get("insufficient_context") and not cites:
        st.markdown("The documents don't cover this.")
    else:
        st.markdown(res["answer"])

    g = res.get("grounding") or {}
    status = g.get("status")
    if status == "grounded":
        fixed = g.get("repaired_quotes") or 0
        extra = f" ({fixed} quote{'s' if fixed != 1 else ''} corrected to the exact wording)" if fixed else ""
        st.markdown(
            f'<p class="vr-check">✓ Every quote and number was found in the sources{extra}.</p>',
            unsafe_allow_html=True,
        )
    elif status in ("partial", "ungrounded"):
        head = (
            "Parts of this answer could not be found in the sources:"
            if status == "partial"
            else "This answer could not be checked against the sources. Read the sources before relying on it:"
        )
        st.error(head + "\n\n" + "\n".join(f"- {i}" for i in g.get("issues", [])))

    if cites:
        with st.expander(f"Sources ({len(cites)})"):
            for c in cites:
                cls = "then" if c["version"] in then else "now" if c["version"] in now else ""
                st.markdown(
                    f'<div class="vr-src {cls}"><div class="vr-src-head"><b>{_esc(c["doc_title"])} {_esc(c["version"])}</b>, '
                    f'section {_esc(c["section"])}, effective {_esc(c["effective_date"])}</div>'
                    f'<div class="vr-quote">{_esc(c["quote"])}</div></div>',
                    unsafe_allow_html=True,
                )
            if res.get("trace_id"):
                st.caption(f"[How this answer was made (Langfuse)]({LANGFUSE_HOST}/project/vrag/traces/{res['trace_id']})")
    if res.get("unknown_citations"):
        st.error(f"The model cited sources it was not given: {', '.join(res['unknown_citations'])}")


# -- left side: documents and adding one ---------------------------------------------


def _doc_label(doc_id: str) -> str:
    if doc_id == ALL:
        return "All documents"
    d = docs[doc_id]
    n = len(d["versions"])
    count = f"{n} version{'s' if n != 1 else ''}"
    if "/" in doc_id:  # from git: the file path says more than its first heading ("[Unreleased]")
        return f"{doc_id}  \n:gray[{count}, from git]"
    return f"{d['doc_title']}  \n:gray[{count}]"


def _start_load(url: str, file: str) -> None:
    """Pick tags if the file has at least two tagged versions, else commits; confirm big loads."""
    rows, err = _post("/sources/git/preview", {"url": url, "files": [file], "by": "tags"}, 120)
    by = "tags"
    if not err and rows and rows[0]["versions"] < 2:
        rows, err = _post("/sources/git/preview", {"url": url, "files": [file], "by": "commits"}, 120)
        by = "commits"
    if err:
        st.session_state["add_msg"] = ("error", err)
        return
    p = rows[0]
    if p["versions"] == 0:
        st.session_state["add_msg"] = ("error", f"{file} has no versions in this repository.")
        return
    plan = {"url": url, "file": file, "by": by, "versions": p["versions"], "size_kb": p["size_kb"]}
    if p["versions"] > BIG_VERSIONS or p["size_kb"] > BIG_KB:
        st.session_state["add_confirm"] = plan
        return
    _run_load(plan)


def _run_load(plan: dict) -> None:
    job, err = _post("/sources/git/sync", {"url": plan["url"], "files": [plan["file"]], "by": plan["by"]}, 600)
    if err:
        st.session_state["add_msg"] = ("error", err)
        return
    st.session_state["add_job"] = {**plan, "id": job["id"]}
    for k in ("add_confirm", "add_pick", "add_msg"):
        st.session_state.pop(k, None)


def _add_section() -> None:
    busy = "add_job" in st.session_state
    with st.expander("Add from GitHub", expanded=busy or any(
        k in st.session_state for k in ("add_pick", "add_confirm", "add_msg")
    )):
        link = st.text_input(
            "Link to a file or repository", key="add_link", label_visibility="collapsed",
            placeholder="Paste a GitHub link to a file",
        )
        pick = st.session_state.get("add_pick")
        file = None
        if pick:
            file = st.selectbox("Which file?", pick["files"], index=None, placeholder="Choose a file")
        confirm = st.session_state.get("add_confirm")
        if confirm:
            st.warning(
                f"{confirm['file']} has {confirm['versions']} versions ({confirm['size_kb']} KB). "
                "Adding it can take an hour or more and can't be stopped from here."
            )
            c1, c2 = st.columns(2)
            if c1.button("Add anyway", type="primary"):
                _run_load(confirm)
                st.rerun()
            if c2.button("Cancel"):
                st.session_state.pop("add_confirm", None)
                st.rerun()
        elif st.button("Add", type="primary", disabled=busy or not link or (pick is not None and not file)):
            st.session_state.pop("add_msg", None)
            if pick and file and pick["url"] == link:
                st.session_state.pop("add_pick", None)
                _start_load(link, file)
            else:
                st.session_state.pop("add_pick", None)
                with st.spinner("Downloading the repository..."):
                    info, err = _post("/sources/git/inspect", {"url": link}, 600)
                if err:
                    st.session_state["add_msg"] = ("error", err)
                elif info["file"]:
                    _start_load(link, info["file"])
                elif info["candidates"]:
                    st.session_state["add_pick"] = {"url": link, "files": info["candidates"]}
                else:
                    st.session_state["add_msg"] = ("error", "This repository has no Markdown, text, PDF or Word files.")
            st.rerun()
        if not pick and not confirm and not busy:
            st.caption("Open the file on GitHub and copy the address. Each release becomes a version.")
        _add_status()


@st.fragment(run_every=2)
def _add_status() -> None:
    job_info = st.session_state.get("add_job")
    if not job_info:
        msg = st.session_state.get("add_msg")
        if msg:
            (st.error if msg[0] == "error" else st.success)(msg[1])
        return
    try:
        job = httpx.get(f"{API_URL}/jobs/{job_info['id']}", timeout=10).json()
    except (httpx.HTTPError, ValueError) as e:
        st.error(f"Lost track of the upload: {e}")
        return
    total = max(job_info["versions"], 1)
    if job["status"] == "running":
        st.progress(min(job["done_count"] / total, 1.0), text=f"Adding {job_info['file']}: {job['done_count']} of {total} versions")
        return
    st.session_state.pop("add_job", None)
    if job["status"] == "error":
        st.session_state["add_msg"] = ("error", f"Adding {job_info['file']} failed: {job['error']}")
    else:
        loaded = [label.split("@")[0] for label in job["indexed"] + job["renumbered"] + job["skipped"]]
        new = len(job["indexed"])
        text = f"Added {job_info['file']}: {new} new version{'s' if new != 1 else ''}"
        text += f", {len(job['skipped'])} already here." if job["skipped"] else "."
        if job["empty"]:
            text += f" No text found in {len(job['empty'])}."
        st.session_state["add_msg"] = ("ok", text)
        if loaded:
            st.session_state["pending_focus"] = loaded[0]
    st.rerun()


with st.sidebar:
    st.subheader("Documents")
    if docs:
        focus = st.radio("Ask about", [ALL, *docs], format_func=_doc_label, key="doc_focus", label_visibility="collapsed")
    else:
        focus = ALL
        st.caption("No documents yet, or the API isn't running.")
    _add_section()


# -- main: the conversation -----------------------------------------------------------

st.title("versioned-rag")
history: list[dict] = st.session_state.setdefault("history", [])
target = docs[focus]["doc_title"] if focus != ALL else None
question = st.chat_input(f"Ask about {target}" if target else "Ask a question")  # pinned to the bottom
USER, BOT = ":material/person:", ":material/description:"

if not history and not question:
    st.markdown(
        '<p class="vr-empty">Ask about the current rules, about an older version, or about what changed. '
        "Every answer says which version it comes from. For example:</p>"
        '<ul class="vr-examples"><li>How many days do customers have to request a refund?</li>'
        "<li>What did version 1 of the leave policy say about carry-over?</li>"
        "<li>What changed in the travel policy v2.0?</li></ul>",
        unsafe_allow_html=True,
    )

for turn in history:
    with st.chat_message("user", avatar=USER):
        st.markdown(turn["question"])
    with st.chat_message("assistant", avatar=BOT):
        if turn.get("error"):
            st.error(turn["error"])
        else:
            render_answer(turn["response"])

if question:
    with st.chat_message("user", avatar=USER):
        st.markdown(question)
    with st.chat_message("assistant", avatar=BOT):
        with st.spinner("Reading the documents..."):
            res, err = _post("/ask", {"question": question, "doc_id": None if focus == ALL else focus}, 300)
        if err:
            st.error(err)
            history.append({"question": question, "error": err})
        else:
            render_answer(res)
            history.append({"question": question, "response": res})
