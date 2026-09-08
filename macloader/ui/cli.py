"""Command line interface (CLI) for MacLoader using Click and Rich."""

import json
from pathlib import Path
import sys
from typing import Optional

import click
from rich.console import Console

from macloader import __version__
from macloader.compatibility.report import (
    render_build_plan,
    render_cache_stats,
    render_compatibility_report,
    render_dependency_catalog,
    render_hardware_snapshot,
    render_resolved_dependency_set,
)
from macloader.config import DEFAULT_MACOS_TARGET, SUPPORTED_MACOS_TARGETS
from macloader.diagnostics.logging import setup_logging
from macloader.domain.dependencies import ArtifactVariant
from macloader.exceptions import (
    CompatibilityEvaluationError,
    DatabaseError,
    DependencyError,
    HardwareDetectionError,
    MacLoaderError,
    UnsupportedMacOSError,
    UnsupportedModelError,
)
from macloader.orchestrator import Orchestrator
from macloader.build.efi import EfiBuilder

console = Console()
err_console = Console(stderr=True)


@click.group()
@click.version_option(__version__, prog_name="macloader")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose debug logging.")
def cli(verbose: bool) -> None:
    """Libre_Core MacLoader — ThinkPad OpenCore & macOS provisioning engine."""
    setup_logging(verbose=verbose)


@cli.command("probe")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option("-o", "--output", type=click.Path(dir_okay=False, writable=True, path_type=Path), help="Save JSON snapshot to file.")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from a fixture file.")
@click.option("--sanitize/--no-sanitize", default=False, help="Sanitize serials, UUIDs, and MAC addresses.")
def probe_cmd(json_mode: bool, output: Optional[Path], fixture: Optional[Path], sanitize: bool) -> None:
    """Probe system hardware and display normalized hardware snapshot."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture, sanitize=sanitize)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(snapshot.to_json(indent=2), encoding="utf-8")
            if not json_mode:
                console.print(f"[green]Hardware snapshot saved to {output}[/green]")

        if json_mode:
            click.echo(snapshot.to_json(indent=2))
        else:
            render_hardware_snapshot(snapshot, console)

    except MacLoaderError as e:
        err_console.print(f"[bold red]Detection Error:[/bold red] {e}")
        sys.exit(1)


@cli.command("support")
@click.option(
    "-m",
    "--macos",
    "target_macos",
    default=DEFAULT_MACOS_TARGET,
    type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False),
    help="Target macOS version (sonoma, sequoia, tahoe).",
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from a fixture file.")
@click.option("-o", "--output", type=click.Path(dir_okay=False, writable=True, path_type=Path), help="Save JSON report to file.")
def support_cmd(target_macos: str, json_mode: bool, fixture: Optional[Path], output: Optional[Path]) -> None:
    """Evaluate detected hardware compatibility against a target macOS version."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        report = orchestrator.check_support(snapshot, target_macos=target_macos)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report.to_json(indent=2), encoding="utf-8")
            if not json_mode:
                console.print(f"[green]Compatibility report saved to {output}[/green]")

        if json_mode:
            click.echo(report.to_json(indent=2))
        else:
            render_compatibility_report(report, console)

    except MacLoaderError as e:
        err_console.print(f"[bold red]Support Evaluation Error:[/bold red] {e}")
        sys.exit(1)


