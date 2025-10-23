import os

import sublime
import mdpopups
from LSP.plugin import LspTextCommand, LspWindowCommand, Request, Session, filename_to_uri
from LSP.plugin.core.types import ClientStates
from LSP.plugin.core.typing import Optional, Any, List
from LSP.plugin.core.protocol import Response

from .plugin_utils import (
    PACKAGE_NAME,
    #SETTINGS_FILE,
    SETTING_DISPLAY_CURRENT_GOALS,
    SETTING_DISPLAY_EXPECTED_TYPE,
    SETTING_DISPLAY_MDPOPUP,
    SETTING_DISPLAY_NOGOALS,
    SETTING_DISPLAY_SYNTAXFILE,
    get_lean_session
)



GoalData = Any
TermGoalData = Any



class LeanInfoview:

    def __init__(self) -> None:
        self._goal_data:GoalData = {}
        self._term_goal_data:TermGoalData = {}

    def request_goal_state(self, session: Session, view: sublime.View,
        row: int, col: int) -> None:
        """
        Request goal state at cursor position from Lean LSP server
        """
        # Check if session is ready
        if not session.state == ClientStates.READY:
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
        if session.config.settings.get(SETTING_DISPLAY_CURRENT_GOALS):
            #print(f"{PACKAGE_NAME}: Requesting goal at {row}:{col} for {view.file_name()}")
            request: Request[GoalData] = Request("$/lean/plainGoal", params)
            session.send_request(request, #type:ignore
                lambda response: self.on_goal_response(session, view, response),
                lambda error: sublime.error_message(f"{PACKAGE_NAME} Error: {error}"))
        # Also request expected type if enabled
        if session.config.settings.get(SETTING_DISPLAY_EXPECTED_TYPE):
            #print(f"{PACKAGE_NAME}: Requesting term at {row}:{col} for {view.file_name()}")
            term_goal_request: Request[TermGoalData] = Request("$/lean/plainTermGoal", params)
            session.send_request(term_goal_request, #type:ignore
                lambda response: self.on_term_goal_response(session, view, response),
                lambda error: sublime.error_message(f"{PACKAGE_NAME} Error: {error}"))

    def on_goal_response(self, session: Session, view: sublime.View,
        response: Response[GoalData]) -> None:
        """
        Handle goal state response from Lean server
        """
        if isinstance(response, dict) and 'error' in response:
            sublime.error_message(f"{PACKAGE_NAME}: Error getting goal: {response['error']}")
            return
        # Store the goal response for combined display
        self._goal_data[view.id()] = response
        # Display combined view
        self.display_combined_info(session, view)

    def on_term_goal_response(self, session: Session, view: sublime.View,
        response: Response[TermGoalData]) -> None:
        """
        Handle expected type (term goal) response from Lean server
        """
        if isinstance(response, dict) and 'error' in response:
            sublime.error_message(f"{PACKAGE_NAME}: Error getting expected type: {response['error']}")
            return
        # Store the term goal response for combined display
        self._term_goal_data[view.id()] = response
        # Display combined view
        self.display_combined_info(session, view)


    def display_combined_info(self, session: Session, view: sublime.View):
        """
        Display both goals and expected type together
        """
        display_goal = session.config.settings.get(SETTING_DISPLAY_CURRENT_GOALS)
        display_type = session.config.settings.get(SETTING_DISPLAY_EXPECTED_TYPE)
        display_mdpopup = session.config.settings.get(SETTING_DISPLAY_MDPOPUP)
        display_nogoals = session.config.settings.get(SETTING_DISPLAY_NOGOALS)
        # Get stored data (if available)
        view_id = view.id()
        goal_data = self._goal_data.get(view_id)
        term_goal_data = self._term_goal_data.get(view_id)
        # Decide what to display
        has_goals = display_goal and goal_data and goal_data.get('goals')
        has_types = display_type and term_goal_data and term_goal_data.get('goal')
        if not has_goals and not has_types:
            if display_nogoals:
                if display_mdpopup:
                    self.display_goal_popup(session, view, None, None)
                else:
                    window = view.window()
                    if window:
                        self.display_goal_panel(session, window, None, None)
            return
        # Display combined information
        if display_mdpopup:
            self.display_goal_popup(session, view, goal_data, term_goal_data)
        else:
            window = view.window()
            if window:
                self.display_goal_panel(session, window, goal_data, term_goal_data)


    def display_goal_panel(self, session: Session, window: sublime.Window,
        goal_data: Optional[GoalData] = None,
        term_goal_data: Optional[TermGoalData] = None):
        """
        Display goal state and expected type in an output panel
        """
        if not window:
            return
        display_nogoals = session.config.settings.get(SETTING_DISPLAY_NOGOALS)
        display_syntaxfile = session.config.settings.get(SETTING_DISPLAY_SYNTAXFILE)
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
            output.append(f"-- Goal {i + 1}:")
            if isinstance(goal, str): # Simple string goal
                output.append(goal)
            elif isinstance(goal, dict): # Structured goal with hypotheses and conclusion
                # Show hypotheses
                hypotheses: List[str] = goal.get('hypotheses', []) #type:ignore
                if hypotheses:
                    output.append("\n-- Hypotheses:")
                    for h in hypotheses:
                        output.append(f"  {h}")
                # Show goal
                conclusion: str = goal.get('conclusion', goal.get('type', 'unknown')) #type:ignore
                output.append(f"\n⊢ {conclusion}")
                output.append("")
            output.append("-" * 40)
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
        output.append("-- Expected Type:")
        output.append(term)
        output.append("-" * 40)
        return "\n".join(output)


    def display_goal_popup(self, session: Session, view: sublime.View,
        goal_data: Optional[GoalData] = None,
        term_goal_data: Optional[TermGoalData] = None,
    ) -> None:
        """
        Display goal state and expected type in an mdpopups popup
        """
        # Format the goal and expected type as markdown
        markdown_content = self.format_combined_markdown(session, goal_data, term_goal_data)
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
        mdpopups.show_popup( #type:ignore
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

    def format_combined_markdown(self, session: Session,
        goal_data: Optional[GoalData] = None,
        term_goal_data: Optional[TermGoalData] = None,
    ) -> str:
        """
        Format goal data and expected type as markdown for mdpopups
        """
        display_nogoals = session.config.settings.get(SETTING_DISPLAY_NOGOALS)

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



class ToggleLeanInfoviewCursorCommand(LspWindowCommand):
    """
    Command to show the Lean infoview (panel or popup depends on settings)
    """

    def run(self):
        session = self.session_by_name(PACKAGE_NAME)
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session")
            return
        display_mdpopup = session.config.settings.get(SETTING_DISPLAY_MDPOPUP)
        # output panel
        if not display_mdpopup:
            panel_name = "lean_infoview"
            panel = self.window.find_output_panel(panel_name)
            # show panel
            if not panel:
                panel = self.window.create_output_panel(panel_name)
                panel.run_command('append', { 'characters': 'Lean 4 Infoview\n\nMove your cursor in a Lean file to see goal states.\n' })
                self.window.run_command("show_panel", {"panel": f"output.{panel_name}"})
            # hide panel
            else:
                self.window.run_command("hide_panel", {"panel": "output.lean_infoview"})
        # markdown popup
        else:
            view = self.window.active_view()
            if not view:
                return
            # show popup
            if not mdpopups.is_popup_visible(view):
                # Trigger a goal request at current cursor position
                sel = view.sel()
                if (len(sel) > 0):
                    point = sel[0].begin()
                    row, col = view.rowcol(point)
                    infoview = LeanInfoview()
                    infoview.request_goal_state(session, view, row, col)
            # hide popup
            else:
                mdpopups.hide_popup(view)



class LeanInfoviewCommand(LspTextCommand):
    """
    Command to explicitly request goal at cursor position
    Whether an output panel or popup appears depends on settings
    Usage: `view.run_command('lean_infoview')`
    """
    capability = 'textDocumentSync'
    session_name = PACKAGE_NAME

    def run(self, edit: sublime.Edit):
        view = self.view
        # Get session
        session = get_lean_session(view)
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session")
            return
        if not view.file_name():
            sublime.status_message(f"{PACKAGE_NAME}: No open file path")
            return
        # Get cursor position
        sel = view.sel()
        if (len(sel) > 0):
            point = sel[0].begin()
            row, col = view.rowcol(point)
            infoview = LeanInfoview()
            infoview.request_goal_state(session, view, row, col)
