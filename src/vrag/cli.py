from dotenv import load_dotenv

load_dotenv()

import typer  # noqa: E402

from vrag import store  # noqa: E402
from vrag.config import get_settings  # noqa: E402
from vrag.sources.db import DbSource  # noqa: E402
from vrag.sources.folder import FolderSource  # noqa: E402
from vrag.sources.git import GitSource  # noqa: E402
from vrag.sources.remote import ensure_clone, parse_link, suggest_files  # noqa: E402
from vrag.sources.s3 import S3Source  # noqa: E402
from vrag.sync import sync as run_sync  # noqa: E402

app = typer.Typer(no_args_is_help=True)


@app.command()
def init_index(recreate: bool = typer.Option(False, help="Delete and recreate the index")):
    """Create the Elasticsearch index."""
    store.ensure_index(recreate=recreate)
    typer.echo("index ready")


@app.command()
def sync(
    source: str = typer.Argument("folder", help="folder | git | db | s3 (alias: minio)"),
    path: str = typer.Option("data/corpus", help="folder: corpus root"),
    repo: str = typer.Option("", help="git: path to a local clone"),
    url: str = typer.Option("", help="git: repo link to clone/pull (GitHub file links also set --file)"),
    file: list[str] = typer.Option([], help="git: file path inside the repo (repeatable)"),
    by: str = typer.Option("tags", help="git: 'tags' or 'commits'"),
    db_url: str = typer.Option("", help="db: connection URL (default DOCS_DB_URL)"),
    query: str = typer.Option("", help="db: custom SQL returning doc_id, version, filename, content, source_date, ref"),
    bucket: str = typer.Option("", help="s3: bucket (default DOCS_S3_BUCKET)"),
    prefix: str = typer.Option("", help="s3: only keys under this folder; it is left out of the doc ids"),
    layout: str = typer.Option(
        "versions", help="s3: 'versions' (each object version is a document version) or 'folders' (<doc>/v<N>.<ext>)"
    ),
):
    """Index new/changed document versions from a source."""
    if source == "folder":
        src = FolderSource(path)
    elif source == "git":
        if url:
            if repo:
                raise typer.BadParameter("use either --url or --repo, not both")
            try:
                link = parse_link(url)
                clone, how = ensure_clone(link)
            except (ValueError, RuntimeError) as e:
                raise typer.BadParameter(str(e)) from e
            typer.echo(f"{how} {link.clone_url} -> {clone}")
            repo = str(clone)
            if link.file and link.file not in file:
                file = [*file, link.file]
            if not file:
                hint = "\n  ".join(suggest_files(clone)) or "(no .md/.txt/.pdf/.docx files found)"
                typer.echo(f"Which file should be versioned? Add --file <path>. Candidates:\n  {hint}")
                raise typer.Exit(1)
        if not repo or not file:
            raise typer.BadParameter("git needs --url or --repo, and at least one --file")
        src = GitSource(repo, file, by=by)
    elif source == "db":
        url = db_url or get_settings().docs_db_url
        src = DbSource(url, query) if query else DbSource(url)
    elif source in ("s3", "minio"):
        s = get_settings()
        try:
            src = S3Source(
                s.docs_s3_url, bucket or s.docs_s3_bucket, prefix=prefix, layout=layout,
                access_key=s.docs_s3_access_key, secret_key=s.docs_s3_secret_key,
            )
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e
    else:
        raise typer.BadParameter("source must be 'folder', 'git', 'db' or 's3' (alias 'minio')")
    r = run_sync(src)
    typer.echo(f"indexed {len(r.indexed)}: {', '.join(r.indexed) or '-'}")
    typer.echo(f"skipped {len(r.skipped)} unchanged")
    if r.renumbered:
        typer.echo(f"renumbered {len(r.renumbered)} (version order changed): {', '.join(r.renumbered)}")
    if r.removed:
        typer.echo(f"removed {len(r.removed)} (no longer in the source): {', '.join(r.removed)}")
    if r.empty:
        typer.echo(f"no text extracted from: {', '.join(r.empty)}")
    deleted = getattr(src, "deleted_docs", [])
    if deleted:
        typer.echo(f"deleted in the bucket (delete marker), not synced: {', '.join(deleted)}")


