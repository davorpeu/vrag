"""Repo activity: time windows, topic words, reading a real (temporary) git repo, counting, sync and ask.
Elasticsearch and the LLM are faked; git is real."""

import os
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from vrag import activity
from vrag.activity import Window, aggregate, parse_window, read_commits, topic_of

TODAY = date(2026, 11, 20)


# -- time window ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "q, since, until, last_n",
    [
        ("what was done recently", date(2026, 11, 6), TODAY, None),
        ("what happened?", date(2026, 11, 6), TODAY, None),
        ("what changed last week", date(2026, 11, 13), TODAY, None),
        ("in the last 3 weeks", date(2026, 10, 30), TODAY, None),
        ("past two months", date(2026, 9, 21), TODAY, None),
        ("since March", date(2026, 3, 1), TODAY, None),
        ("since December", date(2025, 12, 1), TODAY, None),  # a later month than today = last year
        ("since March 2025", date(2025, 3, 1), TODAY, None),
        ("since May 3", date(2026, 5, 3), TODAY, None),
        ("what happened in March", date(2026, 3, 1), date(2026, 3, 31), None),
        ("in October 2025", date(2025, 10, 1), date(2025, 10, 31), None),
        ("since 2026-03-05", date(2026, 3, 5), TODAY, None),
        ("yesterday", date(2026, 11, 19), date(2026, 11, 19), None),
        ("this week", date(2026, 11, 16), TODAY, None),  # 2026-11-20 is a Friday
        ("this month", date(2026, 11, 1), TODAY, None),
        ("last 20 commits", None, None, 20),
    ],
)
def test_parse_window(q, since, until, last_n):
    w = parse_window(q, TODAY)
    assert (w.since, w.until, w.last_n) == (since, until, last_n)


def test_default_window_is_marked_as_not_explicit():
    assert not parse_window("what happened", TODAY).explicit
    assert parse_window("last week", TODAY).explicit


def test_topic_words():
    assert topic_of("What was done recently?", "o/r") == ""
    assert topic_of("describe what's been done recently and in what files", "o/r") == ""
    assert topic_of("what changed in the auth module last month", "o/r") == "auth module"
    assert topic_of("Which areas were most active in the last 2 weeks, and what was the work about?", "o/r") == ""
    assert topic_of("what happened in fastapi since March", "tiangolo/fastapi") == ""  # repo name is not a topic


# -- a real temporary repo ---------------------------------------------------------------


def commit(repo, days_ago, message, files, author="Ann"):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT12:00:00+00:00")
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    git = ["git", "-C", str(repo), "-c", f"user.name={author}", "-c", "user.email=a@a", "-c", "core.autocrlf=false"]
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True, capture_output=True, env=env)


@pytest.fixture
def upstream(tmp_path):
    up = tmp_path / "up"
    up.mkdir()
    subprocess.run(["git", "-C", str(up), "init", "-q", "-b", "main"], check=True, capture_output=True)
    commit(up, 200, "Initial import", {"README.md": "a\n", "src/old.py": "x\n"})
    commit(up, 40, "Add parser", {"src/parser.py": "1\n2\n3\n", "tests/test_parser.py": "t\n"})
    commit(up, 10, "Fix parser edge case", {"src/parser.py": "1\n2\n3\n4\n", "tests/test_parser.py": "t\nu\n"}, author="Bob")
    commit(up, 3, "Docs: explain the parser", {"docs/guide.md": "g\n", "src/parser.py": "1\n2\n3\n4\n5\n"})
    commit(up, 1, "Bump version\n\nReleases 1.2", {"pyproject.toml": "v\n"})
    return up


@pytest.fixture
def clone_root(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "CLONE_ROOT", tmp_path / "clones")
    return tmp_path / "clones"


def load(upstream, days=90):
    repo = activity.repo_from_link(upstream.as_uri())  # file:// so git honours --shallow-since
    action = activity.fetch_repo(repo, date.today() - timedelta(days=days))
    return repo, action, read_commits(repo.path, date.today() - timedelta(days=days))


def test_shallow_clone_reads_window_commits_with_real_numstat(upstream, clone_root):
    repo, action, commits = load(upstream, days=90)
    assert action == "cloned"
    assert [c.message.split("\n")[0] for c in commits] == [
        "Bump version", "Docs: explain the parser", "Fix parser edge case", "Add parser",
    ]  # newest first; the 200-day-old import is outside
    by = {c.message.split("\n")[0]: c for c in commits}
    add = by["Add parser"]  # the oldest commit of the window: a real diff, not "whole tree added"
    assert {f.path: (f.additions, f.deletions) for f in add.files} == {"src/parser.py": (3, 0), "tests/test_parser.py": (1, 0)}
    fix = by["Fix parser edge case"]
    assert fix.author == "Bob" and {f.path: f.additions for f in fix.files} == {"src/parser.py": 1, "tests/test_parser.py": 1}
    assert "Releases 1.2" in by["Bump version"].message and by["Bump version"].files_total == 1
    assert not (repo.path / "src").exists()  # no checkout


