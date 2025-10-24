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
SETTING_INFOVIEW_DISPLAY_CURRENT_GOALS = "infoview_display_current_goals"
SETTING_INFOVIEW_DISPLAY_EXPECTED_TYPE = "infoview_display_expected_type"
SETTING_INFOVIEW_DISPLAY_NOGOALS       = "infoview_display_nogoals"
SETTING_INFOVIEW_MDPOPUP               = "infoview_mdpopup"
SETTING_INFOVIEW_DELAY                 = "infoview_delay"
SETTING_INFOVIEW_SYNTAXFILE            = "infoview_syntaxfile"
SETTING_UNICODE_ENABLED                = "unicode_input_enabled"
SETTING_UNICODE_LEADER                 = "unicode_input_leader"
SETTING_UNICODE_ENDER                  = "unicode_input_ender"
SETTING_UNICODE_EAGER                  = "unicode_input_eager_replacement"
SETTING_UNICODE_CUSTOM                 = "unicode_input_custom_translations"



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
        if (session.config.name == PACKAGE_NAME):
            return session
    return None
