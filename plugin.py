
import sublime
import sublime_plugin
from LSP.plugin import LspTextCommand, Request, Session
from LSP.plugin.core.types import ClientStates
from LSP.plugin.core.typing import Optional, Any, Dict, List
from LSP.plugin.core.protocol import Error, Response



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
        import threading
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
            print(f"Lean4: Session not ready yet")
            return
        # Lean requires saved files to process
        if view.is_dirty():
            print("Lean4: File has unsaved changes, save first")
            #view.run_command('save') # Optionally auto-save
            return
        # Get file path and convert to URI
        file_path = view.file_name()
        if not file_path:
            print("Lean4: No file path")
            return
        # Convert to proper file:// URI
        import urllib.parse
        import os        
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
        print(f"Lean4: Requesting goal at {row}:{col} for {file_uri}")
        # Send custom Lean request for plain goal
        # Lean 4 uses custom LSP extensions
        request:Request[GoalData] = Request("$/lean/plainGoal", params)
        session.send_request(request, lambda response: self.on_goal_response(view, response))

    def get_lean_session(self, view:sublime.View) -> Optional[Session]:
        """
        Get the active Lean LSP session for this view
        """
        # Import the session manager
        from LSP.plugin.core.registry import windows

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
        window = view.window()
        if (window is None):
            raise Exception("No view window")
        if (response): # Display goal in output panel
            self.display_goal(window, response)
        else: # No goal at this position
            self.display_goal(window, {"goals": []})

    def display_goal(self, window:sublime.Window, goal_data:GoalData):
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
        Format goal data for display
        """
        #print(f"DEBUG: {goal_data=}")
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


class ShowLeanInfoviewCommand(sublime_plugin.WindowCommand):
    """
    Command to show the Lean infoview panel
    """

    def run(self):
        window = self.window
        # Create the panel if it doesn't exist
        panel_name = "lean_infoview"
        panel = window.find_output_panel(panel_name)
        if not panel:
            panel = window.create_output_panel(panel_name)
            panel.run_command('append', {
                'characters': 'Lean 4 Infoview\n\nMove your cursor in a Lean file to see goal states.\n'
            })
        # Show the panel
        window.run_command("show_panel", {"panel": f"output.{panel_name}"})


class HideLeanInfoviewCommand(sublime_plugin.WindowCommand):
    """
    Command to hide the Lean infoview panel
    """

    def run(self):
        self.window.run_command("hide_panel", {"panel": "output.lean_infoview"})


# Alternative: Using LspTextCommand for LSP integration
class LeanGoalCommand(LspTextCommand):
    """
    Command to explicitly request goal at cursor position
    Usage: `view.run_command('lean4_goal')`
    """

    def is_enabled(self, event:Optional[Dict], point:Optional[int]) -> bool:
        # Only enable for Lean files with an active session
        return self.has_client_with_capability('textDocumentSync')

    def run(self, edit:sublime.Edit):
        view = self.view
        # Get cursor position  
        sel = view.sel()
        if len(sel) == 0:
            return
        point = sel[0].begin()
        row, col = view.rowcol(point)
        # Get session
        session = self.session_by_name('lean4')
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
            # Also update the panel
            listener = LeanInfoviewListener(self.view)
            window = self.view.window()
            if (window is None):
                raise Exception("No view window")
            listener.display_goal(window, response)
        else:
            sublime.status_message("Lean: No goals at cursor")

    def handle_error(self, error:Error):
        """Handle error response"""
        sublime.error_message(f"Lean Error: {error}")



# import os
# import weakref
# import functools

# import sublime
# import sublime_plugin
# from LSP.plugin import Session, AbstractPlugin, ClientConfig,register_plugin, unregister_plugin
# from LSP.plugin.core.protocol import Request, Response



# class LeanClientConfig(ClientConfig):
#     def __init__(self):
#         super().__init__(
#             name="lean4",
#             command=["lean", "--server"],
#             selector="source.lean"
#         )


# def plugin_loaded() -> None:
#     register_plugin(Lean)

# def plugin_unloaded() -> None:
#     unregister_plugin(Lean)

# class Lean(AbstractPlugin):

#     @classmethod
#     def name(cls) -> str:
#         return "LSP-{}".format(cls.__name__.lower())

#     @classmethod
#     def basedir(cls) -> str:
#         return os.path.join(cls.storage_path(), cls.name())

#     @classmethod
#     def version_file(cls) -> str:
#         return os.path.join(cls.basedir(), "VERSION")

#     @classmethod
#     def platform_arch(cls) -> str:
#         return {
#             "linux_x64":    "linux-x64.tar.gz",
#             "osx_arm64":    "darwin-arm64.tar.gz",
#             "osx_x64":      "darwin-x64.tar.gz",
#             "windows_x32":  "win32-ia32.zip",
#             "windows_x64":  "win32-x64.zip",
#         }[sublime.platform() + "_" + sublime.arch()]

#     @classmethod
#     def needs_update_or_installation(cls) -> bool:
#         settings, _ = cls.configuration()
#         server_version = str(settings.get("server_version"))
#         try:
#             with open(cls.version_file(), "r") as fp:
#                 return server_version != fp.read().strip()
#         except OSError:
#             return True

#     @classmethod
#     def install_or_update(cls) -> None:
#         pass

#     @classmethod
#     def configuration(cls) -> Tuple[sublime.Settings, str]:
#         base_name = "{}.sublime-settings".format(cls.name())
#         file_path = "Packages/{}/{}".format(cls.name(), base_name)
#         return sublime.load_settings(base_name), file_path

#     @classmethod
#     def additional_variables(cls) -> Optional[Dict[str, str]]:
#         settings, _ = cls.configuration()
#         return {
#         }

#     def __init__(self, weaksession: 'weakref.ref[Session]') -> None:
#         super().__init__(weaksession)
#         self._settings_change_count = 0
#         self._queued_changes:List[Dict[str, Any]] = []
#         self.infoview_panel = None
#         self.current_state = None

#     def m___command(self, params: Any) -> None:
#         """Handles the $/command notification."""
#         if not isinstance(params, dict):
#             return print("{}: cannot handle command: expected dict, got {}".format(self.name(), type(params)))
#         command = params["command"]
#         if command == "Lean.config":
#             self._queued_changes.extend(params["data"])
#             self._settings_change_count += 1
#             current_count = self._settings_change_count
#             sublime.set_timeout_async(functools.partial(self._handle_config_commands_async, current_count), 200)
#         else:
#             sublime.error_message("LSP-lean: unrecognized command: {}".format(command))

#     def _handle_config_commands_async(self, settings_change_count: int) -> None:
#         if self._settings_change_count != settings_change_count:
#             return
#         commands, self._queued_changes = self._queued_changes, []
#         session = self.weaksession()
#         if not session:
#             return
#         base, settings = self._get_server_settings(session.window)
#         if base is None or settings is None:
#             return
#         for command in commands:
#             action = command["action"]
#             key = command["key"]
#             value = command["value"]
#             if action == "set":
#                 settings[key] = value
#             elif action == "add":
#                 values = settings.get(key)
#                 if not isinstance(values, list):
#                     values = []
#                 values.append(value)
#                 settings[key] = values
#             else:
#                 print("LSP-lean: unrecognized action:", action)
#         session.window.set_project_data(base)
#         if not session.window.project_file_name():
#             sublime.message_dialog(" ".join((
#                 "The server settings have been applied in the Window,",
#                 "but this Window is not backed by a .sublime-project.",
#                 "Click on Project > Save Project As... to store the settings."
#             )))

#     def _get_server_settings(self, window: sublime.Window) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
#         data = window.project_data()
#         if not isinstance(data, dict):
#             return None, None
#         if "settings" not in data:
#             data["settings"] = {}
#         if "LSP" not in data["settings"]:
#             data["settings"]["LSP"] = {}
#         if "LSP-lean" not in data["settings"]["LSP"]:
#             data["settings"]["LSP"]["LSP-lean"] = {}
#         if "settings" not in data["settings"]["LSP"]["LSP-lean"]:
#             data["settings"]["LSP"]["LSP-lean"]["settings"] = {}
#         return data, data["settings"]["LSP"]["LSP-lean"]["settings"]



#     def create_infoview_panel(self, window):
#         self.infoview_panel = window.create_output_panel("lean_infoview")
#         self.infoview_panel.settings().set("result_file_regex", "^(.+):([0-9]+):([0-9]+)")
#         self.infoview_panel.assign_syntax('Packages/Lean/Lean.sublime-syntax')

#     def update_infoview(self, content):
#         if self.infoview_panel:
#             self.infoview_panel.run_command('append', {'characters': content})

#     def handle_goal_state_response(self, response):
#         goals = response.get('goals', [])
#         html = self.format_goals(goals)
#         self.update_infoview(html)

#     def format_goals(self, goals):
#         if not goals:
#             return "<div>No goals</div>"

#         result = []
#         for i, goal in enumerate(goals):
#             result.append(f"""
#             <div class="goal">
#                 <div class="goal-number">Goal {i+1}</div>
#                 <div class="hypotheses">
#                     {self.format_hypotheses(goal.hypotheses)}
#                 </div>
#                 <div class="turnstile">⊢</div>
#                 <div class="conclusion">{goal.conclusion}</div>
#             </div>
#             """)
#         return ''.join(result)