@app.command()
def ask(question: str):
    """Ask a question from the terminal."""
    from vrag import answer

    try:
        res = answer.ask(question)
    finally:
        answer.flush()
    if res.route:
        r = res.route
        typer.echo(f"[route: {r.intent} {r.doc_id or ''} {' -> '.join(r.versions)}] {r.note}".rstrip())
    typer.echo(res.answer)
    for c in res.citations:
        typer.echo(f"  [{c.source}] {c.doc_title} {c.version} (eff. {c.effective_date}) section {c.section}: \"{c.quote}\"")
    if res.grounding:
        g = res.grounding
        retried = ", retried once" if g.retried else ""
        typer.echo(f"[grounding: {g.status}{retried}] " + "; ".join(g.issues))
    if res.unknown_citations:
        typer.echo(f"  ! cited unknown sources: {res.unknown_citations}")


activity_app = typer.Typer(help="What was done in a repo recently, and in which files (commit history).", no_args_is_help=True)
app.add_typer(activity_app, name="activity")


@activity_app.command("sync")
def activity_sync(
    url: str = typer.Option(..., help="repo link (GitHub or any git URL)"),
    days: int = typer.Option(90, help="load this many days of history"),
    max_commits: int = typer.Option(500, help="read at most this many commits"),
):
    """Load a repo's recent commits (message, files, +/- lines) into the activity index."""
    from vrag import activity

    def progress(phase: str, done: int, total: int) -> None:
        if phase == "embedding":
            typer.echo(f"\rembedding {done}/{total}", nl=False)
        elif done == 0:
            typer.echo(phase + "...")

    try:
        r = activity.sync_repo(url, days=days, max_commits=max_commits, on_progress=progress)
    except activity.ActivityError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(f"\n{r.action} {r.repo}: {r.found} commits since {r.since}, {r.indexed} new, {r.skipped} already indexed")


@activity_app.command("ask")
def activity_ask(
    question: str,
    url: str = typer.Option(..., help="repo link, or the name shown by `vrag activity sync`"),
    days: int = typer.Option(0, help="period in days (default: read it from the question; 'recently' = 14 days)"),
    last_n: int = typer.Option(0, help="the newest N commits instead of a period"),
):
    """Ask what was done in a loaded repo ("what changed last week", "since March", ...)."""
    import sys

    from vrag import activity

    sys.stdout.reconfigure(errors="replace")  # commit messages hold emoji; the Windows console can't print them
    try:
        res = activity.ask(question, url, days=days or None, last_n=last_n or None)
    except activity.ActivityError as e:
        raise typer.BadParameter(str(e)) from e
    w = res.window
    typer.echo(f"[{res.repo}: {w.label}, {res.commits_in_window} commits, {res.authors} authors"
               + (f", topic: {res.topic}" if res.topic else "") + "]")
    typer.echo(res.answer)
    for n in res.notes:
        typer.echo(f"  note: {n}")
    if res.files:
        typer.echo(f"\nFiles changed (top {len(res.files)} of {res.files_total}):  commits  +lines  -lines")
        for f in res.files[:15]:
            typer.echo(f"  {f.path:60.60} {f.commits:7} {f.additions:7} {f.deletions:7}")
    if res.directories:
        typer.echo("\nDirectories:  " + ", ".join(f"{d.path} ({d.commits})" for d in res.directories[:8]))
    if res.citations:
        typer.echo("\nCited commits:")
        for c in res.citations:
            typer.echo(f"  {c.short} {c.message[:80]}  {c.url or ''}".rstrip())


if __name__ == "__main__":
    app()
