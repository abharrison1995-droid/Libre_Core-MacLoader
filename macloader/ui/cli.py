"""Command line interface (CLI) for MacLoader using Click and Rich."""

import json
import os
from pathlib import Path
import sys
from typing import Optional

import click
from rich.console import Console
from rich.markup import escape

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
from macloader.domain.configuration import UserConfiguration
from macloader.exceptions import DependencyError, MacLoaderError
from macloader.orchestrator import Orchestrator
from macloader.build.efi import EfiBuilder
from macloader.domain.recovery import RecoveryState
from macloader.domain.contracts import BuildManifest
from macloader.domain.configuration import UserConfiguration
from macloader.evidence.usb import UsbEvidenceSession
from macloader.evidence.acpi import AcpiEvidenceBundle
from macloader.workflow.service import PRIVATE_IDENTITY_CONFIRMATION, WorkflowService
from macloader.ui.tui import run_tui
from macloader.removable import RemovableDevice, RemovableMediaWriter
from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader

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
    default=None,
    type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False),
    help="Target macOS version (sonoma, sequoia, tahoe).",
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from a fixture file.")
@click.option("-o", "--output", type=click.Path(dir_okay=False, writable=True, path_type=Path), help="Save JSON plan to file.")
@click.option("--config", "configuration_id", type=str, help="Evaluate a persisted exact configuration instead of a product-only plan.")
def plan_cmd(target_macos: Optional[str], json_mode: bool, fixture: Optional[Path], output: Optional[Path], configuration_id: Optional[str]) -> None:
    """Generate a preliminary BuildPlan detailing future EFI requirements."""
    try:
        orchestrator = Orchestrator()
        if configuration_id:
            workflow = WorkflowService(orchestrator=orchestrator)
            configuration = workflow.load(configuration_id)
            if target_macos is not None and (configuration.target is None or configuration.target.product_id != target_macos.lower()):
                raise click.ClickException("--macos conflicts with --config target; change the saved configuration explicitly or omit --macos")
            snapshot = orchestrator.probe_hardware(fixture_path=fixture) if fixture else workflow.resume_snapshot(configuration_id)
            state = workflow.evaluate(configuration, snapshot)
            plan = state.evaluation.plan
        else:
            snapshot = orchestrator.probe_hardware(fixture_path=fixture)
            plan = orchestrator.generate_plan(snapshot, target_macos=target_macos or DEFAULT_MACOS_TARGET)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(plan.to_json(indent=2), encoding="utf-8")
            if not json_mode:
                console.print(f"[green]BuildPlan saved to {output}[/green]")

        if json_mode:
            click.echo(plan.to_json(indent=2))
        else:
            render_build_plan(plan, console)

    except click.ClickException:
        raise
    except (MacLoaderError, OSError, ValueError) as e:
        err_console.print(f"[bold red]BuildPlan Error:[/bold red] {e}")
        raise click.ClickException(str(e)) from e


@cli.command("configure")
@click.option("--version", "macos_version", type=str, help="Exact macOS version from the trusted release catalog.")
@click.option("--build", "macos_build", type=str, help="Exact macOS build from the trusted release catalog.")
@click.option("--json", "json_mode", is_flag=True, help="Output the evaluated configuration and issues as JSON.")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Load hardware snapshot from a fixture file.")
def configure_cmd(macos_version: Optional[str], macos_build: Optional[str], json_mode: bool, fixture: Optional[Path]) -> None:
    """Evaluate a schema-driven T480s configuration draft against a fixture."""
    try:
        orchestrator = Orchestrator()
        snapshot = orchestrator.probe_hardware(fixture_path=fixture)
        draft = orchestrator.new_configuration(snapshot)
        if macos_version is not None or macos_build is not None:
            if not macos_version or not macos_build:
                raise click.ClickException("--version and --build must be supplied together")
            release = orchestrator.configuration_service.policy.get_release("sequoia", macos_version, macos_build)
            if release is None:
                raise click.ClickException("The exact version/build is not present in the trusted release catalog")
            draft = UserConfiguration.from_dict({
                **draft.to_dict(), "target": release.target().to_dict(),
            })
        evaluation = orchestrator.evaluate_configuration(draft, snapshot)
        payload = {
            "configuration": evaluation.configuration.to_dict(),
            "issues": [issue.to_dict() for issue in evaluation.issues],
            "plan": evaluation.plan.to_dict(),
            "accepted": evaluation.accepted is not None,
        }
        if json_mode:
            click.echo(json.dumps(payload, indent=2))
        else:
            console.print(f"Configuration issues: {len(evaluation.issues)}")
            console.print(f"BuildPlan: {evaluation.plan.target_model} / {evaluation.plan.target_macos}")
            for issue in evaluation.issues:
                console.print(f"- {issue.code}: {issue.explanation}")
    except (MacLoaderError, ValueError, click.ClickException) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        sys.exit(1)