#     def on_diagnostics(self, diagnostics):
#         for diag in diagnostics:
#             severity = diag.get('severity')
#             message = diag.get('message')
#             range_info = diag.get('range')
#             # Add to messages panel
#             self.messages.append({
#                 'severity': severity,
#                 'message': message,
#                 'range': range_info
#             })
#         self.update_messages_panel()




# def render_tactic_state(state):
#     html = f"""
#     <div class="tactic-state">
#         <div class="hypotheses">
#             {render_hypotheses(state.hypotheses)}
#         </div>
#         <div class="turnstile">⊢</div>
#         <div class="goals">
#             {render_goals(state.goals)}
#         </div>
#     </div>
#     """
#     return html



# class ShowLeanInfoviewCommand(sublime_plugin.WindowCommand):
#     def run(self):
#         window = self.window
#         plugin = Lean(window)
#         plugin.create_infoview_panel(window)
#         window.run_command("show_panel", {"panel": "output.lean_infoview"})

# class LeanInfoviewCommand(sublime_plugin.TextCommand):
#     def run(self, edit):
#         # Create phantom or output panel with minihtml
#         content = self.generate_infoview_html()
#         self.view.show_popup(
#             content,
#             flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
#             max_width=800,
#             max_height=600
#         )

# class LeanInfoviewListener(sublime_plugin.EventListener):

#     def on_selection_modified_async(self, view:sublime.View):
#         if view.match_selector(0, 'source.lean'):
#             pos = view.sel()[0].begin()
#             row, col = view.rowcol(pos)
#             # Request goal state at position
#             self.request_goal_state(view, row, col)

#     def request_goal_state(self, view:sublime.View, row:int, col:int):
#         # Request goal information from LSP
#         session = Session.for_view(view, 'lean4')
#         if session:
#             params = {
#                 'textDocument': {'uri': view.file_name()},
#                 'position': {'line': row, 'character': col}
#             }
#             session.send_request(
#                 Request("lean/plainGoal", params),
#                 self.on_goal_response
#             )

#     def on_goal_response(self, response):
#         if response:
#             html = self.format_goal_html(response)
#             self.update_infoview(html)
