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

def _api_error(r: httpx.Response) -> str:
    try:
        return r.json().get("detail") or r.text
    except ValueError:
        return r.text


with st.sidebar:
    st.header("Add a git repository")
    st.caption(
        "Paste a GitHub link to a repo or to one file (open the file on GitHub and copy the address). "
        "Each release tag becomes a version you can ask about."
    )
    link = st.text_input("Repository or file link", placeholder="https://github.com/semver/semver/blob/master/semver.md")
    if st.button("Check link") and link:
        st.session_state.pop("git_info", None)
        st.session_state.pop("git_result", None)
        with st.spinner("Downloading the repository (first time can take a while)..."):
            try:
                r = httpx.post(f"{API_URL}/sources/git/inspect", json={"url": link}, timeout=600)
            except httpx.HTTPError as e:
                r = None
                st.error(f"API error: {e}")
        if r is not None and r.status_code != 200:
            st.error(_api_error(r))
        elif r is not None:
            st.session_state["git_info"] = {**r.json(), "url": link}

    info = st.session_state.get("git_info")
    if info:
        st.success(f"{info['name']}: {info['action']}, {info['tags']} tag(s).")
        options = info["candidates"]
        if info["file"] and info["file"] not in options:
            options = [info["file"], *options]
        if not options:
            st.warning("No Markdown, text, PDF or Word files in this repository.")
        files = st.multiselect(
            "Files to follow", options, default=[info["file"]] if info["file"] else [],
            help="Each chosen file gets its own document with one version per tag (or per commit).",
        )
        by = st.radio(
            "Versions from", ["tags", "commits"], horizontal=True,
            help="tags: one version per release tag. commits: one per commit that changed the file "
            "(use this when the repo has no tags; can be many versions).",
        )
        if by == "tags" and info["tags"] == 0:
            st.warning("This repository has no tags. Choose 'commits'.")
        ok_to_load = bool(files)
        if files:
            key = (info["url"], tuple(files), by)
            if st.session_state.get("git_preview_key") != key:
                try:
                    r = httpx.post(
                        f"{API_URL}/sources/git/preview", json={"url": info["url"], "files": files, "by": by}, timeout=120
                    )
                    preview = r.json() if r.status_code == 200 else []
                except (httpx.HTTPError, ValueError):
                    preview = []
                st.session_state["git_preview"], st.session_state["git_preview_key"] = preview, key
            preview = st.session_state["git_preview"]
            for p in preview:
                st.caption(f"{p['file']}: {p['versions']} version(s), newest {p['size_kb']} KB")
            if any(p["versions"] == 0 for p in preview):
                st.warning("A chosen file has no versions this way (try the other option).")
            total = sum(p["versions"] for p in preview)
            big = total > 30 or any(p["size_kb"] > 100 for p in preview)
            if big:
                minutes = max(1, round(total * max(1, max(p["size_kb"] for p in preview) / 10) * 2 / 60))
                st.warning(
                    f"This is a big load: {total} versions. With the local model it may take around "
                    f"{minutes} minutes or more, and it can't be stopped from here."
                )
                ok_to_load = st.checkbox("Load it anyway")
        if st.button("Load versions", type="primary", disabled=not ok_to_load):
            st.session_state.pop("git_result", None)
            try:
                r = httpx.post(
                    f"{API_URL}/sources/git/sync", json={"url": info["url"], "files": files, "by": by}, timeout=600
                )
            except httpx.HTTPError as e:
                r = None
                st.error(f"API error: {e}")
            if r is not None and r.status_code == 202:
                st.session_state["git_job"] = r.json()["id"]
            elif r is not None:
                st.error(_api_error(r))

    @st.fragment(run_every=2)
    def job_status() -> None:
        job_id = st.session_state.get("git_job")
        if not job_id:
            done = st.session_state.get("git_result")
            if done:
                (st.error if done[0] == "error" else st.success)(done[1])
            return
        try:
            job = httpx.get(f"{API_URL}/jobs/{job_id}", timeout=10).json()
        except httpx.HTTPError as e:
            st.error(f"API error: {e}")
            return
        if job["status"] == "running":
            now = f" Now: {job['current']}." if job.get("current") else ""
            st.info(f"Loading... {job['done_count']} version(s) done.{now} About 10-60 s per new version.")
            return
        st.session_state.pop("git_job", None)
        if job["status"] == "error":
            done = ("error", f"Loading failed: {job['error']}")
        else:
            lines = [f"Loaded {len(job['indexed'])} new version(s), {len(job['skipped'])} already up to date."]
            if job.get("removed"):
                lines.append(f"Replaced {len(job['removed'])} version(s) from an earlier load.")
            if job["renumbered"]:
                lines.append("Re-ordered: " + ", ".join(job["renumbered"]))
            if job["indexed"]:
                lines.append("New: " + ", ".join(job["indexed"]))
            if job["empty"]:
                lines.append("No text found in: " + ", ".join(job["empty"]))
            done = ("ok", "  \n".join(lines) + "  \nIt is now selected as the document to ask about.")
            loaded = [label.split("@")[0] for label in job["indexed"] + job["renumbered"] + job["skipped"]]
            if loaded:
                st.session_state["pending_focus"] = loaded[0]
        st.session_state["git_result"] = done
        if done[0] == "ok" and "pending_focus" in st.session_state:
            st.rerun()  # whole page, so the document picker switches to the loaded document
        (st.error if done[0] == "error" else st.success)(done[1])

    job_status()


def _documents() -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/documents", timeout=10)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return []


ALL = "__all__"
docs = {d["doc_id"]: d for d in _documents()}
if "pending_focus" in st.session_state:
    st.session_state["doc_focus"] = st.session_state.pop("pending_focus")
if st.session_state.get("doc_focus", ALL) not in (ALL, *docs):
    st.session_state["doc_focus"] = ALL


def _doc_label(doc_id: str) -> str:
    if doc_id == ALL:
        return "All documents (name the document in your question)"
    d = docs[doc_id]
    return f"{d['doc_title']} ({doc_id}) - {len(d['versions'])} versions, latest {d['versions'][-1]['version']}"


focus = st.selectbox("Document", [ALL, *docs], format_func=_doc_label, key="doc_focus")
question = st.text_input("Question", placeholder="How many days do customers have to request a refund?")

if st.button("Ask", type="primary") and question:
    with st.spinner("Searching and answering (local model, can take ~30s)..."):
        try:
            body = {"question": question, "doc_id": None if focus == ALL else focus}
            r = httpx.post(f"{API_URL}/ask", json=body, timeout=300)
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
