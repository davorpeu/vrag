from dotenv import load_dotenv

load_dotenv()

import typer  # noqa: E402

from vrag import store  # noqa: E402
from vrag.sources.folder import FolderSource  # noqa: E402
from vrag.sync import sync as run_sync  # noqa: E402

app = typer.Typer(no_args_is_help=True)


@app.command()
def init_index(recreate: bool = typer.Option(False, help="Delete and recreate the index")):
    """Create the Elasticsearch index."""
    store.ensure_index(recreate=recreate)
    typer.echo("index ready")


@app.command()
def sync(source: str = typer.Argument("folder"), path: str = typer.Option("data/corpus")):
    """Index new/changed document versions from a source."""
    if source != "folder":
        raise typer.BadParameter("only 'folder' exists so far (git, db, minio come next)")
    r = run_sync(FolderSource(path))
    typer.echo(f"indexed {len(r.indexed)}: {', '.join(r.indexed) or '-'}")
    typer.echo(f"skipped {len(r.skipped)} unchanged")
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
    typer.echo(res.answer)
    for c in res.citations:
        typer.echo(f"  [{c.source}] {c.doc_title} {c.version} (eff. {c.effective_date}) section {c.section}: \"{c.quote}\"")
    if res.unknown_citations:
        typer.echo(f"  ! cited unknown sources: {res.unknown_citations}")


if __name__ == "__main__":
    app()
