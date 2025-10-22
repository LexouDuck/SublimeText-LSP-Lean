import os
from typing_extensions import override
import weakref
import threading

import sublime
import sublime_plugin
import mdpopups
from LSP.plugin import LspTextCommand, LspWindowCommand, Request, Session, filename_to_uri
from LSP.plugin.core.types import ClientStates
from LSP.plugin.core.typing import Optional, Any, Dict, List, Tuple
from LSP.plugin.core.protocol import Error, Response
from LSP.plugin.core.registry import windows
from LSP.plugin.core.sessions import AbstractPlugin, SessionViewProtocol, register_plugin, unregister_plugin



GoalData = Any
TermGoalData = Any


# Package name
PACKAGE_NAME = "LSP-lean"

# Settings keys
SETTINGS_FILE = PACKAGE_NAME + ".sublime-settings"
SETTING_DISPLAY_CURRENT_GOALS = "display_current_goals"
SETTING_DISPLAY_EXPECTED_TYPE = "display_expected_type"
SETTING_DISPLAY_MDPOPUP = "display_mdpopup"
SETTING_DISPLAY_NOGOALS = "display_nogoals"
SETTING_DISPLAY_SYNTAXFILE = "display_syntaxfile"



class Lean(AbstractPlugin):
    """
    Represents the plugin itself
    """

    @classmethod
    def name(cls) -> str:
        return "LSP-{}".format(cls.__name__.lower())

    @classmethod
    def basedir(cls) -> str:
        return os.path.join(cls.storage_path(), cls.name())

    @classmethod
    def version_file(cls) -> str:
        return os.path.join(cls.basedir(), "VERSION")

    @classmethod
    def platform_arch(cls) -> str:
        return {
            "linux_x64": "linux-x64.tar.gz",
            "osx_arm64": "darwin-arm64.tar.gz",
            "osx_x64": "darwin-x64.tar.gz",
            "windows_x32": "win32-ia32.zip",
            "windows_x64": "win32-x64.zip",
        }[sublime.platform() + "_" + sublime.arch()]

    @classmethod
    def needs_update_or_installation(cls) -> bool:
        settings, _ = cls.configuration()
        server_version = str(settings.get("server_version"))
        try:
            with open(cls.version_file(), "r") as fp:
                return server_version != fp.read().strip()
        except OSError:
            return True

    @classmethod
    def install_or_update(cls) -> None:
        pass

    @classmethod
    def configuration(cls) -> Tuple[sublime.Settings, str]:
        base_name = "{}.sublime-settings".format(cls.name())
        file_path = "Packages/{}/{}".format(cls.name(), base_name)
        return sublime.load_settings(base_name), file_path

    def __init__(self, weaksession: 'weakref.ref[Session]') -> None:
        super().__init__(weaksession)
        self._settings_change_count = 0
        self._queued_changes: List[Dict[str, Any]] = []

    def _handle_config_commands_async(self, settings_change_count: int) -> None:
        if self._settings_change_count != settings_change_count:
            return
        commands, self._queued_changes = self._queued_changes, []
        session = self.weaksession()
        if not session:
            return
        base, settings = self._get_server_settings(session.window)
        if base is None or settings is None:
            return
        for command in commands:
            action = command["action"]
            key = command["key"]
            value = command["value"]
            if action == "set":
                settings[key] = value
            elif action == "add":
                values = settings.get(key)
                if not isinstance(values, list):
                    values = []
                values.append(value)
                settings[key] = values
            else:
                sublime.error_message(f"{PACKAGE_NAME}: unrecognized action:", action)
        session.window.set_project_data(base)
        if not session.window.project_file_name():
            sublime.message_dialog(" ".join((
                "The server settings have been applied in the Window,",
                "but this Window is not backed by a .sublime-project.",
                "Click on Project > Save Project As... to store the settings."
            )))

    def _get_server_settings(self, window: sublime.Window) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        data = window.project_data()
        if not isinstance(data, dict):
            return None, None
        if "settings" not in data:
            data["settings"] = {}
        if "LSP" not in data["settings"]:
            data["settings"]["LSP"] = {}
        if PACKAGE_NAME not in data["settings"]["LSP"]:
            data["settings"]["LSP"][PACKAGE_NAME] = {}
        if "settings" not in data["settings"]["LSP"][PACKAGE_NAME]:
            data["settings"]["LSP"][PACKAGE_NAME]["settings"] = {}
        return data, data["settings"]["LSP"][PACKAGE_NAME]["settings"]

    @override
    def on_selection_modified_async(self, session_view: SessionViewProtocol):
        """
        Called when cursor position changes
        """
        # Cancel any pending request
        if hasattr(self, '_pending_timeout'):
            try:
                self._pending_timeout.cancel()
            except:
                pass
        # Wait a bit to avoid too many requests while typing/moving cursor
        self._pending_timeout = threading.Timer(0.1, lambda: self._do_request(view))
        self._pending_timeout.start()

    def _do_request(self, view: sublime.View):
        """
        Actually perform the goal state request
        """
        # Get cursor position
        sel = view.sel()
        if len(sel) == 0:
            return
        point = sel[0].begin()
        row, col = view.rowcol(point)
        # Request goal state from Lean server
        self.request_goal_state(view, row, col)

    def request_goal_state(self, view: sublime.View, row: int, col: int):
        """
        Request goal state at cursor position from Lean LSP server
        """
        # Get the session for this view
        session = self.get_lean_session(view)
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session found")
            return
        self.session = session
        # Check if session is ready
        if not self.session.state == ClientStates.READY:
            sublime.status_message(f"{PACKAGE_NAME}: Session not ready yet")
            return
        # Lean requires saved files to process
        if view.is_dirty():
            sublime.status_message(f"{PACKAGE_NAME}: File has unsaved changes, save first")
            #view.run_command('save') # Optionally auto-save
            return
        if not view.file_name():
            sublime.status_message(f"{PACKAGE_NAME}: No open file path")
            return
        # Prepare LSP request parameters
        params = {
            'textDocument': {
                'uri': filename_to_uri(os.path.abspath(view.file_name() or ""))
            },
            'position': {
                'line': row,
                'character': col
            }
        }
        # Send custom Lean LSP request for plain goal
        if self.session.config.settings.get(SETTING_DISPLAY_CURRENT_GOALS):
            #print(f"{PACKAGE_NAME}: Requesting goal at {row}:{col} for {file_uri}")
            request: Request[GoalData] = Request("$/lean/plainGoal", params)
            session.send_request(request, lambda response: self.on_goal_response(view, response))
        # Also request expected type if enabled
        if self.session.config.settings.get(SETTING_DISPLAY_EXPECTED_TYPE):
            #print(f"{PACKAGE_NAME}: Requesting term at {row}:{col} for {file_uri}")
            term_goal_request: Request[TermGoalData] = Request("$/lean/plainTermGoal", params)
            session.send_request(term_goal_request, lambda response: self.on_term_goal_response(view, response))

    def get_lean_session(self, view: sublime.View) -> Optional[Session]:
        """
        Get the active Lean LSP session for this view
        """
        window = view.window()
        if not window:
            return None
        # Get the window manager
        manager = windows.lookup(window)
        if not manager:
            return None
        # Find Lean session
        for session in manager.sessions(view):
            if 'lean' in session.config.name.lower():
                return session
        return None

    def on_goal_response(self, view: sublime.View, response: Response[GoalData]):
        """
        Handle goal state response from Lean server
        """
        if isinstance(response, dict) and 'error' in response:
            sublime.error_message(f"{PACKAGE_NAME}: Error getting goal: {response['error']}")
            return
        # Store the goal response for combined display
        if not hasattr(self, '_goal_data'):
            self._goal_data:GoalData = {}
        view_id = view.id()
        self._goal_data[view_id] = response
        # Display combined view
        self._display_combined_info(view)

    def on_term_goal_response(self, view: sublime.View, response: Response[TermGoalData]):
        """
        Handle expected type (term goal) response from Lean server
        """
        if isinstance(response, dict) and 'error' in response:
            sublime.error_message(f"{PACKAGE_NAME}: Error getting expected type: {response['error']}")
            return
        # Store the term goal response for combined display
        if not hasattr(self, '_term_goal_data'):
            self._term_goal_data:TermGoalData = {}
        view_id = view.id()
        self._term_goal_data[view_id] = response
        # Display combined view
        self._display_combined_info(view)


    def _display_combined_info(self, view: sublime.View):
        """
        Display both goals and expected type together
        """
        view_id = view.id()
        # Get stored data (if available)
        goal_data = getattr(self, '_goal_data', {}).get(view_id)
        term_goal_data = getattr(self, '_term_goal_data', {}).get(view_id)
        display_goal = self.session.config.settings.get(SETTING_DISPLAY_CURRENT_GOALS)
        display_type = self.session.config.settings.get(SETTING_DISPLAY_EXPECTED_TYPE)
        display_mdpopup = self.session.config.settings.get(SETTING_DISPLAY_MDPOPUP)
        display_nogoals = self.session.config.settings.get(SETTING_DISPLAY_NOGOALS)
        # Decide what to display
        has_goals = display_goal and goal_data and goal_data.get('goals')
        has_types = display_type and term_goal_data and term_goal_data.get('goal')
        if not has_goals and not has_types:
            if display_nogoals:
                if display_mdpopup:
                    self.display_goal_popup(view, None, None)
                else:
                    window = view.window()
                    if window:
                        self.display_goal_panel(window, None, None)
            return
        # Display combined information
        if display_mdpopup:
            self.display_goal_popup(view, goal_data, term_goal_data)
        else:
            window = view.window()
            if window:
                self.display_goal_panel(window, goal_data, term_goal_data)


    def display_goal_panel(self, window: sublime.Window,
        goal_data: Optional[GoalData] = None,
        term_goal_data: Optional[TermGoalData] = None):
        """
        Display goal state and expected type in an output panel
        """
        if not window:
            return
        display_nogoals = self.session.config.settings.get(SETTING_DISPLAY_NOGOALS)
        display_syntaxfile = self.session.config.settings.get(SETTING_DISPLAY_SYNTAXFILE)
        # Create or get the infoview panel
        panel_name = "lean_infoview"
        panel = window.find_output_panel(panel_name)
        if not panel:
            panel = window.create_output_panel(panel_name)
            # Set syntax highlighting (optional)
            panel.set_syntax_file(display_syntaxfile)
        # Format the goal and expected type for display
        content_parts: List[str] = []
        # Add goal state
        if goal_data or display_nogoals:
            goal_content = self.format_goal(goal_data)
            if goal_content:
                content_parts.append(goal_content)
                content_parts.append("")
        # Add expected type if available
        if term_goal_data:
            type_content = self.format_type(term_goal_data)
            if type_content:
                content_parts.append(type_content)
                content_parts.append("")
        content = "\n".join(content_parts)
        # Clear and update panel
        panel.run_command('select_all')
        panel.run_command('right_delete')
        panel.run_command('append', {'characters': content})
        # Show the panel
        window.run_command("show_panel", {"panel": f"output.{panel_name}"})

    def format_goal(self, goal_data: Optional[GoalData]) -> str:
        """
        Format goal data as plain text for output panel
        """
        if not goal_data:
            return "No goals"
        # Check if there are goals
        goals = goal_data.get('goals', [])
        if not goals:
            return "No goals"
        # Format each goal
        output: List[str] = []
        for i, goal in enumerate(goals):
            output.append(f"Goal {i + 1}:")
            output.append("-" * 40)
            if isinstance(goal, str): # Simple string goal
                output.append(goal)
                output.append("")
            elif isinstance(goal, dict): # Structured goal with hypotheses and conclusion
                # Show hypotheses
                hypotheses: List[str] = goal.get('hypotheses', []) #type:ignore
                if hypotheses:
                    output.append("\nHypotheses:")
                    for h in hypotheses:
                        output.append(f"  {h}")
                # Show goal
                conclusion: str = goal.get('conclusion', goal.get('type', 'unknown')) #type:ignore
                output.append(f"\n⊢ {conclusion}")
                output.append("")
        return "\n".join(output)

    def format_type(self, term_goal_data: Optional[TermGoalData]) -> str:
        """
        Format expected type data as plain text
        """
        if not term_goal_data:
            return ""
        term = term_goal_data.get('goal')
        if not term:
            return ""
        output: List[str] = []
        output.append("Expected Type")
        output.append("-" * 40)
        output.append(term)
        return "\n".join(output)


    def display_goal_popup(self, view: sublime.View,
        goal_data: Optional[GoalData] = None,
        term_goal_data: Optional[TermGoalData] = None,
    ) -> None:
        """
        Display goal state and expected type in an mdpopups popup
        """
        # Format the goal and expected type as markdown
        markdown_content = self.format_combined_markdown(goal_data, term_goal_data)
        # Custom CSS for styling
        css = """
        .lean-infoview {
            padding: 0.5rem;
        }
        .lean-infoview h3 {
            margin-top: 0.5rem;
            margin-bottom: 0.5rem;
            color: var(--bluish);
            border-bottom: 1px solid var(--bluish);
        }
        .lean-infoview code {
            background-color: var(--background);
            padding: 0.1rem 0.3rem;
        }
        .lean-infoview .no-goals {
            color: var(--foreground);
            font-style: italic;
        }
        .lean-infoview .expected-type-header {
            font-weight: bold;
            color: var(--purplish);
            margin-top: 0.8rem;
            margin-bottom: 0.2rem;
        }
        .lean-infoview .expected-type-content {
            font-family: monospace;
            color: var(--foreground);
            margin-left: 1rem;
            margin-bottom: 0.8rem;
        }
        .lean-infoview .goal-header {
            font-weight: bold;
            color: var(--greenish);
            margin-top: 0.8rem;
            margin-bottom: 0.2rem;
        }
        .lean-infoview .hypotheses {
            color: var(--foreground);
            margin-left: 1rem;
        }
        .lean-infoview .hypothesis {
            font-family: monospace;
            margin: 0.2rem 0;
        }
        .lean-infoview .turnstile {
            font-weight: bold;
            color: var(--orangish);
            margin: 0.5rem 0;
        }
        .lean-infoview .conclusion {
            font-family: monospace;
            color: var(--foreground);
            margin-left: 1rem;
        }
        """
        # Show popup at cursor position
        mdpopups.show_popup(
            view,
            markdown_content,
            md=True,
            css=css,
            max_width=800,
            max_height=600,
            wrapper_class='lean-infoview',
            flags=sublime.COOPERATE_WITH_AUTO_COMPLETE
                | sublime.HIDE_ON_MOUSE_MOVE_AWAY
                | sublime.HIDE_ON_CHARACTER_EVENT
        )

    def format_combined_markdown(self,
        goal_data: Optional[GoalData] = None,
        term_goal_data: Optional[TermGoalData] = None,
    ) -> str:
        """
        Format goal data and expected type as markdown for mdpopups
        """
        display_nogoals = self.session.config.settings.get(SETTING_DISPLAY_NOGOALS)

        output: List[str] = []
        output.append('### Lean Infoview\n')
        # Add goals section if available
        if goal_data or display_nogoals:
            goals_md = self.format_goal_markdown(goal_data)
            if goals_md:
                output.append(goals_md)
                output.append('\n')
        # Add expected type section if available
        if term_goal_data:
            type_md = self.format_type_markdown(term_goal_data)
            if type_md:
                output.append(type_md)
                output.append('\n')
        return ''.join(output)

    def format_goal_markdown(self, goal_data: Optional[GoalData]) -> str:
        """
        Format goal data as markdown (internal helper)
        """
        if not goal_data:
            return '<div class="no-goals">No goals</div>'
        # Check if there are goals
        goals = goal_data.get('goals', [])
        if not goals:
            return '<div class="no-goals">No goals</div>'
        # Format each goal
        output: List[str] = []
        for i, goal in enumerate(goals):
            output.append(f'<div class="goal-header">Goal {i + 1}:</div>\n')
            if isinstance(goal, str): # Simple string goal
                output.append(f'```lean\n{goal}\n```\n')
            elif isinstance(goal, dict): # Structured goal with hypotheses and conclusion
                hypotheses: List[str] = goal.get('hypotheses', []) #type:ignore
                if hypotheses:
                    output.append('<div class="hypotheses">\n')
                    for h in hypotheses:
                        # Escape HTML in hypothesis
                        hyp_escaped = self._escape_html(h)
                        output.append(f'<div class="hypothesis">`{hyp_escaped}`</div>\n')
                    output.append('</div>\n')
                # Show turnstile
                output.append('<div class="turnstile">⊢</div>\n')
                # Show goal/conclusion
                conclusion: str = goal.get('conclusion', goal.get('type', 'unknown')) #type:ignore
                conclusion_escaped = self._escape_html(conclusion)
                output.append(f'<div class="conclusion">`{conclusion_escaped}`</div>\n')
            output.append('\n')
        return ''.join(output)

    def format_type_markdown(self, term_goal_data: Optional[TermGoalData]) -> str:
        """
        Format expected type as markdown
        """
        if not term_goal_data:
            return ""
        term = term_goal_data.get('goal')
        if not term:
            return ""
        output: List[str] = []
        output.append('<div class="expected-type-header">Expected Type:</div>\n')
        # Escape HTML
        if isinstance(term, str):
            output.append(f'```lean\n{term}\n```\n')
        return ''.join(output)

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))



