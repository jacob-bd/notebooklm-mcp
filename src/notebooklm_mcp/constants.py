"""
Constants and mappings for NotebookLM API.

This module acts as the Single Source of Truth for all API constants, code mappings,
and validation logic. It decouples data definitions from the client logic and
presentation layer.
"""

from typing import Any, Generic, TypeVar

T = TypeVar("T")


class CodeMapper:
    """
    Bidirectional mapping for API codes.
    
    Handles strict validation, normalization (case-insensitivity), and 
    human-readable error messages.
    """

    def __init__(self, mapping: dict[str, int], unknown_label: str = "unknown"):
        # Store as lower-case keys for case-insensitive lookup
        self._name_to_code: dict[str, int] = {k.lower(): v for k, v in mapping.items()}
        # Reverse mapping for code -> name lookup
        self._code_to_name: dict[int, str] = {v: k for k, v in mapping.items()}
        self._unknown_label = unknown_label
        # Keep original display names (keys) sorted for error messages
        self._display_names = sorted(list(mapping.keys()))

    def get_code(self, name: str) -> int:
        """
        Get integer code for a string name.
        
        Args:
            name: The string name (case-insensitive).
            
        Returns:
            The corresponding integer code.
            
        Raises:
            ValueError: If the name is unknown.
        """
        if not name:
            raise ValueError(f"Invalid name: '{name}'. Must be one of: {self.options_str}")
            
        code = self._name_to_code.get(name.lower())
        if code is None:
            raise ValueError(f"Unknown name '{name}'. Must be one of: {self.options_str}")
        return code

    def get_name(self, code: int | None) -> str:
        """
        Get string name for an integer code.
        
        Args:
            code: The integer code.
            
        Returns:
            The corresponding string name, or the 'unknown_label' if not found.
        """
        if code is None:
            return self._unknown_label
        return self._code_to_name.get(code, self._unknown_label)

    @property
    def options_str(self) -> str:
        """Return comma-separated list of valid options."""
        return ", ".join(self._display_names)

    @property
    def names(self) -> list[str]:
        """Return list of valid option names."""
        return self._display_names


# =============================================================================
# Ownership Constants
# =============================================================================
OWNERSHIP_MINE = 1
OWNERSHIP_SHARED = 2

# =============================================================================
# Chat Configuration
# =============================================================================
CHAT_GOAL_DEFAULT = 1
CHAT_GOAL_CUSTOM = 2
CHAT_GOAL_LEARNING_GUIDE = 3

CHAT_GOALS = CodeMapper({
    "default": CHAT_GOAL_DEFAULT,
    "custom": CHAT_GOAL_CUSTOM,
    "learning_guide": CHAT_GOAL_LEARNING_GUIDE,
})

CHAT_RESPONSE_DEFAULT = 1
CHAT_RESPONSE_LONGER = 4
CHAT_RESPONSE_SHORTER = 5

CHAT_RESPONSE_LENGTHS = CodeMapper({
    "default": CHAT_RESPONSE_DEFAULT,
    "longer": CHAT_RESPONSE_LONGER,
    "shorter": CHAT_RESPONSE_SHORTER,
})

# =============================================================================
# Research / Source Discovery
# =============================================================================
RESEARCH_SOURCE_WEB = 1
RESEARCH_SOURCE_DRIVE = 2

RESEARCH_SOURCES = CodeMapper({
    "web": RESEARCH_SOURCE_WEB,
    "drive": RESEARCH_SOURCE_DRIVE,
})

RESEARCH_MODE_FAST = 1
RESEARCH_MODE_DEEP = 5

RESEARCH_MODES = CodeMapper({
    "fast": RESEARCH_MODE_FAST,
    "deep": RESEARCH_MODE_DEEP,
})

RESULT_TYPE_WEB = 1
RESULT_TYPE_GOOGLE_DOC = 2
RESULT_TYPE_GOOGLE_SLIDES = 3
RESULT_TYPE_DEEP_REPORT = 5
RESULT_TYPE_GOOGLE_SHEETS = 8