@cli.command("plan")
@click.option(
    "-m",
    "--macos",
    "target_macos",
    default=DEFAULT_MACOS_TARGET,
    type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False),
    help="Target macOS version (sonoma, sequoia, tahoe).",
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from a fixture file.")
@click.option("-o", "--output", type=click.Path(dir_okay=False, writable=True, path_type=Path), help="Save JSON plan to file.")
def plan_cmd(target_macos: str, json_mode: bool, fixture: Optional[Path], output: Optional[Path]) -> None:
    """Generate a preliminary BuildPlan detailing future EFI requirements."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        plan = orchestrator.generate_plan(snapshot, target_macos=target_macos)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(plan.to_json(indent=2), encoding="utf-8")
            if not json_mode:
                console.print(f"[green]BuildPlan saved to {output}[/green]")

        if json_mode:
            click.echo(plan.to_json(indent=2))
        else:
            render_build_plan(plan, console)

    except MacLoaderError as e:
        err_console.print(f"[bold red]BuildPlan Error:[/bold red] {e}")
        sys.exit(1)


# =========================================================================
# Dependency Management Command Group (v0.0.4)
# =========================================================================

@cli.group("deps")
def deps_group() -> None:
    """Manage OpenCore and kext dependency catalog, resolution, and cache."""
    pass


@deps_group.command("list")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def deps_list_cmd(json_mode: bool) -> None:
    """List all upstream dependencies in the verified MacLoader catalog."""
    try:
        orchestrator = Orchestrator()
        catalog = orchestrator.db.get_dependency_catalog()
        if not catalog:
            raise DependencyError("Dependency catalog not available.")

        if json_mode:
            data = {
                "policy_version": catalog.policy_version,
                "dependencies": [s.to_dict() for s in catalog.dependencies.values()],
            }
            click.echo(json.dumps(data, indent=2))
        else:
            render_dependency_catalog(list(catalog.dependencies.values()), catalog.policy_version, console)
    except MacLoaderError as e:
        err_console.print(f"[bold red]Catalog Error:[/bold red] {e}")
        raise click.ClickException(str(e)) from e


@deps_group.command("resolve")
@click.option(
    "-m",
    "--macos",
    "target_macos",
    default=DEFAULT_MACOS_TARGET,
    type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False),
    help="Target macOS version (sonoma, sequoia, tahoe).",
)
@click.option(
    "--variant",
    default="RELEASE",
    type=click.Choice(["RELEASE", "DEBUG"], case_sensitive=False),
    help="Artifact build variant (RELEASE, DEBUG).",
)
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from fixture.")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option("-o", "--output", type=click.Path(dir_okay=False, writable=True, path_type=Path), help="Save JSON resolved set to file.")
def deps_resolve_cmd(
    target_macos: str,
    variant: str,
    fixture: Optional[Path],
    json_mode: bool,
    output: Optional[Path],
) -> None:
    """Resolve required dependencies for target model and macOS (side-effect-free, no network I/O)."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        plan = orchestrator.generate_plan(snapshot, target_macos=target_macos)
        dep_set = orchestrator.resolve_dependencies(plan=plan, variant=ArtifactVariant(variant.upper()))

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(dep_set.to_json(indent=2), encoding="utf-8")
            if not json_mode:
                console.print(f"[green]Resolved dependency set saved to {output}[/green]")

        if json_mode:
            click.echo(dep_set.to_json(indent=2))
        else:
            render_resolved_dependency_set(dep_set, console)

    except MacLoaderError as e:
        err_console.print(f"[bold red]Resolution Error:[/bold red] {e}")
        sys.exit(1)


