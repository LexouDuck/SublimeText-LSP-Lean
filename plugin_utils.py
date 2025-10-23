import os

import sublime
from LSP.plugin import Session
from LSP.plugin.core.typing import Optional
from LSP.plugin.core.registry import windows



# Package name
PACKAGE_NAME = "LSP-lean"
# Settings file
SETTINGS_FILE = PACKAGE_NAME + ".sublime-settings"
# Settings keys
SETTING_DISPLAY_CURRENT_GOALS = "display_current_goals"
SETTING_DISPLAY_EXPECTED_TYPE = "display_expected_type"
SETTING_DISPLAY_MDPOPUP = "display_mdpopup"
SETTING_DISPLAY_NOGOALS = "display_nogoals"
SETTING_DISPLAY_SYNTAXFILE = "display_syntaxfile"
SETTING_UNICODE_ENABLED = "unicode_input_enabled"
SETTING_UNICODE_LEADER = "unicode_input_leader"
SETTING_UNICODE_ENDER = "unicode_input_ender"
SETTING_UNICODE_EAGER = "unicode_input_eager_replacement"
SETTING_UNICODE_CUSTOM = "unicode_input_custom_translations"



def get_lean_session(view: sublime.View) -> Optional[Session]:
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