def test_second_fetch_updates_and_finds_new_commits(upstream, clone_root):
    repo, _, before = load(upstream)
    commit(upstream, 0, "Add cache", {"src/cache.py": "c\n"})
    action = activity.fetch_repo(repo, date.today() - timedelta(days=90))
    after = read_commits(repo.path, date.today() - timedelta(days=90))
    assert action == "updated" and len(after) == len(before) + 1 and after[0].message == "Add cache"


def test_fetch_refuses_a_folder_cloned_from_elsewhere(upstream, tmp_path, clone_root):
    repo, *_ = load(upstream)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True, capture_output=True)
    bad = activity.Repo(repo.key, other.as_uri(), None)
    with pytest.raises(activity.ActivityError, match="clone of"):
        activity.fetch_repo(bad, date.today())


# -- counting ----------------------------------------------------------------------------


def test_aggregate_counts_files_and_directories_in_code(upstream, clone_root):
    _, _, commits = load(upstream)
    files, dirs = aggregate(commits)
    assert [(f.path, f.commits, f.additions, f.deletions) for f in files[:2]] == [
        ("src/parser.py", 3, 5, 0), ("tests/test_parser.py", 2, 2, 0),
    ]  # 3 + 1 + 1 added lines; the file got longer each time
    d = {x.path: x for x in dirs}
    assert d["src/"].commits == 3 and d["tests/"].commits == 2 and d["(top level)"].commits == 1


def test_a_commit_counts_once_per_directory():
    c = activity.Commit(
        "a" * 40, "2026-11-01T00:00:00+00:00", "x", "m",
        [activity.FileStat("src/a.py", 1, 0), activity.FileStat("src/b.py", 2, 1)], 2,
    )
    files, dirs = aggregate([c])
    assert len(files) == 2 and [(d.path, d.commits, d.additions, d.deletions) for d in dirs] == [("src/", 1, 3, 1)]


# -- fake Elasticsearch ------------------------------------------------------------------


class FakeIndices:
    def __init__(self, es):
        self.es = es

    def exists(self, index):
        return self.es.created

    def create(self, index, mappings):
        self.es.created = True


class FakeEs:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.created = False
        self.indices = FakeIndices(self)

    def _match(self, doc_id, src, clause):
        for f in clause["bool"]["filter"] if "bool" in clause else [clause]:
            if "term" in f:
                ((k, v),) = f["term"].items()
                if src.get(k) != v:
                    return False
            elif "ids" in f:
                if doc_id not in f["ids"]["values"]:
                    return False
            elif "range" in f:
                rng = f["range"]["date"]
                day = src["date"][:10]
                if "gte" in rng and day < rng["gte"][:10] or "lte" in rng and day > rng["lte"][:10]:
                    return False
        return True

    def search(self, index, size=10, query=None, sort=None, knn=None, **kw):
        flt = (query or {"bool": {"filter": knn["filter"]}}) if not knn else {"bool": {"filter": knn["filter"]}}
        hits = [{"_id": i, "_source": s} for i, s in self.docs.items() if self._match(i, s, flt)]
        if sort:
            hits.sort(key=lambda h: h["_source"][list(sort[0])[0]], reverse=True)
        if knn:  # "semantic": commits whose message shares a word with the query, best first
            self.knn_calls = getattr(self, "knn_calls", 0) + 1
            words = set(knn["query_vector"])
            hits = [h for h in hits if words & set(h["_source"]["message"].lower().split())]
        return {"hits": {"total": {"value": len(hits)}, "hits": hits[:size]}}

    def count(self, index, query):
        return {"count": len([1 for i, s in self.docs.items() if self._match(i, s, query)])}

    def get(self, index, id):
        if id not in self.docs:
            raise KeyError(id)
        return {"_source": self.docs[id]}

    def index(self, index, id, document, refresh=None):
        self.docs[id] = document


class FakeEmbedder:
    def __init__(self):
        self.texts = []

    def embed_documents(self, texts):
        self.texts += texts
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return text.lower().replace("search_query: ", "").split()  # the "vector" is the query's words


@pytest.fixture
def fake(monkeypatch, clone_root):
    es, emb = FakeEs(), FakeEmbedder()
    monkeypatch.setattr(activity.store, "es", lambda: es)
    monkeypatch.setattr(activity.store, "embedder", lambda: emb)
    monkeypatch.setattr(
        activity.helpers, "bulk",
        lambda client, actions, refresh=None: [es.docs.__setitem__(a["_id"], a["_source"]) for a in actions],
    )
    es.emb = emb
    return es


