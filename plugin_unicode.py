import os
import json

import sublime
import sublime_plugin
from LSP.plugin import LspTextCommand, LspWindowCommand, Session
from LSP.plugin.core.typing import Optional, Any, Set, Dict, List, Tuple

from .plugin_settings import (
    PACKAGE_NAME,
    SETTINGS_FILE,
    SETTING_UNICODE_ENABLED,
    SETTING_UNICODE_LEADER,
    SETTING_UNICODE_ENDER,
    SETTING_UNICODE_EAGER,
    SETTING_UNICODE_CUSTOM,
)



class LeanUnicodeInput:
    """
    Manages unicode abbreviation translations for Lean
    """

    def __init__(self):
        self.abbreviations: Dict[str, str] = {}
        self.prefix_tree: Set[str] = set()
        self.load_abbreviations()

    def load_abbreviations(self):
        """
        Load abbreviations from the bundled JSON file and custom translations
        """
        # Load default abbreviations from package
        abbrev_path = os.path.join(sublime.packages_path(), PACKAGE_NAME, "abbreviations.json")
        try:
            with open(abbrev_path, 'r', encoding='utf-8') as f:
                self.abbreviations = json.load(f)
        except FileNotFoundError:
            # Fallback to common Lean abbreviations
            self.abbreviations = self.get_default_abbreviations()
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

    def is_complete_abbreviation(self, text: str) -> bool:
        """
        Check if text is a complete abbreviation (not a prefix of a longer one)
        """
        if text not in self.abbreviations:
            return False
        # Check if this abbreviation is a prefix of any other
        longer = text + "x"  # Any character
        return not any(abbrev.startswith(longer) for abbrev in self.abbreviations.keys())

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

    def get_default_abbreviations(self) -> Dict[str, str]:
        """
        Return common Lean unicode abbreviations as fallback
        """
        return {
            # Greek letters
            "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
            "epsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ",
            "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ",
            "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
            "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
            "chi": "χ", "psi": "ψ", "omega": "ω",

            # Capital Greek
            "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
            "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ",
            "Psi": "Ψ", "Omega": "Ω",

            # Logic symbols
            "forall": "∀", "exists": "∃",
            "not": "¬", "and": "∧", "or": "∨",
            "top": "⊤", "bot": "⊥",

            # Arrows
            "to": "→", "lr": "↔", "ud": "↕",
            "r": "→", "l": "←", "u": "↑", "d": "↓",
            "=>": "⇒", "<=": "⇐", "iff": "↔",
            "mapsto": "↦", "implies": "→",

            # Relations
            "le": "≤", "ge": "≥", "ne": "≠",
            "sim": "∼", "equiv": "≡", "approx": "≈", "cong": "≅",
            "subset": "⊂", "supset": "⊃",
            "subseteq": "⊆", "supseteq": "⊇",
            "in": "∈", "notin": "∉",
            "cap": "∩", "cup": "∪",

            # Math operators
            "times": "×", "div": "÷",
            "pm": "±", "mp": "∓",
            "cdot": "·", "circ": "∘",
            "oplus": "⊕", "ominus": "⊖", "otimes": "⊗", "odot": "⊙",

            # Special brackets
            "<>": "⟨⟩",
            "<<": "⟪", ">>": "⟫",
            "[[": "⟦", "]]": "⟧",

            # Other
            "inf": "∞", "int": "∫",
            "partial": "∂", "nabla": "∇",
            "sum": "∑", "prod": "∏",
            "sqcup": "⊔", "sqcap": "⊓",
            "emptyset": "∅",
            "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "R": "ℝ", "C": "ℂ",
        }



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
        self.pending_abbreviation = ""
        self.pending_region: Optional[sublime.Region] = None

    def on_modified(self):
        """
        Called when the view is modified
        """
        settings = sublime.load_settings(SETTINGS_FILE)
        enabled: bool = settings.get("settings", {}).get(SETTING_UNICODE_ENABLED, True) #type:ignore
        if not enabled:
            return
        leader: str = settings.get("settings", {}).get(SETTING_UNICODE_LEADER, "\\") #type:ignore
        ender: str  = settings.get("settings", {}).get(SETTING_UNICODE_ENDER, "\t") #type:ignore
        eager: bool = settings.get("settings", {}).get(SETTING_UNICODE_EAGER, False) #type:ignore
        # Get the cursor position
        sel = self.view.sel()
        if len(sel) == 0:
            return
        point = sel[0].begin()
        # Check if we're in the middle of an abbreviation
        if self.pending_region and self.pending_region.contains(point - 1):
            self.update_abbreviation(point, leader, ender, eager)
        else:
            # Check if we just typed the leader character
            if (point > 0):
                start = point - len(leader)
                prev_char = self.view.substr(sublime.Region(start, point))
                if (prev_char == leader):
                    # Start tracking abbreviation
                    self.pending_region = sublime.Region(start, point)
                    self.pending_abbreviation = ""
                    print(f"{PACKAGE_NAME}: Started abbreviation sequence at {point}")

    def update_abbreviation(self, point: int, leader: str, ender: str, eager: bool):
        """
        Update the current abbreviation being typed
        """
        if not self.pending_region:
            return
        # Get the text of the current abbreviation (without leader)
        abbrev_region = sublime.Region(self.pending_region.begin() + len(leader), point)
        abbrev_text = self.view.substr(abbrev_region)
        # Check if this is a valid abbreviation or prefix
        if _unicode_input.is_prefix(abbrev_text):
            self.pending_abbreviation = abbrev_text
            self.pending_region = sublime.Region(self.pending_region.begin(), point)
            print(f"{PACKAGE_NAME}: Typing abbreviation sequence at {point}: \"{self.pending_abbreviation}\"")
            # If eager replacement is enabled and this is a complete abbreviation
            if eager and _unicode_input.is_complete_abbreviation(abbrev_text):
                replacement = _unicode_input.get_replacement(abbrev_text)
                if replacement:
                    self.replace_abbreviation(replacement)
            if (len(abbrev_text) >= len(ender)):
                ender_text = abbrev_text[-len(ender):]
                abbrev_text = abbrev_text[:-len(ender)]
                if _unicode_input.is_complete_abbreviation(abbrev_text) and (ender_text == ender):
                    replacement = _unicode_input.get_replacement(abbrev_text)
                    if replacement:
                        self.replace_abbreviation(replacement)
        else: # Not a valid prefix, clear pending
            self.pending_abbreviation = ""
            self.pending_region = None
            #print(f"{PACKAGE_NAME}: Invalid abbreviation prefix: {abbrev_text}")

    def replace_abbreviation(self, replacement: str):
        """
        Replace the abbreviation with its unicode character
        """
        if not self.pending_region:
            return
        print(f"{PACKAGE_NAME}: Completed abbreviation sequence: \"{self.pending_abbreviation}\" → \"{replacement}\"")
        # Replace the text
        self.view.run_command('lean_replace_abbreviation', {
            'region_begin': self.pending_region.begin(),
            'region_end': self.pending_region.end(),
            'replacement': replacement
        })
        # Clear pending state
        self.pending_abbreviation = ""
        self.pending_region = None

    def on_selection_modified(self):
        """
        Called when selection (cursor) moves
        """
        # If cursor moves away from abbreviation, try to replace it
        if self.pending_region:
            sel = self.view.sel()
            if len(sel) > 0:
                point = sel[0].begin()
                if not self.pending_region.contains(point):
                    # Cursor moved away, try to replace
                    if self.pending_abbreviation:
                        replacement = _unicode_input.get_replacement(self.pending_abbreviation)
                        if replacement:
                            self.replace_abbreviation(replacement)
                    # Clear pending
                    self.pending_abbreviation = ""
                    self.pending_region = None