@cli.group("config")
def config_group() -> None:
    """Create, review, migrate and persist schema-driven configurations."""
    pass


@config_group.command("new")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Hardware fixture for deterministic creation.")
@click.option("--sanitize/--no-sanitize", default=False)
@click.option("--json", "json_mode", is_flag=True)
def config_new_cmd(fixture: Optional[Path], sanitize: bool, json_mode: bool) -> None:
    """Create and atomically persist a new configuration draft."""
    try:
        service = WorkflowService()
        draft, snapshot = service.create(fixture, sanitize=sanitize)
        service.save_snapshot(draft, snapshot)
        path = service.save(draft)
        payload = {"configuration": draft.to_dict(), "snapshot": {"snapshot_id": snapshot.snapshot_id}, "saved": True}
        if json_mode:
            click.echo(json.dumps(payload, indent=2))
        else:
            console.print(f"Created configuration {draft.configuration_id}")
            console.print(f"Saved revision {draft.revision}")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("show")
@click.argument("configuration_id")
@click.option("--json", "json_mode", is_flag=True)
def config_show_cmd(configuration_id: str, json_mode: bool) -> None:
    """Show one persisted configuration without probing hardware or networking."""
    try:
        draft = WorkflowService().load(configuration_id)
        if json_mode:
            click.echo(draft.to_json(indent=2))
        else:
            console.print(f"Configuration {draft.configuration_id} revision {draft.revision}")
            console.print(f"Semantic digest: {draft.semantic_digest}")
            console.print(f"Target: {draft.target.to_dict() if draft.target else 'not selected'}")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("set")
@click.argument("configuration_id")
@click.option("--version", "macos_version", type=str)
@click.option("--build", "macos_build", type=str)
@click.option("--option", "options", multiple=True, help="Set a policy option as option.id=value.")
@click.option("--identity-ref", type=str, help="Select an existing private identity JSON filename for deliberate reuse.")
@click.option("--json", "json_mode", is_flag=True)
def config_set_cmd(configuration_id: str, macos_version: Optional[str], macos_build: Optional[str], options: tuple[str, ...], identity_ref: Optional[str], json_mode: bool) -> None:
    """Apply exact target and policy option changes, invalidating stale acknowledgements."""
    try:
        service = WorkflowService()
        draft = service.load(configuration_id)
        if (macos_version is None) != (macos_build is None):
            raise click.ClickException("--version and --build must be supplied together")
        if macos_version and macos_build:
            draft = service.set_target(draft, macos_version, macos_build)
        for item in options:
            if "=" not in item:
                raise click.ClickException("--option must use option.id=value")
            option_id, value = item.split("=", 1)
            draft = service.set_option(draft, option_id, value)
        if identity_ref:
            draft = service.set_identity_reference(draft, identity_ref)
        path = service.save(draft)
        payload = {"configuration": draft.to_dict(), "saved": True}
        click.echo(json.dumps(payload, indent=2) if json_mode else f"Saved revision {draft.revision}")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("check")
@click.argument("configuration_id")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Optional matching hardware snapshot fixture; otherwise resume the private saved snapshot.")
@click.option("--json", "json_mode", is_flag=True)
def config_check_cmd(configuration_id: str, fixture: Optional[Path], json_mode: bool) -> None:
    """Evaluate configuration against its saved snapshot or an explicit matching fixture."""
    try:
        service = WorkflowService()
        draft = service.load(configuration_id)
        snapshot = service.orchestrator.probe_hardware(fixture_path=fixture) if fixture else service.resume_snapshot(configuration_id)
        state = service.evaluate(draft, snapshot)
        if json_mode:
            click.echo(service.render_json(state))
        else:
            console.print(f"Configuration issues: {len(state.evaluation.issues)}")
            for issue in state.evaluation.issues:
                console.print(f"- {issue.code}: {issue.explanation}")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("acknowledge")
