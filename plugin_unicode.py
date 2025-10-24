import os
import json

import sublime
import sublime_plugin
from LSP.plugin import LspTextCommand, LspWindowCommand, Session
from LSP.plugin.core.typing import Optional, Set, Dict

from .plugin_utils import (
    PACKAGE_NAME,
    SETTINGS_FILE,
    SETTING_UNICODE_ENABLED,
    SETTING_UNICODE_LEADER,
    SETTING_UNICODE_ENDER,
    SETTING_UNICODE_EAGER,
    SETTING_UNICODE_CUSTOM,
    get_lean_session
)
from .plugin_unicode_abbreviations import get_default_abbreviations



class LeanUnicodeInput:
    """
    Manages unicode abbreviation translations for Lean
    """

    def __init__(self):
        self.abbreviations: Dict[str, str] = {}
        self.prefix_tree: Set[str] = set()

    def load_abbreviations(self):
        """
        Load abbreviations from the bundled JSON file and custom translations
        """
        # Load default abbreviations from package
        self.abbreviations = get_default_abbreviations()
        # Load custom translations from settings
        settings = sublime.load_settings(SETTINGS_FILE)
        custom: Dict[str, str] = settings.get("settings", {}).get(SETTING_UNICODE_CUSTOM, {}) #type:ignore
        if custom:
            self.abbreviations.update(custom)
        # Build prefix tree for efficient lookup
        self.build_prefix_tree()
        print(f"{PACKAGE_NAME}: Loaded {len(self.abbreviations)} abbreviations")

    def build_prefix_tree(self):
        """
        Build a set of all prefixes to determine if an abbreviation is complete
        """
        self.prefix_tree = set()
        for abbrev in self.abbreviations.keys():
            for i in range(1, len(abbrev) + 1):
                self.prefix_tree.add(abbrev[:i])

    def is_prefix(self, text: str) -> bool:
        """
        Check if text is a prefix of any abbreviation
        """
        return text in self.prefix_tree

    def is_complete_abbreviation(self, text: str, strict: bool = False) -> bool:
        """
        Check if text is a complete abbreviation.
        If `strict` is True, any prefix of a longer one will return False
        """
        if not strict:
            return (text in self.abbreviations)
        else:
            if (text not in self.abbreviations):
                return False
            # Check if this abbreviation is a prefix of any other
            return not any((text != abbrev and abbrev.startswith(text)) for abbrev in self.abbreviations.keys())

    def get_replacement(self, text: str) -> Optional[str]:
        """
        Get unicode replacement for abbreviation
        """
        return self.abbreviations.get(text)

    def get_shortest_match(self, text: str) -> Optional[str]:
        """
        Get the shortest complete abbreviation matching the prefix
        """
        for length in range(1, len(text) + 1):
            prefix = text[:length]
            if prefix in self.abbreviations:
                return prefix
        return None



# Global instance
_unicode_input = LeanUnicodeInput()



