"""Guided Textual client renders the service's actions and checkpoint choices."""
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, Footer, Header, Static

from macloader.autoloader.models import ActionKind, Stage
from macloader.autoloader.service import AutoloaderService


class GuidedApp(App[bool]):
    TITLE = "Libre_Core MacLoader — Guided preparation"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Retry"), ("c", "cancel", "Pause")]
    DEFAULT_CSS = """
    #guided-content { padding: 1 2; }
    #campaign-review { height: auto; margin-bottom: 1; }
    #stage-progress { height: auto; color: $text-muted; margin-bottom: 1; }
    #next-action { height: auto; margin-bottom: 1; }
    #guided-content Button { width: 100%; margin-bottom: 1; }
    """

    def __init__(self, service: Optional[AutoloaderService] = None):
        super().__init__()
        self.service = service or AutoloaderService()
        self._cancelled = False
        self._busy = False
        self._choices: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="guided-content"):
            yield Static("Identifying this laptop…", id="campaign-review", markup=False)
            yield Static("", id="stage-progress", markup=False)
            yield Static("", id="next-action", markup=False)
            yield Button("", id="choice-one", variant="primary")
            yield Button("", id="choice-two")
            yield Button("Retry preparation", id="retry-guided")
            yield Button("Engineering / Advanced", id="open-engineering")
        yield Footer()

    def on_mount(self) -> None:
        self._render_status()
        self._advance()

    @staticmethod
    def _stage_label(stage: Stage) -> str:
        return {Stage.ACPI: "Firmware tables", Stage.USB: "Physical USB ports"}.get(stage, stage.value.replace("-", " ").title())

    def _render_status(self, error: str = "") -> None:
        action = self.service.next_action()
        self.query_one("#campaign-review", Static).update(self.service.review_summary())
        stages = list(Stage)
        self.query_one("#stage-progress", Static).update(f"Step {stages.index(action.stage) + 1} of {len(stages)} · {self._stage_label(action.stage)}")
        self.query_one("#next-action", Static).update(error or ("Preparing automatically…" if self._busy else ("Preparation paused. " if action.kind == ActionKind.BLOCKED else "") + action.message))
        self._choices = action.choices
        for index, identifier in enumerate(("choice-one", "choice-two")):
            button = self.query_one(f"#{identifier}", Button)
            button.display = index < len(self._choices)
            button.label = self._choices[index] if index < len(self._choices) else ""
            button.disabled = self._busy
        self.query_one("#retry-guided", Button).disabled = self._busy
        self.query_one("#open-engineering", Button).disabled = self._busy

    @work(thread=True, exclusive=True)
    def _advance(self, choice: Optional[str] = None) -> None:
        self._busy = True
        self._cancelled = False
        self.call_from_thread(self._render_status)
        error = ""
        try:
            if choice is not None:
                self.service.perform_choice(choice)
            else:
                if self.service.session is None:
                    self.service.start()
                self.service.advance_until_blocked(cancel=lambda: self._cancelled)
        except Exception:
            error = "Preparation could not complete. No destructive action occurred; saved work is preserved. Retry or open Engineering diagnostics."
        finally:
            self._busy = False
            self.call_from_thread(self._render_status, error)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._busy:
            return
        identifier = event.button.id
        if identifier in {"choice-one", "choice-two"}:
            index = 0 if identifier == "choice-one" else 1
            if index < len(self._choices):
                self._busy = True
                self._advance(self._choices[index])
        elif identifier == "retry-guided":
            self.action_refresh()
        elif identifier == "open-engineering":
            self.exit(True)

    def action_refresh(self) -> None:
        if not self._busy:
            self.service._blocker = None
            self._busy = True
            self._advance()

    def action_cancel(self) -> None:
        self._cancelled = True