@deps_group.command("fetch")
@click.option(
    "-m",
    "--macos",
    "target_macos",
    default=DEFAULT_MACOS_TARGET,
    type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False),
    help="Target macOS version (sonoma, sequoia, tahoe).",
)
@click.option(
    "--variant",
    default="RELEASE",
    type=click.Choice(["RELEASE", "DEBUG"], case_sensitive=False),
    help="Artifact build variant (RELEASE, DEBUG).",
)
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from fixture.")
@click.option("--offline", is_flag=True, help="Operate strictly from cache without network access.")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def deps_fetch_cmd(
    target_macos: str,
    variant: str,
    fixture: Optional[Path],
    offline: bool,
    json_mode: bool,
) -> None:
    """Acquire and cache verified dependency artifacts with SHA-256 integrity checks."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        plan = orchestrator.generate_plan(snapshot, target_macos=target_macos)
        dep_set = orchestrator.resolve_dependencies(plan=plan, variant=ArtifactVariant(variant.upper()))

        results = orchestrator.fetch_dependencies(dep_set=dep_set, offline=offline, plan=plan)

        if json_mode:
            res_dict = {
                "status": "success" if dep_set.is_complete else "dependencies_cached_incomplete_plan",
                "cached_count": len(results),
                "dependencies_complete": dep_set.is_complete,
                "build_ready": dep_set.is_complete,
                "artifacts": {k: str(v) for k, v in results.items()},
            }
            click.echo(json.dumps(res_dict, indent=2))
        else:
            console.print(f"[bold green]Successfully verified and cached {len(results)} dependencies.[/bold green]")
            if not dep_set.is_complete:
                err_console.print("[yellow]Dependencies are cached, but the plan is not build-ready; unresolved requirements remain.[/yellow]")
            for dep_id, path in results.items():
                console.print(f"  • [cyan]{dep_id}[/cyan] -> {path}")

    except MacLoaderError as e:
        err_console.print(f"[bold red]Fetch Error:[/bold red] {e}")
        sys.exit(1)


@deps_group.command("verify")
@click.option(
    "-m",
    "--macos",
    "target_macos",
    default=DEFAULT_MACOS_TARGET,
    type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False),
    help="Target macOS version (sonoma, sequoia, tahoe).",
)
@click.option(
    "--variant",
    default="RELEASE",
    type=click.Choice(["RELEASE", "DEBUG"], case_sensitive=False),
    help="Artifact build variant (RELEASE, DEBUG).",
)
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from fixture.")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def deps_verify_cmd(
    target_macos: str,
    variant: str,
    fixture: Optional[Path],
    json_mode: bool,
) -> None:
    """Verify SHA-256 integrity of all cached dependencies for the resolved set."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        plan = orchestrator.generate_plan(snapshot, target_macos=target_macos)
        dep_set = orchestrator.resolve_dependencies(plan=plan, variant=ArtifactVariant(variant.upper()))

        status = orchestrator.verify_cached_dependencies(dep_set=dep_set, plan=plan)
        all_ok = bool(status) and all(status.values())

        if json_mode:
            click.echo(json.dumps(status, indent=2))
            if not all_ok:
                raise click.ClickException("Some dependencies are missing or corrupt in cache.")
        else:
            for dep_id, valid in status.items():
                if valid:
                    console.print(f"  • [green]VALID[/green] {dep_id}")
                else:
                    console.print(f"  • [red]MISSING OR CORRUPT[/red] {dep_id}")
                    all_ok = False
            if all_ok and status:
                console.print("[bold green]All required dependencies are valid in cache.[/bold green]")
            elif not all_ok:
                err_console.print("[bold red]Some dependencies are missing or corrupt in cache.[/bold red]")
                sys.exit(1)

    except MacLoaderError as e:
        err_console.print(f"[bold red]Verification Error:[/bold red] {e}")
        sys.exit(1)


@deps_group.command("cache")
@click.option("--clear", is_flag=True, help="Clear all cached dependency downloads and index.")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def deps_cache_cmd(clear: bool, json_mode: bool) -> None:
    """Inspect or manage the local dependency cache."""
    try:
        orchestrator = Orchestrator()
        if clear:
            orchestrator.cache.clear_cache()
            if not json_mode:
                console.print("[yellow]Dependency cache cleared successfully.[/yellow]")
            else:
                click.echo(json.dumps({"status": "cleared"}, indent=2))
            return

        stats = orchestrator.cache.get_cache_stats()
        if json_mode:
            click.echo(json.dumps(stats, indent=2))
        else:
            render_cache_stats(stats, console)
    except MacLoaderError as e:
        err_console.print(f"[bold red]Cache Error:[/bold red] {e}")
        raise click.ClickException(str(e)) from e


@cli.command("validate")
@click.argument("efi_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def validate_cmd(efi_dir: Path, json_mode: bool) -> None:
    """Validate an existing EFI tree structurally."""
    report = EfiBuilder().validate_tree(efi_dir)
    if json_mode:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        console.print(f"EFI validation: {report.status}")
        for error in report.errors:
            err_console.print(f"[red]{error}[/red]")
    if report.status != "VALID":
        raise click.ClickException("EFI validation failed")


@cli.command("build")
@click.option("-m", "--macos", "target_macos", default=DEFAULT_MACOS_TARGET, type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False))
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--offline", is_flag=True, help="Use only verified cached dependencies.")
def build_cmd(target_macos: str, fixture: Optional[Path], output: Path, offline: bool) -> None:
    """Build a validated EFI tree from the actionable hardware plan."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        plan = orchestrator.generate_plan(snapshot, target_macos=target_macos)
        dep_set = orchestrator.resolve_dependencies(plan)
        if not dep_set.is_complete:
            raise click.ClickException("EFI build blocked: unresolved requirements remain in the dependency plan")
        artifact_paths = orchestrator.fetch_dependencies(dep_set, offline=offline, plan=plan)
        result = orchestrator.build_efi(plan, dep_set, artifact_paths, output)
        click.echo(json.dumps({"status": result.validation.status, "output": str(result.output_dir), "manifest": result.manifest.to_dict()}, indent=2))
    except MacLoaderError as e:
        err_console.print(f"[bold red]Build Error:[/bold red] {e}")
        raise click.ClickException(str(e)) from e