def test_sync_indexes_commits_and_is_idempotent_by_sha(upstream, fake):
    r1 = activity.sync_repo(upstream.as_uri(), days=90)
    assert (r1.action, r1.found, r1.indexed, r1.skipped) == ("cloned", 4, 4, 0)
    assert len(fake.emb.texts) == 4 and "pyproject.toml" in fake.emb.texts[0] and "Releases 1.2" in fake.emb.texts[0]  # message + file paths embedded
    r2 = activity.sync_repo(upstream.as_uri(), days=90)
    assert (r2.action, r2.indexed, r2.skipped) == ("updated", 0, 4)
    assert len(fake.emb.texts) == 4  # nothing embedded twice
    commit(upstream, 0, "Add cache", {"src/cache.py": "c\n"})
    r3 = activity.sync_repo(upstream.as_uri(), days=90)
    assert (r3.indexed, r3.skipped) == (1, 4)
    repos = activity.list_repos()
    assert len(repos) == 1 and repos[0].commits == 5 and repos[0].since == (date.today() - timedelta(days=90)).isoformat()


def test_a_shorter_sync_does_not_shrink_the_loaded_period(upstream, fake):
    activity.sync_repo(upstream.as_uri(), days=90)
    activity.sync_repo(upstream.as_uri(), days=30)
    assert activity.list_repos()[0].since == (date.today() - timedelta(days=90)).isoformat()


def test_sync_reports_progress_phases(upstream, fake):
    seen = []
    activity.sync_repo(upstream.as_uri(), days=90, on_progress=lambda *a: seen.append(a))
    assert [p[0] for p in seen][:2] == ["downloading", "reading"] and seen[-1] == ("embedding", 4, 4)


def test_sync_bad_link_is_an_activity_error(fake, tmp_path):
    with pytest.raises(activity.ActivityError, match="clone"):
        activity.sync_repo((tmp_path / "missing").as_uri())


def test_github_links_give_owner_name_key_and_commit_urls():
    r = activity.repo_from_link("https://github.com/tiangolo/fastapi/tree/master")
    assert r.key == "tiangolo/fastapi" and r.clone_url == "https://github.com/tiangolo/fastapi.git"
    assert r.commit_url("abc") == "https://github.com/tiangolo/fastapi/commit/abc"
    assert activity.repo_from_link("/some/where/proj.git").commit_url("abc") is None


# -- asking ------------------------------------------------------------------------------


class Summary:
    """Stands in for the LLM: cites whatever sha the test says, plus one made-up sha."""

    def __init__(self, shas):
        self.shas = shas
        self.prompt = None

    def __call__(self, vars_):
        self.prompt = vars_
        return activity.ThemeSummary(
            summary="Work on the parser.",
            themes=[
                activity.Theme(title="Parser", description="Parser was added and fixed.", commits=self.shas),
                activity.Theme(title="Invented", description="Not real.", commits=["deadbee"]),
            ],
        )


@pytest.fixture
def loaded(upstream, fake, monkeypatch):
    activity.sync_repo(upstream.as_uri(), days=90)
    key = activity.list_repos()[0].repo
    return key


def test_ask_counts_files_in_code_and_cites_real_shas(loaded, upstream, monkeypatch):
    llm = Summary([])
    monkeypatch.setattr(activity, "_llm_summary", llm)
    shas = {c.message.split("\n")[0]: c.sha for c in read_commits(activity.repo_from_link(upstream.as_uri()).path, date.today() - timedelta(days=90))}
    llm.shas = [shas["Add parser"][:7], shas["Fix parser edge case"][:7]]
    res = activity.ask("what was done in the last 60 days", loaded)
    assert res.window.label == "last 60 days" and res.commits_in_window == 4 and res.commits_used == 4
    assert [(f.path, f.commits) for f in res.files[:3]] == [
        ("src/parser.py", 3), ("tests/test_parser.py", 2), ("docs/guide.md", 1)]  # ties: by lines, then path
    assert res.authors == 2 and res.topic == ""
    # the model's made-up sha is dropped, with its theme; the real ones are kept
    assert [c.short for c in res.citations] == llm.shas and "Invented" not in res.answer and "deadbee" not in res.answer
    assert res.citations[0].url is None  # a local repo has no web link
    assert "Most changed files" in res.answer and "src/parser.py (3)" in res.answer
    assert "Fix parser edge case" in llm.prompt["commits"] and "Initial import" not in llm.prompt["commits"]
    assert "parser.py" not in llm.prompt["commits"]  # file names are not given to the model