@click.argument("configuration_id")
@click.option("--rule", "rule_id", required=True, help="Exact policy rule or option ID being acknowledged.")
@click.option("--warning", "warning_text", required=True, help="Exact warning text shown during review.")
@click.option("--json", "json_mode", is_flag=True)
def config_acknowledge_cmd(configuration_id: str, rule_id: str, warning_text: str, json_mode: bool) -> None:
    """Record an experimental acknowledgement bound to the current draft."""
    try:
        service = WorkflowService()
        updated = service.acknowledge(service.load(configuration_id), rule_id, warning_text)
        path = service.save(updated)
        payload = {"configuration": updated.to_dict(), "saved": True}
        click.echo(json.dumps(payload, indent=2) if json_mode else f"Saved acknowledgement in revision {updated.revision}")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("export")
@click.argument("configuration_id")
@click.argument("output", type=click.Path(dir_okay=False, writable=True, path_type=Path))
def config_export_cmd(configuration_id: str, output: Path) -> None:
    """Export a redacted public configuration review artifact."""
    try:
        service = WorkflowService()
        service.export_file(service.load(configuration_id), output)
        console.print(f"Exported redacted configuration to {output.name}")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("import")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "json_mode", is_flag=True)
def config_import_cmd(input_path: Path, json_mode: bool) -> None:
    """Import schema-1 JSON or migrate a legacy product-only plan for review."""
    try:
        service = WorkflowService()
        draft, issues = service.import_as_new(input_path)
        path = service.save(draft)
        payload = {"configuration": draft.to_dict(), "issues": [getattr(issue, "to_dict")() for issue in issues], "saved": True}
        click.echo(json.dumps(payload, indent=2) if json_mode else f"Imported revision {draft.revision}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc


@config_group.command("migrate")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "json_mode", is_flag=True)
def config_migrate_cmd(input_path: Path, json_mode: bool) -> None:
    """Explicit alias for the review-required configuration import boundary."""
    try:
        service = WorkflowService()
        draft, issues = service.migrate_file(input_path)
        path = service.save(draft)
        payload = {"configuration": draft.to_dict(), "issues": [getattr(issue, "to_dict")() for issue in issues], "saved": True}
        click.echo(json.dumps(payload, indent=2) if json_mode else f"Migrated revision {draft.revision}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group("evidence")
def evidence_group() -> None:
    """Import reviewed hardware evidence into a configuration draft."""
    pass


@evidence_group.command("import")
@click.argument("configuration_id")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(["usb", "acpi"], case_sensitive=False), required=True)
@click.option("--json", "json_mode", is_flag=True)
def evidence_import_cmd(configuration_id: str, input_path: Path, kind: str, json_mode: bool) -> None:
    """Import sanitized USB or ACPI evidence metadata; private raw captures stay local."""
    try:
        service = WorkflowService()
        draft = service.load(configuration_id)
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        record = (
            UsbEvidenceSession.from_dict(payload).to_evidence_record()
            if kind.lower() == "usb"
            else AcpiEvidenceBundle.from_dict(payload).to_evidence_record()
        )
        updated = service.add_evidence(draft, record)
        path = service.save(updated)
        result = {"configuration": updated.to_dict(), "evidence": record.to_dict(), "saved": True}
        click.echo(json.dumps(result, indent=2) if json_mode else f"Imported {kind.lower()} evidence into revision {updated.revision}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc


@evidence_group.command("acpi-capture")
@click.argument("destination", type=click.Path(file_okay=False, path_type=Path))
@click.option("--json", "json_mode", is_flag=True)
def evidence_acpi_capture_cmd(destination: Path, json_mode: bool) -> None:
    """Read-only capture of this T480s's firmware DSDT/SSDTs (Linux, run with sudo).

    DESTINATION must be a new private directory outside any Git checkout.
    Nothing on the machine is modified; tables are only read from sysfs.
    """
    from macloader.evidence.acpi_capture import AcpiCaptureError, capture_acpi_tables, sudo_owner

    owner = sudo_owner()
    if owner is None and hasattr(os, "geteuid") and os.geteuid() == 0:
        click.echo(
            "Warning: running as root without sudo; the capture will be root-owned. "
            "Run it through sudo from your own account so the import can read it.",
            err=True,
        )
    try:
        result = capture_acpi_tables(destination, owner=owner)
    except (AcpiCaptureError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = result.summary()
    if json_mode:
        click.echo(json.dumps(summary, indent=2))
    else:
        click.echo(
            f"Captured {summary['dsdt_count']} DSDT and {summary['ssdt_count']} SSDTs from "
            f"{summary['machine_type']} / {summary['bios_binding']} into {result.capture_root.name}/PRIVATE-ACPI "
            "(owner-only). Keep this directory private."
        )
        click.echo(f"Next: {summary['next_step']}")


@evidence_group.command("acpi-import")
@click.argument("configuration_id")
@click.argument("capture_directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="The exact fixture snapshot bound to this configuration.")
@click.option("--json", "json_mode", is_flag=True)
def evidence_acpi_import_cmd(configuration_id: str, capture_directory: Path, fixture: Optional[Path], json_mode: bool) -> None:
    """Validate and privately import this T480s machine's DSDT and SSDT set."""
    try:
        service = WorkflowService()
        configuration = service.load(configuration_id)
        snapshot = (
            service.orchestrator.probe_hardware(fixture_path=fixture)
            if fixture is not None
            else service.resume_snapshot(configuration_id)
        )
        updated, record = service.import_acpi_capture(configuration, snapshot, capture_directory)
        service.save(updated)
        payload = {
            "configuration_id": updated.configuration_id,
            "revision": updated.revision,
            "evidence_id": record.evidence_id,
            "evidence_digest": record.digest,
            "completeness": record.completeness.value,
            "confidence": record.confidence.value,
            "raw_tables_persisted_privately": True,
        }
        click.echo(json.dumps(payload, indent=2) if json_mode else "ACPI capture validated and stored privately.")
    except (MacLoaderError, OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group("identity")
def identity_group() -> None:
    """Deliberately generate or reuse a private local SMBIOS identity."""
    pass


@identity_group.command("generate")
@click.argument("configuration_id")
@click.option("--confirm", required=True, help="Type the exact private-identity checkpoint phrase.")
def identity_generate_cmd(configuration_id: str, confirm: str) -> None:
    """Generate one private identity with the verified local macserial tool."""
    if confirm != PRIVATE_IDENTITY_CONFIRMATION:
        raise click.ClickException(f"Type the exact confirmation: {PRIVATE_IDENTITY_CONFIRMATION}")
    try:
        service = WorkflowService()
        updated, _reference = service.generate_private_identity(service.load(configuration_id), confirm)
        service.save(updated)
        console.print(f"Private identity generated and bound to configuration {updated.configuration_id}; values remain in protected local storage.")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@identity_group.command("use")
@click.argument("configuration_id")
@click.option("--reference", required=True, help="Private identity JSON filename in this workspace's protected identity store.")
def identity_use_cmd(configuration_id: str, reference: str) -> None:
    """Verify and deliberately reuse an existing protected local identity."""
    try:
        service = WorkflowService()
        updated = service.reuse_private_identity(service.load(configuration_id), reference)
        service.save(updated)
        console.print(f"Verified private identity reuse for configuration {updated.configuration_id}; values were not displayed.")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group("toolchain")
def toolchain_group() -> None:
    """Inspect or acquire host tools whose bytes are pinned in the catalog."""
    pass


@toolchain_group.command("install")
@click.option("--json", "json_mode", is_flag=True)
def toolchain_install_cmd(json_mode: bool) -> None:
    """Download approved source archives and install only catalog-verified tools."""
    try:
        selection = TrustedToolchainLoader().provision()
        payload = {
            "status": "verified",
            "host_platform": selection.host_platform,
            "host_architecture": selection.host_architecture,
            "opencore": selection.opencore_version,
            "ocvalidate": selection.ocvalidate_version,
            "iasl": selection.acpi_compiler,
            "macserial": selection.identity_tool,
            "toolchain_digest": selection.digest,
        }
        click.echo(json.dumps(payload, indent=2) if json_mode else "Catalog-pinned OpenCore, ocvalidate, iASL and macserial are verified and installed in the configured workspace.")
    except (MacLoaderError, OSError, ValueError) as exc:
        raise click.ClickException(f"Pinned toolchain installation failed: {exc}") from exc


@toolchain_group.command("status")
@click.option("--json", "json_mode", is_flag=True)
def toolchain_status_cmd(json_mode: bool) -> None:
    try:
        loader = TrustedToolchainLoader()
        selection = loader.select()
        payload = {"status": "verified", "host_platform": selection.host_platform,
                   "host_architecture": selection.host_architecture, "opencore": selection.opencore_version,
                   "ocvalidate": selection.ocvalidate_version, "iasl": selection.acpi_compiler,
                   "macserial": selection.identity_tool, "toolchain_digest": selection.digest}
    except (MacLoaderError, OSError, ValueError) as exc:
        payload = {"status": "missing_or_unverified", "action": "run `macloader toolchain install`", "reason": str(exc)}
    click.echo(json.dumps(payload, indent=2) if json_mode else f"Toolchain status: {payload['status']}. {payload.get('action', '')} {payload.get('reason', '')}")


@cli.command("preflight")
@click.option("--config", "configuration_id", type=str, help="Saved workflow configuration to inspect.")
@click.option("--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Exact hardware fixture to bind to the preflight.")
@click.option("--json", "json_mode", is_flag=True)
def preflight_cmd(configuration_id: Optional[str], fixture: Optional[Path], json_mode: bool) -> None:
    """List all known installation prerequisites without downloading or writing media."""
    service = WorkflowService()
    configuration: Optional[UserConfiguration] = None
    snapshot = None
    if configuration_id:
        try:
            configuration = service.load(configuration_id)
            snapshot = service.orchestrator.probe_hardware(fixture_path=fixture) if fixture else service.resume_snapshot(configuration_id)
        except (MacLoaderError, OSError, ValueError) as exc:
            click.echo(f"Preflight could not load the selected configuration or snapshot: {exc}", err=True)
    else:
        try:
            snapshot = service.orchestrator.probe_hardware(fixture_path=fixture)
        except (MacLoaderError, OSError, ValueError) as exc:
            click.echo(f"Hardware probe unavailable: {type(exc).__name__}", err=True)
    report = service.preflight(configuration, snapshot)
    if json_mode:
        click.echo(json.dumps(report, indent=2))
    else:
        console.print(f"Installation preflight: {report['status']}")
        for check in report["checks"]:
            assert isinstance(check, dict)
            # States such as "[missing]" would otherwise be parsed as Rich markup.
            console.print(escape(f"[{check['state']}] {check['id']}: {check['summary']}"))
            if check["action"]:
                console.print(escape(f"  Next: {check['action']}"))


@cli.command("tui")
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--config", "configuration_id", type=str)
def tui_cmd(fixture: Optional[Path], configuration_id: Optional[str]) -> None:
    """Open the keyboard-accessible non-destructive Textual workflow."""
    run_tui(fixture=fixture, config_id=configuration_id)


@cli.group("usb")
def usb_group() -> None:
    """Inspect removable targets and create non-destructive media plans."""
    pass


@usb_group.command("list")
@click.option("--json", "json_mode", is_flag=True)
def usb_list_cmd(json_mode: bool) -> None:
    """List only explicitly supported adapters; never writes or dismounts devices."""
    try:
        payload = WorkflowService.removable_status()
    except Exception as exc:
        raise click.ClickException(
            f"Device discovery is unavailable: {type(exc).__name__}. Retry or inspect the configured adapter."
        ) from exc
    if json_mode:
        click.echo(json.dumps(payload, indent=2))
    else:
        console.print(
            f"Removable adapter status: {payload['status']}; "
            "no write or dismount operation was attempted."
        )


@usb_group.command("plan")
@click.option("--device-id", required=True)
@click.option("--model", required=True)
@click.option("--capacity-bytes", type=int, required=True)
@click.option("--serial", required=True)
@click.option("--required-bytes", type=int, required=True)
@click.option("--source-dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--removable/--not-removable", default=True)
@click.option("--system-disk/--not-system-disk", default=False)
@click.option("--mounted/--unmounted", default=False)
@click.option("--read-only/--writable", default=False)
@click.option("--json", "json_mode", is_flag=True)
def usb_plan_cmd(
    device_id: str,
    model: str,
    capacity_bytes: int,
    serial: str,
    required_bytes: int,
    source_dir: Optional[Path],
    removable: bool,
    system_disk: bool,
    mounted: bool,
    read_only: bool,
    json_mode: bool,
) -> None:
    """Create an immutable, non-destructive preflight plan from an explicit descriptor."""
    try:
        device = RemovableDevice(
            device_id=device_id,
            model=model,
            capacity_bytes=capacity_bytes,
            is_system_disk=system_disk,
            is_removable=removable,
            mounted=mounted,
            serial=serial,
            read_only=read_only,
        )
        plan = RemovableMediaWriter().dry_run(device, required_bytes, source_dir=source_dir)
        payload = plan.to_dict()
        click.echo(json.dumps(payload, indent=2) if json_mode else f"Non-destructive media plan created for {device.model}; writes remain disabled")
    except (MacLoaderError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group("recovery")
def recovery_group() -> None:
    """Discover and verify the exact Apple Recovery target (never silently substitute)."""
    pass


@recovery_group.command("list")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def recovery_list_cmd(json_mode: bool) -> None:
    """Show the frozen Recovery policy without network access."""
    service = Orchestrator().recovery_service
    target = service.target()
    payload = {
        "policy_id": service.policy.policy_id,
        "policy_digest": service.policy.digest,
        "tool_version": service.policy.tool_version,
        "tool_digest": service.policy.tool_digest,
        "target": target.to_dict(),
        "board_id": service.policy.board_id,
        "query_scheme": service.policy.query_scheme,
        "discovery_host": service.policy.discovery_host,
        "asset_hosts": list(service.policy.asset_hosts),
        "authentication": service.policy.authentication,
        "minimum_free_bytes": service.policy.minimum_free_bytes,
    }
    if json_mode:
        click.echo(json.dumps(payload, indent=2))
    else:
        console.print(f"Recovery target: {target.product_name} {target.version} ({target.build})")
        console.print(f"Authentication: {payload['authentication']}")
        console.print(f"Policy digest: {payload['policy_digest']}")


@recovery_group.command("resolve")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def recovery_resolve_cmd(json_mode: bool) -> None:
    """Query Apple for the exact frozen target; a default/latest response is not accepted."""
    service = WorkflowService()

    def show_gate() -> None:
        # Both a response and a transport failure are recorded as current
        # evidence; show what the shared preflight now derives from it.
        readiness = service.recovery_readiness()
        if not json_mode:
            console.print(escape(f"Preflight exact_recovery: {readiness.state}: {readiness.summary}"))
            console.print(escape(f"Next action: {readiness.action}"))

    try:
        result = service.discover_recovery()
    except (MacLoaderError, OSError, ValueError) as exc:
        err_console.print(escape(f"Recovery Discovery Error: {exc}"))
        show_gate()
        raise click.ClickException(str(exc)) from exc
    readiness = service.recovery_readiness()
    if json_mode:
        click.echo(json.dumps({**result.to_dict(), "readiness": readiness.__dict__}, indent=2))
    else:
        console.print(f"Recovery discovery: {result.state.value}")
        for diagnostic in result.diagnostics:
            console.print(escape(f"- {diagnostic}"))
        show_gate()
    if result.state != RecoveryState.DISCOVERED:
        raise click.ClickException("The exact Recovery target was not identified; no fallback was selected")


@recovery_group.command("status")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def recovery_status_cmd(json_mode: bool) -> None:
    """Show the exact-Recovery gate derived from recorded evidence (no network access)."""
    try:
        readiness = WorkflowService().recovery_readiness()
    except (MacLoaderError, OSError, ValueError) as exc:
        err_console.print(f"[bold red]Recovery Status Error:[/bold red] {exc}")
        raise click.ClickException(str(exc)) from exc
    if json_mode:
        click.echo(json.dumps(readiness.__dict__, indent=2))
    else:
        console.print(escape(f"exact_recovery: {readiness.state}"))
        console.print(escape(readiness.summary))
        console.print(escape(f"Next action: {readiness.action}"))


@recovery_group.command("download")
@click.option("--binding", "binding_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Deprecated: handwritten binding files are not accepted.")
@click.option("--configuration-id", type=str, help="Persisted accepted configuration to bind to Recovery.")
@click.option("--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Current hardware snapshot fixture used to re-evaluate the configuration.")
@click.option("--efi-manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Manifest from the current qualified EFI output.")
@click.option("--efi-output", type=click.Path(exists=True, file_okay=False, path_type=Path), help="EFI output directory corresponding to --efi-manifest.")
@click.option("--destination", type=click.Path(file_okay=False, path_type=Path), required=True, help="Ignored/private directory for the Recovery cache.")
@click.option("--allow-large-download", is_flag=True, help="Explicitly authorize the large Apple Recovery acquisition checkpoint.")
@click.option("--resume/--no-resume", default=True, help="Retain validated partial assets and resume with HTTPS Range after interruption.")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def recovery_download_cmd(binding_path: Optional[Path], configuration_id: Optional[str], fixture: Optional[Path], efi_manifest: Optional[Path], efi_output: Optional[Path], destination: Path, allow_large_download: bool, resume: bool, json_mode: bool) -> None:
    """Acquire exact Recovery only from a current accepted configuration and EFI manifest."""
    if not allow_large_download:
        raise click.ClickException("Large Apple Recovery acquisition requires an explicit checkpoint approval")
    try:
        if binding_path is not None:
            raise click.ClickException(
                "Manual Recovery binding JSON is not accepted; supply --configuration-id, --fixture, and --efi-manifest."
            )
        if configuration_id is None or fixture is None or efi_manifest is None or efi_output is None:
            raise click.ClickException(
                "Recovery acquisition requires --configuration-id, --fixture, --efi-manifest, and --efi-output from the shared workflow."
            )
        service = WorkflowService()
        configuration = service.load(configuration_id)
        snapshot = service.orchestrator.probe_hardware(fixture_path=fixture)
        manifest_data = json.loads(efi_manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest_data, dict):
            raise ValueError("EFI manifest must contain an object")
        manifest = BuildManifest.from_dict(manifest_data)
        toolchain = service.orchestrator.trusted_toolchain()
        validation = service.orchestrator.builder.validate_tree(
            efi_output, toolchain=toolchain, expected_manifest=manifest
        )
        if validation.status != "VALID":
            raise ValueError("EFI output failed the trusted manifest validation: " + "; ".join(validation.errors))
        binding = service.derive_recovery_binding(configuration, snapshot, None, manifest, efi_output=efi_output)
        verified_artifacts = {
            "configuration_digest": binding.configuration_digest,
            "build_plan_digest": binding.build_plan_digest,
            "catalog_digest": binding.catalog_digest,
            "toolchain_digest": binding.toolchain_digest,
            "efi_manifest_digest": binding.efi_manifest_digest,
        }
        orchestrator = service.orchestrator
        result = orchestrator.discover_recovery()
        if result.state != RecoveryState.DISCOVERED:
            raise click.ClickException("The exact Recovery target was not identified; no fallback was selected")
        lock, bundle = orchestrator.recovery_service.acquire(
            result, binding, destination, resume=resume, verified_artifacts=verified_artifacts, require_verified=True
        )
        lock_path, evidence_path, state_path = orchestrator.recovery_service.save_verified_bundle(
            lock, bundle.evidence, destination
        )
        payload = {
            "state": lock.state.value,
            "lock": lock_path.name,
            "evidence": evidence_path.name,
            "state_record": state_path.name,
            **bundle.evidence.to_dict(),
        }
        if json_mode:
            click.echo(json.dumps(payload, indent=2))
        else:
            console.print(f"Recovery acquired and verified: {bundle.image_path.name}")
            console.print(f"Evidence: {evidence_path.name}")
    except (MacLoaderError, OSError, ValueError) as exc:
        err_console.print(f"[bold red]Recovery Acquisition Error:[/bold red] {exc}")
        raise click.ClickException(str(exc)) from exc


def _verify_recovery_cache(lock_path: Path, image_path: Path, chunklist_path: Path, json_mode: bool) -> None:
    service = Orchestrator().recovery_service
    lock = service.load_lock(lock_path)
    evidence = service.verify(lock, image_path, chunklist_path)
    payload = {"state": RecoveryState.VERIFIED.value, **evidence.to_dict()}
    if json_mode:
        click.echo(json.dumps(payload, indent=2))
    else:
        console.print(f"Recovery cache verified: {evidence.verified_chunks} signed chunks")


@recovery_group.command("verify")
@click.option("--lock", "lock_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True, help="Redacted Recovery lock JSON.")
@click.option("--image", "image_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--chunklist", "chunklist_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def recovery_verify_cmd(lock_path: Path, image_path: Path, chunklist_path: Path, json_mode: bool) -> None:
    """Verify a cached Recovery bundle offline against its redacted lock."""
    try:
        _verify_recovery_cache(lock_path, image_path, chunklist_path, json_mode)
    except (MacLoaderError, OSError, ValueError) as exc:
        err_console.print(f"[bold red]Recovery Verification Error:[/bold red] {exc}")
        raise click.ClickException(str(exc)) from exc


@recovery_group.group("cache")
def recovery_cache_group() -> None:
    """Inspect or verify the private offline Recovery cache."""
    pass


@recovery_cache_group.command("verify")
@click.option("--lock", "lock_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--image", "image_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--chunklist", "chunklist_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def recovery_cache_verify_cmd(lock_path: Path, image_path: Path, chunklist_path: Path, json_mode: bool) -> None:
    """Offline replay of the exact Recovery cache verification."""
    try:
        _verify_recovery_cache(lock_path, image_path, chunklist_path, json_mode)
    except (MacLoaderError, OSError, ValueError) as exc:
        err_console.print(f"[bold red]Recovery Cache Error:[/bold red] {exc}")
        raise click.ClickException(str(exc)) from exc


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
@click.option("--structural-only", is_flag=True, help="Run structural checks only; this is not qualified release validation.")
@click.option("--ocvalidate", "ocvalidate_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Matching OpenCore ocvalidate executable or script.")
@click.option("--ocvalidate-sha256", type=str, help="SHA-256 for the selected ocvalidate executable/script.")
def validate_cmd(efi_dir: Path, json_mode: bool, structural_only: bool, ocvalidate_path: Optional[Path], ocvalidate_sha256: Optional[str]) -> None:
    """Validate an EFI tree structurally or with the trusted qualified toolchain."""
    builder = EfiBuilder()
    toolchain = None
    if structural_only and (ocvalidate_path or ocvalidate_sha256):
        raise click.ClickException("--structural-only cannot be combined with a caller-supplied validator")
    if not structural_only:
        try:
            loader = TrustedToolchainLoader()
            toolchain = loader.select()
            if ocvalidate_path and Path(toolchain.ocvalidate_path or "").resolve() != ocvalidate_path.resolve():
                raise ToolchainTrustError("requested validator is not the trusted catalog selection")
            if ocvalidate_sha256 and ocvalidate_sha256.lower() != (toolchain.ocvalidate_sha256 or "").lower():
                raise ToolchainTrustError("requested validator digest does not match the trusted catalog selection")
        except ToolchainTrustError as exc:
            raise click.ClickException(f"Qualified validation is unavailable: {exc}") from exc
    report = builder.validate_tree(efi_dir, toolchain=toolchain)
    if json_mode:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        console.print(f"EFI validation: {report.status}")
        for error in report.errors:
            err_console.print(f"[red]{error}[/red]")
    if (structural_only and report.status != "STRUCTURAL_ONLY") or (not structural_only and report.status != "VALID"):
        raise click.ClickException("EFI validation failed")


@cli.command("build")
@click.option("-m", "--macos", "target_macos", default=None, type=click.Choice(SUPPORTED_MACOS_TARGETS, case_sensitive=False))
@click.option("-f", "--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--offline", is_flag=True, help="Use only verified cached dependencies.")
@click.option("--ocvalidate", "ocvalidate_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Matching OpenCore ocvalidate executable or script.")
@click.option("--ocvalidate-sha256", type=str, help="SHA-256 for the selected ocvalidate executable/script.")
@click.option("--config", "configuration_id", type=str, help="Build from a persisted configuration and its exact target bindings.")
def build_cmd(target_macos: Optional[str], fixture: Optional[Path], output: Path, offline: bool, ocvalidate_path: Optional[Path], ocvalidate_sha256: Optional[str], configuration_id: Optional[str]) -> None:
    """Build a validated EFI tree from the actionable hardware plan."""
    try:
        orchestrator = Orchestrator()
        if not configuration_id:
            raise click.ClickException(
                "EFI build requires a persisted reviewed configuration; run "
                "`config new`, set the exact target/evidence, then pass `--config ID`."
            )
        workflow = WorkflowService(orchestrator=orchestrator)
        configuration = workflow.load(configuration_id)
        if target_macos is not None and (configuration.target is None or configuration.target.product_id != target_macos.lower()):
            raise click.ClickException("--macos conflicts with --config target; change the saved configuration explicitly or omit --macos")
        snapshot = orchestrator.probe_hardware(fixture_path=fixture) if fixture else workflow.resume_snapshot(configuration_id)
        result = workflow.build_efi_preview(
            configuration,
            snapshot,
            output,
            offline=offline,
            ocvalidate_path=ocvalidate_path,
            ocvalidate_sha256=ocvalidate_sha256,
        )
        click.echo(json.dumps({"status": result.validation.status, "output": str(result.output_dir), "manifest": result.manifest.to_dict()}, indent=2))
    except click.ClickException:
        raise
    except (MacLoaderError, OSError, ValueError) as e:
        err_console.print(f"[bold red]Build Error:[/bold red] {e}")
        raise click.ClickException(str(e)) from e