def plugin_loaded() -> None:
    register_plugin(Lean)

def plugin_unloaded() -> None:
    unregister_plugin(Lean)



class ShowLeanInfoviewCommand(LspWindowCommand):
    """
    Command to show the Lean infoview (panel or popup depends on settings)
    """

    def run(self):
        session = self.session_by_name('lean')
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session")
            return
        display_mdpopup = session.config.settings.get(SETTING_DISPLAY_MDPOPUP)
        if not display_mdpopup:  # display output panel
            window = self.window
            # Create the panel if it doesn't exist
            panel_name = "lean_infoview"
            panel = window.find_output_panel(panel_name)
            if not panel:
                panel = window.create_output_panel(panel_name)
                panel.run_command('append', {
                    'characters': 'Lean 4 Infoview\n\nMove your cursor in a Lean file to see goal states.\n'
                })
            window.run_command("show_panel", {"panel": f"output.{panel_name}"})
        else:  # display mdpopup
            view = self.window.active_view()
            if not view:
                return
            # Trigger a goal request at current cursor position
            listener = LeanInfoviewListener(view)
            sel = view.sel()
            if len(sel) > 0:
                point = sel[0].begin()
                row, col = view.rowcol(point)
                listener.request_goal_state(view, row, col)



class HideLeanInfoviewCommand(LspWindowCommand):
    """
    Command to hide the Lean infoview popup
    """

    def run(self):
        session = self.session_by_name('lean')
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session")
            return
        display_mdpopup = session.config.settings.get(SETTING_DISPLAY_MDPOPUP)
        if not display_mdpopup:  # hide output panel
            self.window.run_command("hide_panel", {"panel": "output.lean_infoview"})
        else:  # hide mdpopup
            view = self.window.active_view()
            if view:
                mdpopups.hide_popup(view)