RESULT_TYPES = CodeMapper({
    "web": RESULT_TYPE_WEB,
    "google_doc": RESULT_TYPE_GOOGLE_DOC,
    "google_slides": RESULT_TYPE_GOOGLE_SLIDES,
    "deep_report": RESULT_TYPE_DEEP_REPORT,
    "google_sheets": RESULT_TYPE_GOOGLE_SHEETS,
})

# =============================================================================
# Source Types (Notebook Content)
# =============================================================================
SOURCE_TYPE_GOOGLE_DOCS = 1
SOURCE_TYPE_GOOGLE_OTHER = 2
SOURCE_TYPE_PDF = 3
SOURCE_TYPE_PASTED_TEXT = 4
SOURCE_TYPE_WEB_PAGE = 5
SOURCE_TYPE_GENERATED_TEXT = 8
SOURCE_TYPE_YOUTUBE = 9
SOURCE_TYPE_UPLOADED_FILE = 11
SOURCE_TYPE_IMAGE = 13
SOURCE_TYPE_WORD_DOC = 14

SOURCE_TYPES = CodeMapper({
    "google_docs": SOURCE_TYPE_GOOGLE_DOCS,
    "google_slides_sheets": SOURCE_TYPE_GOOGLE_OTHER,
    "pdf": SOURCE_TYPE_PDF,
    "pasted_text": SOURCE_TYPE_PASTED_TEXT,
    "web_page": SOURCE_TYPE_WEB_PAGE,
    "generated_text": SOURCE_TYPE_GENERATED_TEXT,
    "youtube": SOURCE_TYPE_YOUTUBE,
    "uploaded_file": SOURCE_TYPE_UPLOADED_FILE,
    "image": SOURCE_TYPE_IMAGE,
    "word_doc": SOURCE_TYPE_WORD_DOC,
})

# =============================================================================
# Studio Types
# =============================================================================
STUDIO_TYPE_AUDIO = 1
STUDIO_TYPE_REPORT = 2
STUDIO_TYPE_VIDEO = 3
STUDIO_TYPE_FLASHCARDS = 4  # Also Quiz
STUDIO_TYPE_INFOGRAPHIC = 7
STUDIO_TYPE_SLIDE_DECK = 8
STUDIO_TYPE_DATA_TABLE = 9

STUDIO_TYPES = CodeMapper({
    "audio": STUDIO_TYPE_AUDIO,
    "report": STUDIO_TYPE_REPORT,
    "video": STUDIO_TYPE_VIDEO,
    "flashcards": STUDIO_TYPE_FLASHCARDS,
    "infographic": STUDIO_TYPE_INFOGRAPHIC,
    "slide_deck": STUDIO_TYPE_SLIDE_DECK,
    "data_table": STUDIO_TYPE_DATA_TABLE,
})

# =============================================================================
# Audio Overview
# =============================================================================
AUDIO_FORMAT_DEEP_DIVE = 1
AUDIO_FORMAT_BRIEF = 2
AUDIO_FORMAT_CRITIQUE = 3
AUDIO_FORMAT_DEBATE = 4

AUDIO_FORMATS = CodeMapper({
    "deep_dive": AUDIO_FORMAT_DEEP_DIVE,
    "brief": AUDIO_FORMAT_BRIEF,
    "critique": AUDIO_FORMAT_CRITIQUE,
    "debate": AUDIO_FORMAT_DEBATE,
})

AUDIO_LENGTH_SHORT = 1
AUDIO_LENGTH_DEFAULT = 2
AUDIO_LENGTH_LONG = 3

AUDIO_LENGTHS = CodeMapper({
    "short": AUDIO_LENGTH_SHORT,
    "default": AUDIO_LENGTH_DEFAULT,
    "long": AUDIO_LENGTH_LONG,
})

# =============================================================================
# Video Overview
# =============================================================================
VIDEO_FORMAT_EXPLAINER = 1
VIDEO_FORMAT_BRIEF = 2

VIDEO_FORMATS = CodeMapper({
    "explainer": VIDEO_FORMAT_EXPLAINER,
    "brief": VIDEO_FORMAT_BRIEF,
})