class LeanReplaceAbbreviationCommand(LspTextCommand):
    """
    Command to replace an abbreviation with unicode.
    Usage: `view.run_command('lean_replace_abbreviation')`
    """

    def run(self, edit: sublime.Edit, region_begin: int, region_end: int, replacement: str):
        region = sublime.Region(region_begin, region_end)
        self.view.replace(edit, region, replacement)

class LeanConvertAbbreviationCommand(LspTextCommand):
    """
    Command to manually convert current abbreviation (triggered by Tab).
    Usage: `view.run_command('lean_convert_abbreviation')`
    """

    def is_enabled(self, event: Optional[Dict] = None, point: Optional[int] = None) -> bool:
        # Only enable for Lean files
        syntax = self.view.settings().get('syntax')
        return (syntax is not None) and ('Lean' in syntax)

    def run(self, edit: sublime.Edit):
        settings = sublime.load_settings(SETTINGS_FILE)
        enabled: bool = settings.get("settings", {}).get(SETTING_UNICODE_ENABLED, True) #type:ignore
        if not enabled:
            return
        leader: str = settings.get("settings", {}).get(SETTING_UNICODE_LEADER, "\\") #type:ignore
        # Get cursor position
        sel = self.view.sel()
        if len(sel) == 0:
            return
        point = sel[0].begin()
        # Look backwards for the leader character
        line_region = self.view.line(point)
        line_start = line_region.begin()
        # Search backwards for leader
        search_start = max(line_start, point - 20)  # Look back max 20 chars
        text_before = self.view.substr(sublime.Region(search_start, point))
        leader_pos = text_before.rfind(leader)
        if leader_pos == -1:
            return
        # Get the abbreviation text
        abbrev_start = search_start + leader_pos + len(leader)
        abbrev_text = self.view.substr(sublime.Region(abbrev_start, point))
        # Find shortest matching abbreviation
        match = _unicode_input.get_shortest_match(abbrev_text)
        if match:
            replacement = _unicode_input.get_replacement(match)
            if replacement:
                # Replace from leader to end of match
                replace_end = abbrev_start + len(match)
                replace_region = sublime.Region(search_start + leader_pos, replace_end)
                self.view.replace(edit, replace_region, replacement)

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
