
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



settings_display_mdpopup = True
settings_display_nogoals = False

GoalData = Any



class LeanInfoviewListener(sublime_plugin.ViewEventListener):
    """Listen to cursor movements and update infoview"""

    @classmethod
    def is_applicable(cls, settings:sublime.Settings) -> bool:
        # Only activate for Lean files
        syntax = settings.get('syntax')
        return (syntax is not None and ('Lean' in syntax))

    def is_lean_view(self, view:sublime.View):
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

    def _do_request(self, view:sublime.View):
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

    def request_goal_state(self, view:sublime.View, row:int, col:int):
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

        # Alternative: use the session's method to get URI
        # file_uri = session.config.map_client_path_to_server_uri(file_path)

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
        print(f"Lean: Requesting goal at {row}:{col} for {file_uri}")
        # Send custom Lean request for plain goal
        # Lean 4 uses custom LSP extensions
        request:Request[GoalData] = Request("$/lean/plainGoal", params)
        session.send_request(request, lambda response: self.on_goal_response(view, response))

    def get_lean_session(self, view:sublime.View) -> Optional[Session]:
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

    def on_goal_response(self, view:sublime.View, response:Response[GoalData]):
        """
        Handle goal state response from Lean server
        """
        if isinstance(response, dict) and 'error' in response:
            print(f"Lean: Error getting goal: {response['error']}")
            return
        if not settings_display_mdpopup: # output panel
            window = view.window()
            if (window is None):
                raise Exception("No view window")
            if (response): # Display goal in output panel
                self.display_goal_panel(window, response)
            elif settings_display_nogoals: # No goal at this position
                self.display_goal_panel(window, {"goals": []})
        else: # mdpopups
            if response:  # Display goal in popup
                self.display_goal_popup(view, response)
            elif settings_display_nogoals:  # No goal at this position
                self.display_goal_popup(view, {"goals": []})


    def display_goal_panel(self, window:sublime.Window, goal_data:GoalData):
        """
        Display goal state in an output panel
        """
        if not window:
            return
        # Create or get the infoview panel
        panel_name = "lean_infoview"
        panel = window.find_output_panel(panel_name)
        if not panel:
            panel = window.create_output_panel(panel_name)
            # Set syntax highlighting (optional)
            panel.set_syntax_file("Packages/Text/Plain text.tmLanguage")
        # Format the goal for display
        content = self.format_goal(goal_data)
        # Clear and update panel
        panel.run_command('select_all')
        panel.run_command('right_delete')
        panel.run_command('append', {'characters': content})
        # Show the panel
        window.run_command("show_panel", {"panel": f"output.{panel_name}"})

    def format_goal(self, goal_data:GoalData) -> str:
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
        output:List[str] = []
        for i, goal in enumerate(goals):
            output.append(f"Goal {i + 1}:")
            output.append("-" * 40)
            if isinstance(goal, str):
                output.append(goal)
                output.append("")
            # Show hypotheses
            elif ('hypotheses' in goal):
                hyps = goal.get('hypotheses', [])
                if hyps:
                    output.append("\nHypotheses:")
                    for hyp in hyps:
                        output.append(f"  {hyp}")
            # Show goal
            elif ('conclusion' in goal):
                conclusion = goal.get('conclusion', goal.get('type', 'unknown'))
                output.append(f"\n⊢ {conclusion}")
                output.append("")
        return "\n".join(output)


    def display_goal_popup(self, view:sublime.View, goal_data:GoalData):
        """
        Display goal state in an mdpopups popup
        """
        # Format the goal as markdown
        markdown_content = self.format_goal_markdown(goal_data)
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
        .lean-infoview .goal-header {
            font-weight: bold;
            color: var(--greenish);
            margin-top: 0.8rem;
            margin-bottom: 0.3rem;
        }
        .lean-infoview .hypotheses {
            margin-left: 1rem;
            color: var(--foreground);
        }
        .lean-infoview .hypothesis {
            font-family: monospace;
            margin: 0.2rem 0;
        }
        .lean-infoview .turnstile {
            color: var(--orangish);
            font-weight: bold;
            margin: 0.5rem 0;
        }
        .lean-infoview .conclusion {
            font-family: monospace;
            margin-left: 1rem;
            color: var(--foreground);
        }
        .lean-infoview .no-goals {
            color: var(--foreground);
            font-style: italic;
        }
        .lean-infoview code {
            background-color: var(--background);
            padding: 0.1rem 0.3rem;
        }
        """
        # Show popup at cursor position
        mdpopups.show_popup(
            view,
            markdown_content,
            md = True,
            css = css,
            max_width = 800,
            max_height = 600,
            wrapper_class = 'lean-infoview',
            flags = sublime.COOPERATE_WITH_AUTO_COMPLETE
                | sublime.HIDE_ON_MOUSE_MOVE_AWAY
                | sublime.HIDE_ON_CHARACTER_EVENT
        )

    def format_goal_markdown(self, goal_data:GoalData) -> str:
        """
        Format goal data as markdown for mdpopups
        """
        if not goal_data:
            return '<div class="no-goals">No goals</div>'
        # Check if there are goals
        goals = goal_data.get('goals', [])
        if not goals:
            return '<div class="no-goals">No goals</div>'
        # Format each goal
        output:List[str] = []
        output.append('### Lean Infoview\n')
        for i, goal in enumerate(goals):
            output.append(f'<div class="goal-header">Goal {i + 1}:</div>\n')
            if isinstance(goal, str):
                # Simple string goal
                output.append(f'```lean\n{goal}\n```\n')
            elif isinstance(goal, dict):
                # Structured goal with hypotheses and conclusion
                hyps = goal.get('hypotheses', [])
                if hyps:
                    output.append('<div class="hypotheses">\n')
                    for hyp in hyps:
                        # Escape HTML in hypothesis
                        hyp_escaped = self._escape_html(hyp)
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

    def _escape_html(self, text:str) -> str:
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
        if not settings_display_mdpopup: # display output panel
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
        else: # display mdpopup
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
        if not settings_display_mdpopup: # hide output panel
            self.window.run_command("hide_panel", {"panel": "output.lean_infoview"})
        else: # hide mdpopup
            view = self.window.active_view()
            if view:
                mdpopups.hide_popup(view)


# Alternative: Using LspTextCommand for LSP integration
class LeanGoalCommand(LspTextCommand):
    """
    Command to explicitly request goal at cursor position
    Usage: `view.run_command('lean_goal')`
    """

    def is_enabled(self, event:Optional[Dict] = None, point:Optional[int] = None) -> bool:
        # Only enable for Lean files with an active session
        return self.has_client_with_capability('textDocumentSync')

    def run(self, edit:sublime.Edit, event:Optional[Dict] = None):
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
        request:Request[GoalData] = Request("$/lean/plainGoal", params)
        session.send_request(
            request,
            lambda response: self.handle_response(response),
            lambda error: self.handle_error(error)
        )

    def handle_response(self, response:Response[GoalData]):
        """
        Handle successful response
        """
        # Display in status bar for quick feedback
        if response and response.get('goals'):
            num_goals = len(response['goals'])
            sublime.status_message(f"Lean: {num_goals} goal(s)")
            # Also display in popup
            listener = LeanInfoviewListener(self.view)
            if not settings_display_mdpopup:
                window = self.view.window()
                if (window is None):
                    raise Exception("No view window")
                listener.display_goal_panel(window, response)
            else:
                listener.display_goal_popup(self.view, response)
        else:
            sublime.status_message("Lean: No goals at cursor")

    def handle_error(self, error:Error):
        """Handle error response"""
        sublime.error_message(f"Lean Error: {error}")
