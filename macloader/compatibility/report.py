"""Terminal presentation helpers using Rich for reports, build plans, and dependencies."""

from typing import Any, Dict, List
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityReport, CompatibilityState
from macloader.domain.dependencies import DependencySpec, ResolvedDependencySet
from macloader.domain.hardware import HardwareSnapshot


def _state_badge(state: CompatibilityState) -> Text:
    colors = {
        CompatibilityState.SUPPORTED: "bold green",
        CompatibilityState.CONDITIONAL: "bold yellow",
        CompatibilityState.EXPERIMENTAL: "bold cyan",
        CompatibilityState.BLOCKED: "bold red",
        CompatibilityState.UNKNOWN: "bold magenta",
    }
    style = colors.get(state, "white")
    return Text(f"[{state.value}]", style=style)


def render_hardware_snapshot(snapshot: HardwareSnapshot, console: Console) -> None:
    """Render a HardwareSnapshot to the Rich console."""
    table = Table(title=f"Hardware Snapshot: {snapshot.product_name}", show_header=True, header_style="bold cyan")
    table.add_column("Category", style="dim", width=18)
    table.add_column("Details")

    table.add_row("Manufacturer", snapshot.manufacturer or "Unknown")
    table.add_row("Product Name", snapshot.product_name or "Unknown")
    table.add_row("Product Version", snapshot.product_version or "Unknown")
    table.add_row("Machine Type", snapshot.machine_type or "Unknown")
    table.add_row("BIOS Version", f"{snapshot.bios_version or 'Unknown'} ({snapshot.bios_date or 'No date'})")

    if snapshot.cpu:
        table.add_row("CPU", f"{snapshot.cpu.model_name} ({snapshot.cpu.cores} cores, {snapshot.cpu.threads} threads)")

    if snapshot.igpu:
        pci_str = f" [{snapshot.igpu.pci.canonical_id}]" if snapshot.igpu.pci else ""
        table.add_row("Intel iGPU", f"{snapshot.igpu.name}{pci_str}")

    if snapshot.dgpus:
        for dgpu in snapshot.dgpus:
            pci_str = f" [{dgpu.pci.canonical_id}]" if dgpu.pci else ""
            table.add_row("Discrete GPU", f"{dgpu.name}{pci_str}")

    if snapshot.audio:
        for a in snapshot.audio:
            codec = f" (Codec: {a.codec_name})" if a.codec_name else ""
            table.add_row("Audio", f"{a.name}{codec}")

    if snapshot.ethernet:
        for eth in snapshot.ethernet:
            table.add_row("Ethernet", eth.name)

    if snapshot.wifi:
        for w in snapshot.wifi:
            table.add_row("Wi-Fi", w.name)

    if snapshot.bluetooth:
        for bt in snapshot.bluetooth:
            table.add_row("Bluetooth", bt.name)

    if snapshot.storage:
        for s in snapshot.storage:
            table.add_row("Storage", f"{s.model} ({s.kind.upper()})")

    if snapshot.input_devices:
        for inp in snapshot.input_devices:
            table.add_row("Input Device", f"{inp.name} ({inp.kind} via {inp.bus})")

    if snapshot.thunderbolt and snapshot.thunderbolt.present:
        table.add_row("Thunderbolt", snapshot.thunderbolt.controller_name or "Present")

    console.print(table)


def render_compatibility_report(report: CompatibilityReport, console: Console) -> None:
    """Render a CompatibilityReport to the Rich console."""
    badge = _state_badge(report.overall_state)
    header = Text.assemble(
        ("Compatibility Report for ", "bold"),
        (f"{report.model_name} ", "bold yellow"),
        ("on ", "bold"),
        (f"macOS {report.target_macos.capitalize()} ", "bold green"),
        badge,
    )
    console.print(Panel(header, border_style="cyan"))

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Category", width=14)
    table.add_column("Component", width=30)
    table.add_column("Status", width=16)
    table.add_column("Notes / Actions")

    table.add_row(
        "System Model",
        report.model_name,
        _state_badge(report.model_decision.state),
        report.model_decision.reason,
    )

    for comp in report.component_results:
        req_actions = ""
        if comp.decision.required_actions:
            req_actions = " | Action: " + "; ".join(comp.decision.required_actions)
        notes = f"{comp.decision.reason}{req_actions}"
        table.add_row(
            comp.category.capitalize(),
            comp.component_name,
            _state_badge(comp.decision.state),
            notes,
        )

    console.print(table)

    if report.warnings:
        warn_text = Text("\n".join(f"• {w}" for w in report.warnings), style="yellow")
        console.print(Panel(warn_text, title="[bold yellow]Warnings & Limitations[/bold yellow]", border_style="yellow"))


