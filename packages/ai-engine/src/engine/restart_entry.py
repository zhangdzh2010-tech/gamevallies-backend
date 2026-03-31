from __future__ import annotations

import re

NAMED_RESTART_ENTRY_RE = re.compile(
    r"""
    (?:
        function\s+
      | (?:const|let|var)\s+
    )?
    (?:
        restart\w*
      | reset\w*
      | startgame
      | newgame
      | initgame
      | begin\w*game
      | play\w*game
      | resume\w*game
      | boot\w*game
    )
    \s*
    (?:=|\()
    """,
    re.IGNORECASE | re.VERBOSE,
)

TERMINAL_BRANCH_RESTART_RE = re.compile(
    r"""
    if\s*\([^)]*
    (?:game_over|gameover|game\ over|level_complete|levelcomplete|completed|complete|victory|win|won|clear)
    [^)]*\)
    \s*\{?
    [\s\S]{0,260}?
    (?:
        restart\w*\s*\(
      | reset\w*\s*\(
      | startgame\s*\(
      | newgame\s*\(
      | initgame\s*\(
      | begin\w*game\s*\(
      | play\w*game\s*\(
      | resume\w*game\s*\(
      | boot\w*game\s*\(
      | boot\s*\(\)\s*;?\s*init\s*\(
      | init\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

TERMINAL_READY_RESET_RE = re.compile(
    r"""
    if\s*\([^)]*
    (?:game_over|gameover|game\ over|level_complete|levelcomplete|completed|complete|victory|win|won|clear)
    [^)]*\)
    \s*\{?
    [\s\S]{0,320}?
    (?:
        (?:state|gameState|currentState|status|gameStatus)\s*=\s*['\"]?(?:ready|start)['\"]?
      | (?:gameOver|game_over|isOver|isGameOver)\s*=\s*false
    )
    [\s\S]{0,220}?
    (?:
        create\s*\(
      | init\s*\(
      | setup\w*\s*\(
      | build\w*\s*\(
      | seed\w*\s*\(
      | reset\w*\s*\(
      | restart\w*\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

RESTART_TEXT_HINT_RE = re.compile(
    r"(?:重新开始|再来一局|点击重新开始|play again|restart|tap to retry|click to retry)",
    re.IGNORECASE,
)

TEXT_HINT_TRIGGER_RE = re.compile(
    r"""
    (?:重新开始|再来一局|点击重新开始|play\ again|restart)
    [\s\S]{0,420}?
    (?:
        addEventListener
      | onclick
      | onpointerdown
      | ontouchstart
      | keydown
    )
    [\s\S]{0,420}?
    (?:
        restart\w*\s*\(
      | reset\w*\s*\(
      | startgame\s*\(
      | newgame\s*\(
      | initgame\s*\(
      | begin\w*game\s*\(
      | play\w*game\s*\(
      | resume\w*game\s*\(
      | boot\w*game\s*\(
      | boot\s*\(\)\s*;?\s*init\s*\(
      | init\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

BOUND_HANDLER_NAME_RE = re.compile(
    r"""
    (?:
        addEventListener\s*\(\s*['"]
        (?:pointerdown|touchstart|click|mousedown|mouseup|touchend|keydown)
        ['"]\s*,\s*
        ((?!function\b)(?!async\b)[A-Za-z_$][\w$]*)
      | \bon(?:pointerdown|touchstart|click|mousedown|mouseup|touchend|keydown)\s*=\s*
        ((?!function\b)(?!async\b)[A-Za-z_$][\w$]*)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

TERMINAL_MARKER_RE = re.compile(
    r"(?:game_over|gameover|game\ over|level_complete|levelcomplete|completed|complete|victory|win|won|clear)",
    re.IGNORECASE,
)

TERMINAL_STATE_RESET_RE = re.compile(
    r"""
    (?:
        (?:state|gameState|currentState|status|gameStatus)\s*=\s*['"]?(?:ready|start|playing)['"]?
      | (?:gameOver|game_over|isOver|isGameOver)\s*=\s*false
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

TERMINAL_PROGRESS_RESET_RE = re.compile(
    r"""
    (?:
        (?:
            score
          | points
          | strokes?
          | totalStrokes
          | holeStrokes
          | moves
          | lives
          | life
          | level
          | currentLevel
          | round
          | stage
          | hole
          | currentHole
          | combo
          | inventory
          | tiles
          | board
        )\s*=
      | generateLevel\s*\(
      | loadLevel\s*\(
      | loadStage\s*\(
      | loadRound\s*\(
      | loadHole\s*\(
      | createLevel\s*\(
      | buildBoard\s*\(
      | seedBoard\s*\(
      | shuffleBoard\s*\(
      | resetBoard\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_bound_handler_names(source: str) -> set[str]:
    names: set[str] = set()
    for match in BOUND_HANDLER_NAME_RE.finditer(source or ""):
        for group in match.groups():
            if group:
                names.add(group)
    return names


def _handler_has_terminal_reset_path(source: str, handler_name: str) -> bool:
    if not source or not handler_name:
        return False

    escaped_name = re.escape(handler_name)
    match = None
    for pattern in (
        rf"function\s+{escaped_name}\s*\([^)]*\)\s*\{{",
        rf"(?:const|let|var)\s+{escaped_name}\s*=\s*function\s*\([^)]*\)\s*\{{",
        rf"(?:const|let|var)\s+{escaped_name}\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{{",
    ):
        match = re.search(pattern, source, re.IGNORECASE)
        if match:
            break
    if not match:
        return False

    # We only need a local heuristic window after the handler declaration. The
    # restart branch typically sits close to the top of the handler body.
    snippet = source[match.start(): match.start() + 1800]
    if not TERMINAL_MARKER_RE.search(snippet):
        return False
    if not TERMINAL_STATE_RESET_RE.search(snippet):
        return False
    if not TERMINAL_PROGRESS_RESET_RE.search(snippet):
        return False
    return True


def has_restart_entry(code: str) -> bool:
    source = code or ""
    if not source:
        return False

    if NAMED_RESTART_ENTRY_RE.search(source):
        return True

    if TERMINAL_BRANCH_RESTART_RE.search(source):
        return True

    if TERMINAL_READY_RESET_RE.search(source):
        return True

    if RESTART_TEXT_HINT_RE.search(source) and TEXT_HINT_TRIGGER_RE.search(source):
        return True

    for handler_name in _extract_bound_handler_names(source):
        if _handler_has_terminal_reset_path(source, handler_name):
            return True

    return False
