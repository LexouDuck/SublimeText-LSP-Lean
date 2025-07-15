from LSP.plugin import AbstractPlugin
from LSP.plugin import register_plugin
from LSP.plugin import unregister_plugin
from LSP.plugin.core.sessions import Session
from LSP.plugin.core.typing import Any, List, Dict, Optional, Tuple
from distutils.dir_util import copy_tree
import functools
import os
import shutil
import sublime
import tempfile
import urllib.request
import weakref
import zipfile
import tarfile



class Lean(AbstractPlugin):
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

    @classmethod
    def additional_variables(cls) -> Optional[Dict[str, str]]:
        settings, _ = cls.configuration()
        return {
        }

    def __init__(self, weaksession: 'weakref.ref[Session]') -> None:
        super().__init__(weaksession)
        self._settings_change_count = 0
        self._queued_changes = []  # type: List[Dict[str, Any]]

    def m___command(self, params: Any) -> None:
        """Handles the $/command notification."""
        if not isinstance(params, dict):
            return print("{}: cannot handle command: expected dict, got {}".format(self.name(), type(params)))
        command = params["command"]
        if command == "Lean.config":
            self._queued_changes.extend(params["data"])
            self._settings_change_count += 1
            current_count = self._settings_change_count
            sublime.set_timeout_async(functools.partial(self._handle_config_commands_async, current_count), 200)
        else:
            sublime.error_message("LSP-Lean: unrecognized command: {}".format(command))

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
                print("LSP-Lean: unrecognized action:", action)
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
        if "LSP-Lean" not in data["settings"]["LSP"]:
            data["settings"]["LSP"]["LSP-Lean"] = {}
        if "settings" not in data["settings"]["LSP"]["LSP-Lean"]:
            data["settings"]["LSP"]["LSP-Lean"]["settings"] = {}
        return data, data["settings"]["LSP"]["LSP-Lean"]["settings"]



def plugin_loaded() -> None:
    register_plugin(Lean)

def plugin_unloaded() -> None:
    unregister_plugin(Lean)
