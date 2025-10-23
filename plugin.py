import os
from typing_extensions import override
import weakref
import threading

import sublime
from LSP.plugin import Session
from LSP.plugin.core.typing import Any, Dict, List, Tuple
from LSP.plugin.core.sessions import AbstractPlugin, SessionViewProtocol, register_plugin, unregister_plugin

from .plugin_utils import PACKAGE_NAME, SETTINGS_FILE
from .plugin_infoview import LeanInfoview
from .plugin_unicode import plugin_loaded as unicode_plugin_loaded



class Lean(AbstractPlugin):
    """
    Represents the plugin itself
    """

    @classmethod
    def name(cls) -> str:
        return PACKAGE_NAME

    @classmethod
    def basedir(cls) -> str:
        return os.path.join(cls.storage_path(), cls.name())

    @classmethod
    def version_file(cls) -> str:
        return os.path.join(cls.basedir(), "VERSION")

    @classmethod
    def platform_arch(cls) -> str:
        return {
            "linux_x64":   "linux-x64.tar.gz",
            "osx_arm64":   "darwin-arm64.tar.gz",
            "osx_x64":     "darwin-x64.tar.gz",
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
        file_name = SETTINGS_FILE
        file_path = f"Packages/{PACKAGE_NAME}/{file_name}"
        return sublime.load_settings(file_name), file_path

    def __init__(self, weaksession: 'weakref.ref[Session]') -> None:
        super().__init__(weaksession)
        self.lean_infoview:LeanInfoview = LeanInfoview()
        self._settings_change_count = 0
        self._queued_changes: List[Dict[str, Any]] = []

    @staticmethod
    def is_lean_view(view: sublime.View) -> bool:
        """
        Check if view is a Lean file
        """
        syntax = view.settings().get('syntax')
        return (syntax and ('Lean' in syntax)) #type:ignore

    @override
    def on_selection_modified_async(self, session_view: SessionViewProtocol):
        """
        Called when cursor position changes
        """
        session = session_view.session # self.weaksession()
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session found")
            return
        view = session_view.view
        # Only process if this is a Lean file
        if not self.is_lean_view(view):
            return
        # Cancel any pending request
        if hasattr(self, '_pending_timeout'):
            try:
                self._pending_timeout.cancel()
            except:
                pass
        # Wait a bit to avoid too many requests while typing/moving cursor
        self._pending_timeout = threading.Timer(0.1, lambda: self._do_request(session, view))
        self._pending_timeout.start()

    def _do_request(self, session: Session, view: sublime.View):
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
        self.lean_infoview.request_goal_state(session, view, row, col)



def plugin_loaded() -> None:
    register_plugin(Lean)
    unicode_plugin_loaded()  # Initialize unicode input

def plugin_unloaded() -> None:
    unregister_plugin(Lean)