def test_clean_text_removes_sha_lists_the_model_adds():
    assert activity.clean_text("MLX work. Commits: 0b243a1, 73ee71d, b8db8d5 (0b243a1, 73ee71d)") == "MLX work."
    assert activity.clean_text("Fixed bug abc1234 in parser.") == "Fixed bug in parser."
    assert activity.clean_text("Commits: 1f23c72, f1ee567") == ""
    assert activity.clean_text("Added a face and a decade of cache.") == "Added a face and a decade of cache."  # words stay


def test_summary_that_says_nothing_happened_is_replaced_when_themes_exist(loaded, monkeypatch):
    class Contradicting(Summary):
        def __call__(self, vars_):
            r = super().__call__(vars_)
            r.summary = "No documentation changes were made last week."
            r.themes[0].description = "Parser work. Commits: " + ", ".join(self.shas)
            return r

    llm = Contradicting([])
    monkeypatch.setattr(activity, "_llm_summary", llm)
    llm.shas = [x["sha"][:7] for x in [d for d in activity.store.es().docs.values() if d["kind"] == "commit"][:2]]
    res = activity.ask("what changed in the last 30 days", loaded)
    assert "No documentation" not in res.answer and res.answer.startswith("Work in last 30 days:")
    theme_line = [ln for ln in res.answer.splitlines() if ln.startswith("- **Parser**")][0]
    assert "Commits:" not in theme_line and theme_line.count(llm.shas[0]) == 1  # sha list stripped, citation kept


def test_at_most_five_commits_are_cited_per_theme(loaded, monkeypatch):
    shas = [d["sha"][:7] for d in activity.store.es().docs.values() if d["kind"] == "commit"]
    monkeypatch.setattr(activity, "MAX_CITED_PER_THEME", 2)
    monkeypatch.setattr(activity, "_llm_summary", Summary(shas))
    res = activity.ask("what changed in the last 60 days", loaded)
    assert len(res.citations) == 2


def test_ask_default_window_is_recent_and_can_be_empty(loaded, monkeypatch):
    monkeypatch.setattr(activity, "_llm_summary", Summary([]))
    res = activity.ask("what happened recently?", loaded)  # 14 days: 3 commits (10, 3, 1 days ago)
    assert res.window.label.startswith("last 14 days") and res.commits_used == 3
    res = activity.ask("what happened yesterday?", loaded)
    assert res.commits_used == 1 and res.commits[0].message == "Bump version"
    none = activity.ask("what happened today?", loaded)
    assert none.insufficient_context and none.commits == [] and "No commits" in none.answer


def test_ask_last_n_commits(loaded, monkeypatch):
    monkeypatch.setattr(activity, "_llm_summary", Summary([]))
    res = activity.ask("show the last 2 commits", loaded)
    assert [c.message for c in res.commits] == ["Bump version", "Docs: explain the parser"]


def test_ask_warns_when_the_period_is_older_than_what_is_loaded(loaded, monkeypatch):
    monkeypatch.setattr(activity, "_llm_summary", Summary([]))
    res = activity.ask("what happened since last year", loaded, days=300)
    assert any("Only history since" in n for n in res.notes)


def test_topic_question_narrows_the_commits_by_semantic_search(loaded, monkeypatch):
    monkeypatch.setattr(activity, "_llm_summary", Summary([]))
    res = activity.ask("what changed about the parser in the last 60 days", loaded)
    assert res.topic == "parser" and res.commits_in_window == 3 + 1  # window total = all 4 commits since 60 days
    assert res.commits_used == 3 and {c.message for c in res.commits} == {
        "Add parser", "Fix parser edge case", "Docs: explain the parser"}
    assert any("names a topic" in n for n in res.notes)


def test_model_down_still_returns_the_data(loaded, monkeypatch):
    def down(_):
        raise ConnectionError("ollama is off")

    monkeypatch.setattr(activity, "_llm_summary", down)
    res = activity.ask("what happened in the last 30 days", loaded)
    assert res.files and res.commits and "Bump version" in res.answer
    assert any("not available" in n for n in res.notes)


def test_unknown_repo_is_an_activity_error(loaded):
    with pytest.raises(activity.ActivityError, match="not loaded"):
        activity.ask("what happened", "nobody/nothing")
    assert activity.ask  # the short name of a loaded repo works too


def test_ask_by_short_name_and_by_link(loaded, upstream, monkeypatch):
    monkeypatch.setattr(activity, "_llm_summary", Summary([]))
    assert activity.ask("what happened in the last 30 days", "up").repo == loaded
    assert activity.ask("what happened in the last 30 days", upstream.as_uri()).repo == loaded