def render_build_plan(plan: BuildPlan, console: Console) -> None:
    """Render a preliminary BuildPlan to the Rich console."""
    badge = _state_badge(plan.support_state)
    header = Text.assemble(
        ("Preliminary EFI BuildPlan: ", "bold cyan"),
        (f"{plan.target_model} ", "bold yellow"),
        (f"({plan.target_macos.capitalize()}) ", "bold green"),
        badge,
    )
    console.print(Panel(header, border_style="cyan"))

    cap_table = Table(title="Required Capabilities", show_header=True, header_style="bold blue")
    cap_table.add_column("Capability ID")
    for cap in plan.required_capabilities:
        cap_table.add_row(cap)
    console.print(cap_table)

    comp_table = Table(title="Planned EFI Components & Future Policies", show_header=True, header_style="bold blue")
    comp_table.add_column("Category", width=18)
    comp_table.add_column("Component Name", width=28)
    comp_table.add_column("Policy / Driver", width=36)
    for c in plan.planned_components:
        comp_table.add_row(
            c.get("category", "").capitalize(),
            c.get("name", ""),
            c.get("driver_requirement") or c.get("policy", ""),
        )
    console.print(comp_table)

    if plan.unresolved_requirements:
        unres_text = Text("\n".join(f"• {u}" for u in plan.unresolved_requirements), style="cyan")
        console.print(
            Panel(
                unres_text,
                title="[bold cyan]Unresolved Requirements (Deferred to v0.0.4/v0.0.5)[/bold cyan]",
                border_style="cyan",
            )
        )


def render_dependency_catalog(specs: List[DependencySpec], policy_version: str, console: Console) -> None:
    """Render the verified OpenCore dependency catalog."""
    header = Text.assemble(
        ("Verified Dependency Catalog ", "bold cyan"),
        (f"[Policy: {policy_version}]", "bold yellow"),
    )
    console.print(Panel(header, border_style="cyan"))

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("ID", width=16)
    table.add_column("Project", width=22)
    table.add_column("Version", width=10)
    table.add_column("Dependencies", width=16)
    table.add_column("License", width=14)
    table.add_column("Verified")

    for s in specs:
        deps_str = ", ".join(s.dependencies) if s.dependencies else "-"
        table.add_row(s.id, s.project_name, s.version, deps_str, s.license, s.date_verified)

    console.print(table)


def render_resolved_dependency_set(dep_set: ResolvedDependencySet, console: Console) -> None:
    """Render a ResolvedDependencySet in a structured, explainable table."""
    status_text = Text("[COMPLETE]", style="bold green") if dep_set.is_complete else Text("[INCOMPLETE]", style="bold yellow")
    header = Text.assemble(
        ("Resolved Dependencies for ", "bold"),
        (f"{dep_set.target_model} ", "bold yellow"),
        (f"({dep_set.target_macos.capitalize()}) ", "bold green"),
        (f"[{dep_set.variant.value}] ", "bold magenta"),
        status_text,
    )
    console.print(Panel(header, border_style="cyan"))

    table = Table(title="Resolved Components (Topologically Ordered)", show_header=True, header_style="bold blue")
    table.add_column("Order", width=6)
    table.add_column("Project", width=24)
    table.add_column("Version", width=10)
    table.add_column("Type", width=12)
    table.add_column("Reason / Required By")

    for i, dep in enumerate(dep_set.resolved_dependencies, 1):
        dep_type = "Transitive" if dep.is_transitive else "Direct"
        type_style = "dim cyan" if dep.is_transitive else "bold white"
        table.add_row(
            str(i),
            dep.project_name,
            dep.version,
            Text(dep_type, style=type_style),
            f"{dep.reason} ({dep.required_by})",
        )

    console.print(table)

    if dep_set.unresolved_requirements:
        unres_text = Text("\n".join(f"• {u}" for u in dep_set.unresolved_requirements), style="bold yellow")
        console.print(Panel(unres_text, title="[bold yellow]Unresolved Requirements[/bold yellow]", border_style="yellow"))

    if dep_set.warnings:
        warn_text = Text("\n".join(f"• {w}" for w in dep_set.warnings), style="yellow")
        console.print(Panel(warn_text, title="[bold yellow]Warnings[/bold yellow]", border_style="yellow"))


def render_cache_stats(stats: Dict[str, Any], console: Console) -> None:
    """Render cache status and cached files."""
    header = Text.assemble(
        ("Local Dependency Cache: ", "bold cyan"),
        (f"{stats['total_files']} files, ", "bold yellow"),
        (f"{stats['total_size_bytes'] / (1024*1024):.2f} MB", "bold green"),
    )
    console.print(Panel(header, border_style="cyan"))

    if stats["entries"]:
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Filename")
        table.add_column("Size", width=14)

        for entry in stats["entries"]:
            size_mb = f"{entry['size_bytes'] / (1024*1024):.2f} MB"
            table.add_row(entry["name"], size_mb)

        console.print(table)
