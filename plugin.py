import os
import threading
import urllib.parse

import sublime
import sublime_plugin
import mdpopups
from LSP.plugin import LspTextCommand, Request, Session
from LSP.plugin.core.types import ClientStates
from LSP.plugin.core.typing import Optional, Any, Dict, List
from LSP.plugin.core.protocol import Error, Response
from LSP.plugin.core.registry import windows



GoalData = Any
TermGoalData = Any



# Settings keys
SETTINGS_FILE = "LSP-lean.sublime-settings"
SETTING_DISPLAY_CURRENT_GOALS = "display_current_goals"
SETTING_DISPLAY_EXPECTED_TYPE = "display_expected_type"
SETTING_DISPLAY_MDPOPUP = "display_mdpopup"
SETTING_DISPLAY_NOGOALS = "display_nogoals"

def get_setting(key: str, default: Any = None) -> Any:
    """
    Get a setting value from the LSP-lean settings file
    """
    settings = sublime.load_settings(SETTINGS_FILE)
    return settings.get("settings", {}).get(key, default)



class LeanInfoviewListener(sublime_plugin.ViewEventListener):
    """
    Listen to cursor movements and update infoview
    """

    @classmethod
    def is_applicable(cls, settings: sublime.Settings) -> bool:
        # Only activate for Lean files
        syntax = settings.get('syntax')
        return (syntax is not None and ('Lean' in syntax))

    def is_lean_view(self, view: sublime.View):
        """
        Check if view is a Lean file
        """
        syntax = view.settings().get('syntax')
        return (syntax and ('Lean' in syntax))

    def on_selection_modified_async(self):
        """
        Called when cursor position changes
        """
        view = self.view
        # Only process if this is a Lean file
        if not self.is_lean_view(view):
            return
        # Wait a bit to avoid too many requests while typing/moving cursor
        # Cancel any pending request
        if hasattr(self, '_pending_timeout'):
            try:
                self._pending_timeout.cancel()
            except:
                pass
        # Schedule request with small delay
        self._pending_timeout = threading.Timer(0.3, lambda: self._do_request(view))
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
            print("Lean: No active session found")
            return
        # Check if session is ready
        if not session.state == ClientStates.READY:
            print(f"Lean: Session not ready yet")
            return
        # Lean requires saved files to process
        if view.is_dirty():
            print("Lean: File has unsaved changes, save first")
            #view.run_command('save') # Optionally auto-save
            return
        # Get file path and convert to URI
        file_path = view.file_name()
        if not file_path:
            print("Lean: No file path")
            return
        # Convert to proper file:// URI
        # Normalize path and convert to URI
        file_path = os.path.abspath(file_path)
        file_uri = urllib.parse.urljoin('file:', urllib.parse.quote(file_path.replace('\\', '/')))

        # Prepare LSP request parameters
        params = {
            'textDocument': {
                'uri': file_uri
            },
            'position': {
                'line': row,
                'character': col
            }
        }
        # Send custom Lean LSP request for plain goal
        if get_setting(SETTING_DISPLAY_CURRENT_GOALS, True):
            #print(f"Lean: Requesting goal at {row}:{col} for {file_uri}")
            request: Request[GoalData] = Request("$/lean/plainGoal", params)
            session.send_request(request, lambda response: self.on_goal_response(view, response))
        # Also request expected type if enabled
        if get_setting(SETTING_DISPLAY_EXPECTED_TYPE, True):
            #print(f"Lean: Requesting term at {row}:{col} for {file_uri}")
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
            print(f"Lean: Error getting goal: {response['error']}")
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
            print(f"Lean: Error getting expected type: {response['error']}")
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
        display_goal = get_setting(SETTING_DISPLAY_CURRENT_GOALS, True)
        display_type = get_setting(SETTING_DISPLAY_EXPECTED_TYPE, True)
        display_mdpopup = get_setting(SETTING_DISPLAY_MDPOPUP, True)
        display_nogoals = get_setting(SETTING_DISPLAY_NOGOALS, False)
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
        display_nogoals = get_setting(SETTING_DISPLAY_NOGOALS, False)
        # Create or get the infoview panel
        panel_name = "lean_infoview"
        panel = window.find_output_panel(panel_name)
        if not panel:
            panel = window.create_output_panel(panel_name)
            # Set syntax highlighting (optional)
            panel.set_syntax_file("Packages/Text/Plain text.tmLanguage")
        # Format the goal and expected type for display
        content_parts: List[str] = []
        # Add expected type if available
        if term_goal_data:
            type_content = self.format_type(term_goal_data)
            if type_content:
                content_parts.append(type_content)
                content_parts.append("")  # Blank line separator
        # Add goal state
        if goal_data or display_nogoals:
            goal_content = self.format_goal(goal_data)
            if goal_content:
                content_parts.append(goal_content)
        content = "\n".join(content_parts)
        # Clear and update panel
        panel.run_command('select_all')
        panel.run_command('right_delete')
        panel.run_command('append', {'characters': content})
        # Show the panel
        window.run_command("show_panel", {"panel": f"output.{panel_name}"})

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
        output.append("=" * 40)
        output.append(term)
        return "\n".join(output)

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
            if isinstance(goal, str):
                output.append(goal)
                output.append("")
            # Show hypotheses
            elif ('hypotheses' in goal):
                hypotheses = goal.get('hypotheses', [])
                if hypotheses:
                    output.append("\nHypotheses:")
                    for h in hypotheses:
                        output.append(f"  {h}")
            # Show goal
            elif ('conclusion' in goal):
                conclusion = goal.get('conclusion', goal.get('type', 'unknown'))
                output.append(f"\n⊢ {conclusion}")
                output.append("")
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
            margin-bottom: 0.3rem;
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
        display_nogoals = get_setting(SETTING_DISPLAY_NOGOALS, False)

        output: List[str] = []
        output.append('### Lean Infoview\n')
        # Add expected type section if available
        if term_goal_data:
            type_md = self.format_type_markdown(term_goal_data)
            if type_md:
                output.append(type_md)
                output.append('\n')
        # Add goals section if available
        if goal_data or display_nogoals:
            goals_md = self.format_goal_markdown(goal_data)
            if goals_md:
                output.append(goals_md)
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
            if isinstance(goal, str):
                # Simple string goal
                output.append(f'```lean\n{goal}\n```\n')
            elif isinstance(goal, dict):
                # Structured goal with hypotheses and conclusion
                hypotheses = goal.get('hypotheses', [])
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
                conclusion = goal.get('conclusion', goal.get('type', 'unknown'))
                conclusion_escaped = self._escape_html(conclusion)
                output.append(f'<div class="conclusion">`{conclusion_escaped}`</div>\n')
            output.append('\n')
        return ''.join(output)

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))