VIDEO_STYLE_AUTO_SELECT = 1
VIDEO_STYLE_CUSTOM = 2
VIDEO_STYLE_CLASSIC = 3
VIDEO_STYLE_WHITEBOARD = 4
VIDEO_STYLE_KAWAII = 5
VIDEO_STYLE_ANIME = 6
VIDEO_STYLE_WATERCOLOR = 7
VIDEO_STYLE_RETRO_PRINT = 8
VIDEO_STYLE_HERITAGE = 9
VIDEO_STYLE_PAPER_CRAFT = 10

VIDEO_STYLES = CodeMapper({
    "auto_select": VIDEO_STYLE_AUTO_SELECT,
    "custom": VIDEO_STYLE_CUSTOM,
    "classic": VIDEO_STYLE_CLASSIC,
    "whiteboard": VIDEO_STYLE_WHITEBOARD,
    "kawaii": VIDEO_STYLE_KAWAII,
    "anime": VIDEO_STYLE_ANIME,
    "watercolor": VIDEO_STYLE_WATERCOLOR,
    "retro_print": VIDEO_STYLE_RETRO_PRINT,
    "heritage": VIDEO_STYLE_HERITAGE,
    "paper_craft": VIDEO_STYLE_PAPER_CRAFT,
})

# =============================================================================
# Infographic
# =============================================================================
INFOGRAPHIC_ORIENTATION_LANDSCAPE = 1
INFOGRAPHIC_ORIENTATION_PORTRAIT = 2
INFOGRAPHIC_ORIENTATION_SQUARE = 3

INFOGRAPHIC_ORIENTATIONS = CodeMapper({
    "landscape": INFOGRAPHIC_ORIENTATION_LANDSCAPE,
    "portrait": INFOGRAPHIC_ORIENTATION_PORTRAIT,
    "square": INFOGRAPHIC_ORIENTATION_SQUARE,
})

INFOGRAPHIC_DETAIL_CONCISE = 1
INFOGRAPHIC_DETAIL_STANDARD = 2
INFOGRAPHIC_DETAIL_DETAILED = 3

INFOGRAPHIC_DETAILS = CodeMapper({
    "concise": INFOGRAPHIC_DETAIL_CONCISE,
    "standard": INFOGRAPHIC_DETAIL_STANDARD,
    "detailed": INFOGRAPHIC_DETAIL_DETAILED,
})

# =============================================================================
# Slide Deck
# =============================================================================
SLIDE_DECK_FORMAT_DETAILED = 1
SLIDE_DECK_FORMAT_PRESENTER = 2

SLIDE_DECK_FORMATS = CodeMapper({
    "detailed_deck": SLIDE_DECK_FORMAT_DETAILED,
    "presenter_slides": SLIDE_DECK_FORMAT_PRESENTER,
})

SLIDE_DECK_LENGTH_SHORT = 1
SLIDE_DECK_LENGTH_DEFAULT = 3

SLIDE_DECK_LENGTHS = CodeMapper({
    "short": SLIDE_DECK_LENGTH_SHORT,
    "default": SLIDE_DECK_LENGTH_DEFAULT,
})

# =============================================================================
# Flashcards / Quiz
# =============================================================================
FLASHCARD_DIFFICULTY_EASY = 1
FLASHCARD_DIFFICULTY_MEDIUM = 2
FLASHCARD_DIFFICULTY_HARD = 3

FLASHCARD_DIFFICULTIES = CodeMapper({
    "easy": FLASHCARD_DIFFICULTY_EASY,
    "medium": FLASHCARD_DIFFICULTY_MEDIUM,
    "hard": FLASHCARD_DIFFICULTY_HARD,
})

FLASHCARD_COUNT_DEFAULT = 2

# =============================================================================
# Reports
# =============================================================================
REPORT_FORMAT_BRIEFING_DOC = "Briefing Doc"
REPORT_FORMAT_STUDY_GUIDE = "Study Guide"
REPORT_FORMAT_BLOG_POST = "Blog Post"
REPORT_FORMAT_CUSTOM = "Create Your Own"