class LeanUnicodeListener(sublime_plugin.ViewEventListener):
    """
    Listens for unicode abbreviation input
    """

    @classmethod
    def is_applicable(cls, settings: sublime.Settings) -> bool:
        # Only activate for Lean files
        syntax = settings.get('syntax')
        return (syntax is not None) and ('Lean' in syntax) #type:ignore

    def __init__(self, view: sublime.View):
        super().__init__(view)
        self.abbrev_text: str = ""
        self.abbrev_region: Optional[sublime.Region] = None

    def on_modified(self):
        """
        Called when the view is modified
        """
        session = get_lean_session(self.view)
        if not session:
            sublime.status_message(f"{PACKAGE_NAME}: No active session")
            return
        enabled: bool = session.config.settings.get(SETTING_UNICODE_ENABLED) #type:ignore
        if not enabled:
            return
        leader: str = session.config.settings.get(SETTING_UNICODE_LEADER) #type:ignore
        ender: str  = session.config.settings.get(SETTING_UNICODE_ENDER) #type:ignore
        eager: bool = session.config.settings.get(SETTING_UNICODE_EAGER) #type:ignore
        # Get the cursor position
        sel = self.view.sel()
        if len(sel) == 0:
            return
        point = sel[0].begin()
        if self.abbrev_region and self.abbrev_region.contains(point - 1):
            # Check if we're in the middle of an abbreviation
            self.update_abbreviation(point, leader, ender, eager)
        else:
            # Check if we just typed the leader character
            if (point > 0):
                start = point - len(leader)
                prev_char = self.view.substr(sublime.Region(start, point))
                if (prev_char == leader):
                    # Start tracking abbreviation
                    self.abbrev_region = sublime.Region(start, point)
                    self.abbrev_text = ""
                    #print(f"{PACKAGE_NAME}: Started abbreviation sequence at {point}")

    def update_abbreviation(self, point: int, leader: str, ender: str, eager: bool):
        """
        Update the current abbreviation being typed
        """
        if not self.abbrev_region:
            return
        # Get the text of the current abbreviation (without leader)
        abbrev_region = sublime.Region(self.abbrev_region.begin() + len(leader), point)
        abbrev_text = self.view.substr(abbrev_region)
        #print(f"{PACKAGE_NAME}: Typing abbreviation sequence at {point}: \"{abbrev_text}\"")
        if _unicode_input.is_prefix(abbrev_text):
            self.abbrev_text = abbrev_text
            self.abbrev_region = sublime.Region(self.abbrev_region.begin(), point)
            # If eager replacement is enabled and this is a complete abbreviation
            if eager and _unicode_input.is_complete_abbreviation(abbrev_text, strict=True):
                replacement = _unicode_input.get_replacement(abbrev_text)
                if replacement:
                    self.replace_abbreviation(replacement)
                    return
            elif (self.abbrev_text[-len(ender):] == ender):
                abbrev_text_region = sublime.Region(
                    self.abbrev_region.begin() + len(leader),
                    self.abbrev_region.end() - len(ender))
                abbrev_text = self.view.substr(abbrev_text_region)
                if _unicode_input.is_complete_abbreviation(abbrev_text):
                    replacement = _unicode_input.get_replacement(abbrev_text)
                    if replacement:
                        self.replace_abbreviation(replacement)
                        return
        else: # Not a valid prefix, clear pending
            if eager and _unicode_input.is_complete_abbreviation(self.abbrev_text):
                replacement = _unicode_input.get_replacement(self.abbrev_text)
                if replacement:
                    self.replace_abbreviation(replacement)
                    return
            self.abbrev_text = ""
            self.abbrev_region = None
            sublime.error_message(f"{PACKAGE_NAME}: Invalid abbreviation: \"{abbrev_text}\"")

    def replace_abbreviation(self, replacement: str):
        """
        Replace the abbreviation with its unicode character
        """
        if not self.abbrev_region:
            return
        abbrev_text = self.abbrev_text
        abbrev_region = self.abbrev_region
        # Clear pending state
        self.abbrev_text = ""
        self.abbrev_region = None
        # Replace the text
        self.view.run_command('lean_replace_abbreviation', {
            'region_begin': abbrev_region.begin(),
            'region_end': abbrev_region.end(),
            'replacement': replacement
        })
        sublime.status_message(f"{PACKAGE_NAME}: Completed abbreviation: \"{abbrev_text}\" → \"{replacement}\"")

    def on_selection_modified(self):
        """
        Called when selection (cursor) moves
        """
        # If cursor moves away from abbreviation, try to replace it
        if self.abbrev_region:
            sel = self.view.sel()
            if len(sel) > 0:
                point = sel[0].begin()
                if not self.abbrev_region.contains(point):
                    # Cursor moved away, try to replace
                    if self.abbrev_text:
                        replacement = _unicode_input.get_replacement(self.abbrev_text)
                        if replacement:
                            self.replace_abbreviation(replacement)
                            return
                    # Clear pending
                    self.abbrev_text = ""
                    self.abbrev_region = None



class LeanReplaceAbbreviationCommand(LspTextCommand):
    """
    Command to replace an abbreviation with unicode.
    Usage: `view.run_command('lean_replace_abbreviation')`
    """

    def run(self, edit: sublime.Edit,
        region_begin: int,
        region_end: int,
        replacement: str):
        region = sublime.Region(region_begin, region_end)
        self.view.replace(edit, region, replacement)
        if ("$CURSOR" in replacement):
            index = replacement.index("$CURSOR")
            region = sublime.Region(
                region_begin + index,
                region_begin + index + len("$CURSOR"))
            self.view.erase(edit, region)
            self.view.sel().clear()
            self.view.sel().add(region.begin())



class LeanShowAbbreviationsCommand(LspWindowCommand):
    """
    Show all available unicode abbreviations
    """

    def run(self):
        # Create a new view to display abbreviations
        view = self.window.new_file()
        view.set_name("Lean Unicode Abbreviations")
        view.set_scratch(True)
        view.set_read_only(False)
        # Format abbreviations
        content = "Lean Unicode Abbreviations\n"
        content += "=" * 50 + "\n\n"
        settings = sublime.load_settings(SETTINGS_FILE)
        leader: str = settings.get("settings", {}).get(SETTING_UNICODE_LEADER, "\\") #type:ignore
        # Sort abbreviations by category (heuristic)
        abbrevs = sorted(_unicode_input.abbreviations.items())
        for abbrev, char in abbrevs:
            content += f"{leader}{abbrev:<20} → {char}\n"
        view.run_command('append', {'characters': content})
        view.set_read_only(True)



def plugin_loaded():
    """
    Called when plugin is loaded
    """
    # Reload abbreviations
    _unicode_input.load_abbreviations()