class ShowLeanInfoviewCommand(sublime_plugin.WindowCommand):
    """
    Command to show the Lean infoview (panel or popup depends on settings)
    """

    def run(self):
        display_mdpopup = get_setting(SETTING_DISPLAY_MDPOPUP, True)
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



class HideLeanInfoviewCommand(sublime_plugin.WindowCommand):
    """
    Command to hide the Lean infoview popup
    """

    def run(self):
        display_mdpopup = get_setting(SETTING_DISPLAY_MDPOPUP, True)
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

    def is_enabled(self, event: Optional[Dict] = None, point: Optional[int] = None) -> bool:
        # Only enable for Lean files with an active session
        return self.has_client_with_capability('textDocumentSync')

    def run(self, edit: sublime.Edit, event: Optional[Dict] = None):
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
            sublime.status_message("Lean: No active session")
            return
        # Prepare request
        params = {
            'textDocument': {
                'uri': f"file://{view.file_name()}" if view.file_name() else ''
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
            lambda response: self.handle_response(response),
            lambda error: self.handle_error(error)
        )

    def handle_response(self, response: Response[GoalData]):
        """
        Handle successful response
        """
        display_mdpopup = get_setting(SETTING_DISPLAY_MDPOPUP, True)
        # Display in status bar for quick feedback
        if response and response.get('goals'):
            num_goals = len(response['goals'])
            sublime.status_message(f"Lean: {num_goals} goal(s)")
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
            sublime.status_message("Lean: No goals at cursor")

    def handle_error(self, error: Error):
        """
        Handle error response
        """
        sublime.error_message(f"Lean Error: {error}")
