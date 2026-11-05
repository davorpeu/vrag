from dotenv import load_dotenv

load_dotenv()

import typer  # noqa: E402

from vrag import store  # noqa: E402
from vrag.config import get_settings  # noqa: E402
from vrag.sources.db import DbSource  # noqa: E402
from vrag.sources.folder import FolderSource  # noqa: E402
from vrag.sources.git import GitSource  # noqa: E402
from vrag.sync import sync as run_sync  # noqa: E402

app = typer.Typer(no_args_is_help=True)


@app.command()
def init_index(recreate: bool = typer.Option(False, help="Delete and recreate the index")):
    """Create the Elasticsearch index."""
    store.ensure_index(recreate=recreate)
    typer.echo("index ready")


@app.command()
def sync(
    source: str = typer.Argument("folder", help="folder | git | db"),
    path: str = typer.Option("data/corpus", help="folder: corpus root"),
    repo: str = typer.Option("", help="git: path to a local clone"),
    file: list[str] = typer.Option([], help="git: file path inside the repo (repeatable)"),
    by: str = typer.Option("tags", help="git: 'tags' or 'commits'"),
    db_url: str = typer.Option("", help="db: connection URL (default DOCS_DB_URL)"),
    query: str = typer.Option("", help="db: custom SQL returning doc_id, version, filename, content, source_date, ref"),
):
    """Index new/changed document versions from a source."""
    if source == "folder":
        src = FolderSource(path)
    elif source == "git":
        if not repo or not file:
            raise typer.BadParameter("git needs --repo and at least one --file")
        src = GitSource(repo, file, by=by)
    elif source == "db":
        url = db_url or get_settings().docs_db_url
        src = DbSource(url, query) if query else DbSource(url)
    else:
        raise typer.BadParameter("source must be 'folder', 'git' or 'db' (minio comes next)")
    r = run_sync(src)
    typer.echo(f"indexed {len(r.indexed)}: {', '.join(r.indexed) or '-'}")
    typer.echo(f"skipped {len(r.skipped)} unchanged")
    if r.renumbered:
        typer.echo(f"renumbered {len(r.renumbered)} (version order changed): {', '.join(r.renumbered)}")
    if r.empty:
        typer.echo(f"no text extracted from: {', '.join(r.empty)}")


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


if __name__ == "__main__":
    app()