class LeanGoalCommand(LspTextCommand):
    """
    Command to explicitly request goal at cursor position
    Usage: `view.run_command('lean_goal')`
    """
    capability = 'textDocumentSync'
    session_name = PACKAGE_NAME

    def run(self, edit: sublime.Edit):
        view = self.view
        # Get cursor position
        sel = view.sel()
        if len(sel) == 0:
            return
        point = sel[0].begin()
        row, col = view.rowcol(point)
        # Get session
        session = self.session_by_name('lean')
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session")
            return
        if not view.file_name():
            sublime.status_message(f"{PACKAGE_NAME}: No open file path")
            return
        # Prepare request
        params = {
            'textDocument': {
                'uri': filename_to_uri(os.path.abspath(view.file_name() or ""))
            },
            'position': {
                'line': row,
                'character': col
            }
        }
        # Send request
        request: Request[GoalData] = Request("$/lean/plainGoal", params)
        session.send_request(
            request,
            lambda response: self.handle_response(session, response),
            lambda error: self.handle_error(session, error)
        )

    def handle_response(self, session: Session, response: Response[GoalData]):
        """
        Handle successful response
        """
        display_mdpopup = session.config.settings.get(SETTING_DISPLAY_MDPOPUP)
        # Display in status bar for quick feedback
        if response and response.get('goals'):
            num_goals = len(response['goals'])
            sublime.status_message(f"{PACKAGE_NAME}: {num_goals} goal(s)")
            # Also display in popup
            listener = LeanInfoviewListener(self.view)
            if not display_mdpopup:
                window = self.view.window()
                if window is None:
                    raise Exception("No view window")
                listener.display_goal_panel(window, response, None)
            else:
                listener.display_goal_popup(self.view, response, None)
        else:
            sublime.status_message(f"{PACKAGE_NAME}: No goals at cursor")

    def handle_error(self, session: Session, error: Error):
        """
        Handle error response
        """
        sublime.error_message(f"{PACKAGE_NAME} Error: {error}")
